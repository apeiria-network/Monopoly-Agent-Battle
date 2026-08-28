"""Agent-scoped conversation memory for Stage 4C prompt composition.

An ``AgentConversation`` records the full time-ordered stream of events,
decisions and validation errors relevant to one Agent. Segment 3
(history-before-current-turn) is rendered once per Agent turn and cached and
only carries game events — errors are per-turn and are scrubbed at the turn
boundary. Segment 4 replays the current turn's entries verbatim including
error records, so the AI can see its earlier mistakes plus the corrective
feedback throughout the turn.

The conversation itself is a pure data structure: it does not perform I/O,
does not call the LLM, and is fully replayable given the same event / decision
/ error input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from monopoly_agent_battle.context.broadcast import render_event
from monopoly_agent_battle.context.token_guard import (
    ContextWarning,
    estimate_tokens,
    truncate_events_to_budget,
)
from monopoly_agent_battle.domain.models import GameEvent


@dataclass(frozen=True, slots=True)
class EventEntry:
    """One engine event observed during an Agent's action turn.

    ``complete_round`` is the ``GameState.complete_rounds`` snapshot when the
    event fires; used to prefix ``[第N轮]`` in broadcast rendering. Defaults
    to 0 so unit tests that construct fixtures without a live engine don't
    have to thread the counter through — production runners always pass the
    real value from ``engine.state.complete_rounds``.
    """

    event: GameEvent
    complete_round: int = 0


@dataclass(frozen=True, slots=True)
class DecisionEntry:
    """One decision this Agent produced during the current action turn.

    ``question_summary`` is a compressed replay of segments 8 (question only)
    used when segment 4 re-shows this decision to later turns: the AI already
    committed to an answer, so re-listing candidates + output guide would just
    waste tokens. ``assistant_reply`` is the JSON that will be replayed as the
    assistant message in segment 4. For a normal success, this is the LLM's
    own reply verbatim; for a fallback triggered by exhausted retries, the
    runner substitutes a synthesized default-option JSON whose ``reason``
    explains the fallback.
    """

    decision_id: str
    question_summary: str
    assistant_reply: str


@dataclass(frozen=True, slots=True)
class InternalDecisionEntry:
    """A private decision-related message emitted by another Court AI.

    The receiving role's conversation stores the source role's raw response
    separately from its trusted institutional attribution.  During prompt
    composition the response is converted into JSON and the system-owned
    ``decision_maker`` and ``content_type`` fields are overlaid, so an LLM
    cannot impersonate a role by supplying either field itself.

    ``internal_decision_id`` is a court-workflow-owned idempotency key.  It
    permits a workflow to retry a downstream role without duplicating an
    already-delivered upstream opinion in that role's current-turn history.
    """

    internal_decision_id: str
    decision_id: str
    question_summary: str
    decision_maker: str
    content_type: str
    raw_content: str


@dataclass(frozen=True, slots=True)
class ContextEntry:
    """One plain user-context instruction or broadcast within the current turn."""

    content: str


@dataclass(frozen=True, slots=True)
class ErrorEntry:
    """A validation-failed AI reply within the current action turn.

    ``decision_id`` + ``question_summary`` let the composer emit a single
    ``user(question)`` message before the first assistant chunk of a decision,
    so the AI's ``assistant(bad_reply)`` always follows a real user prompt.
    Subsequent errors on the same ``decision_id`` reuse that user message
    (they merely add the feedback text to the next user chunk).
    """

    decision_id: str
    question_summary: str
    bad_reply: str
    feedback_text: str


TurnEntry = EventEntry | DecisionEntry | InternalDecisionEntry | ContextEntry | ErrorEntry

_SEGMENT3_TOKEN_CAP = 500


@dataclass(slots=True)
class TurnRecord:
    """A single Agent action turn's time-ordered entries."""

    turn_num: int
    entries: list[TurnEntry] = field(default_factory=lambda: [])


@dataclass(slots=True)
class AgentConversation:
    """Per-Agent conversation memory that feeds ``compose_prompt``.

    Attributes prefixed with ``_`` are internal state. External callers should
    use the mutation methods; ``compose_prompt`` reads the public fields.
    """

    agent_id: str
    window_turns: int = 1
    completed_turns: list[TurnRecord] = field(default_factory=lambda: [])
    current_turn: TurnRecord | None = None
    _segment3_cache: tuple[str, ...] | None = None
    _segment3_cache_for_turn: int | None = None
    _segment3_warning: ContextWarning | None = None

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------

    def start_turn(self, turn_num: int) -> None:
        """Begin a new Agent action turn and rebuild its fixed segment-3 cache.

        Segment 3 is independently capped at 500 estimated tokens. It is
        rebuilt only at this action-turn boundary, so subsequent decisions in
        the same turn observe an identical history cache.
        """
        if self.current_turn is not None:
            self.completed_turns.append(self.current_turn)
        self.current_turn = TurnRecord(turn_num=turn_num, entries=[])
        self._rebuild_segment3_cache()

    def append_event(self, event: GameEvent, complete_round: int = 0) -> None:
        """Record an engine event under the current action turn.

        Events that arrive before ``start_turn`` (e.g. the game's very first
        ``turn_started`` before any Agent turn has begun) are silently ignored
        — they cannot be attributed to any Agent turn yet.
        """
        if self.current_turn is None:
            return
        self.current_turn.entries.append(EventEntry(event=event, complete_round=complete_round))

    def append_decision(
        self,
        *,
        decision_id: str,
        question_summary: str,
        assistant_reply: str,
        allow_duplicate_decision_id: bool = False,
    ) -> None:
        """Record a completed decision reply.

        External decisions remain idempotent by default. A court workflow may
        explicitly append several replies under the same external decision ID
        when one role speaks in multiple rounds; keeping the shared ID lets the
        composer render the historical question only once.
        """
        if self.current_turn is None:
            raise RuntimeError(
                "append_decision called before start_turn; conversation has no active turn"
            )
        if not allow_duplicate_decision_id and any(
            isinstance(entry, DecisionEntry) and entry.decision_id == decision_id
            for turn in (*self.completed_turns, self.current_turn)
            for entry in turn.entries
        ):
            return
        self.current_turn.entries.append(
            DecisionEntry(
                decision_id=decision_id,
                question_summary=question_summary,
                assistant_reply=assistant_reply,
            )
        )

    def append_internal_decision(
        self,
        *,
        internal_decision_id: str,
        decision_id: str,
        question_summary: str,
        decision_maker: str,
        content_type: str,
        raw_content: str,
    ) -> bool:
        """Deliver one trusted private Court-AI message to this conversation.

        Delivery is idempotent within the active turn and completed history:
        retrying the same workflow message does not duplicate it.  The
        receiving conversation is the privacy boundary; callers must invoke
        this method only for roles authorized to see the message.
        """
        if self.current_turn is None:
            return False
        if any(
            isinstance(entry, InternalDecisionEntry)
            and entry.internal_decision_id == internal_decision_id
            for turn in (*self.completed_turns, self.current_turn)
            for entry in turn.entries
        ):
            return False
        self.current_turn.entries.append(
            InternalDecisionEntry(
                internal_decision_id=internal_decision_id,
                decision_id=decision_id,
                question_summary=question_summary,
                decision_maker=decision_maker,
                content_type=content_type,
                raw_content=raw_content,
            )
        )
        return True

    def insert_internal_decision_before_decision(
        self,
        *,
        internal_decision_id: str,
        decision_id: str,
        question_summary: str,
        decision_maker: str,
        content_type: str,
        raw_content: str,
    ) -> bool:
        """Insert trusted court context immediately before this role's decision.

        Court workflows use this when the external runner has already persisted
        the role's final ``DecisionEntry`` but filtered upstream discussion must
        precede that assistant reply during later same-turn replay.
        """
        if self.current_turn is None:
            return False
        if any(
            isinstance(entry, InternalDecisionEntry)
            and entry.internal_decision_id == internal_decision_id
            for turn in (*self.completed_turns, self.current_turn)
            for entry in turn.entries
        ):
            return False
        insertion_index = next(
            (
                index
                for index, entry in enumerate(self.current_turn.entries)
                if isinstance(entry, DecisionEntry) and entry.decision_id == decision_id
            ),
            len(self.current_turn.entries),
        )
        while insertion_index < len(self.current_turn.entries):
            entry = self.current_turn.entries[insertion_index]
            if not isinstance(entry, InternalDecisionEntry) or entry.decision_id != decision_id:
                break
            insertion_index += 1
        self.current_turn.entries.insert(
            insertion_index,
            InternalDecisionEntry(
                internal_decision_id=internal_decision_id,
                decision_id=decision_id,
                question_summary=question_summary,
                decision_maker=decision_maker,
                content_type=content_type,
                raw_content=raw_content,
            ),
        )
        return True

    def append_context(self, content: str) -> None:
        """Append plain user context at the current point in segment-five history."""
        if self.current_turn is None:
            return
        self.current_turn.entries.append(ContextEntry(content=content))

    def append_error(
        self,
        *,
        decision_id: str,
        question_summary: str,
        bad_reply: str,
        feedback_text: str,
    ) -> None:
        """Record a validation-failed AI reply for in-turn replay.

        The error is kept in the current turn's entries and surfaces in
        segment 4 for the remainder of this turn. When the next
        ``start_turn`` fires, the entire turn (including this error) migrates
        to ``completed_turns`` but segment 3 rendering skips ``ErrorEntry``
        items, so the mistake never leaks into later turns' history.
        """
        if self.current_turn is None:
            return
        self.current_turn.entries.append(
            ErrorEntry(
                decision_id=decision_id,
                question_summary=question_summary,
                bad_reply=bad_reply,
                feedback_text=feedback_text,
            )
        )

    # ------------------------------------------------------------------
    # Segment 3 rendering (public reads used by composer)
    # ------------------------------------------------------------------

    @property
    def segment3_sentences(self) -> tuple[str, ...]:
        """Return the cached, budget-truncated segment-3 rendered sentences."""
        return self._segment3_cache or ()

    @property
    def segment3_warning(self) -> ContextWarning | None:
        return self._segment3_warning

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rebuild_segment3_cache(self) -> None:
        rendered: list[str] = []
        for turn in self.completed_turns:
            for entry in turn.entries:
                if not isinstance(entry, EventEntry):
                    continue
                sentence = render_event(entry.event, self.agent_id)
                if sentence is not None:
                    rendered.append(f"[第{entry.complete_round}轮] {sentence}")
        kept, warning = truncate_events_to_budget(rendered, _SEGMENT3_TOKEN_CAP)
        if estimate_tokens("\n".join(kept)) > _SEGMENT3_TOKEN_CAP:
            raise AssertionError("segment 3 cache exceeds its fixed token cap")
        self._segment3_cache = kept
        self._segment3_warning = warning
        self._segment3_cache_for_turn = (
            self.current_turn.turn_num if self.current_turn is not None else None
        )
