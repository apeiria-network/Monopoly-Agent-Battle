"""Agent-scoped conversation memory for Stage 4C prompt composition.

An ``AgentConversation`` records the full time-ordered stream of events and
decisions relevant to one Agent (Baseline: one player; Stage 5 court: each AI
inside the court holds one of these). Segment 3 (history-before-current-turn)
is rendered once per Agent turn and cached; the Stage 4B broadcaster is used
to produce viewer-scoped sentences.

The conversation itself is a pure data structure: it does not perform I/O,
does not call the LLM, and is fully replayable given the same event/decision
input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from monopoly_agent_battle.context.broadcast import render_event
from monopoly_agent_battle.context.token_guard import (
    ContextWarning,
    truncate_events_to_budget,
)
from monopoly_agent_battle.domain.models import GameEvent


@dataclass(frozen=True, slots=True)
class EventEntry:
    """One engine event observed during an Agent's action turn."""

    event: GameEvent


@dataclass(frozen=True, slots=True)
class DecisionEntry:
    """One decision this Agent produced during the current action turn.

    ``user_snapshot`` is the exact text that was sent as segments 8+9+10 when
    the request was posed (segment 14 replay uses this). ``assistant_reply``
    is the LLM's original JSON response (segment 11 replay uses this).
    """

    decision_id: str
    user_snapshot: str
    assistant_reply: str


TurnEntry = EventEntry | DecisionEntry


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
    _pending_bad_reply: str | None = None
    _pending_feedback: str | None = None

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------

    def start_turn(self, turn_num: int, *, segment3_budget_tokens: int) -> None:
        """Begin a new Agent action turn.

        Moves the prior ``current_turn`` (if any) into ``completed_turns`` and
        rebuilds the segment-3 render cache from all completed turns' events.
        The budget is applied once here; the cache is reused for every
        composed prompt within this turn until ``start_turn`` is called again.
        """
        if self.current_turn is not None:
            self.completed_turns.append(self.current_turn)
        self.current_turn = TurnRecord(turn_num=turn_num, entries=[])
        self._rebuild_segment3_cache(segment3_budget_tokens)

    def append_event(self, event: GameEvent) -> None:
        """Record an engine event under the current action turn.

        Events that arrive before ``start_turn`` (e.g. the game's very first
        ``turn_started`` before any Agent turn has begun) are silently ignored
        — they cannot be attributed to any Agent turn yet.
        """
        if self.current_turn is None:
            return
        self.current_turn.entries.append(EventEntry(event=event))

    def append_decision(
        self, *, decision_id: str, user_snapshot: str, assistant_reply: str
    ) -> None:
        """Record a completed decision (both request snapshot and reply)."""
        if self.current_turn is None:
            raise RuntimeError(
                "append_decision called before start_turn; conversation has no active turn"
            )
        self.current_turn.entries.append(
            DecisionEntry(
                decision_id=decision_id,
                user_snapshot=user_snapshot,
                assistant_reply=assistant_reply,
            )
        )

    # ------------------------------------------------------------------
    # Validation feedback lifecycle
    # ------------------------------------------------------------------

    def set_pending_feedback(self, *, bad_reply: str, feedback: str) -> None:
        """Attach a transient (assistant_bad_reply, user_feedback) pair.

        The composer will append these to segment 4 before the current
        request. Clear them once the retry succeeds or the runner falls back.
        """
        self._pending_bad_reply = bad_reply
        self._pending_feedback = feedback

    def clear_pending_feedback(self) -> None:
        """Drop any transient validation-failure feedback."""
        self._pending_bad_reply = None
        self._pending_feedback = None

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

    @property
    def pending_bad_reply(self) -> str | None:
        return self._pending_bad_reply

    @property
    def pending_feedback(self) -> str | None:
        return self._pending_feedback

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rebuild_segment3_cache(self, budget_tokens: int) -> None:
        rendered: list[str] = []
        for turn in self.completed_turns:
            for entry in turn.entries:
                if not isinstance(entry, EventEntry):
                    continue
                sentence = render_event(entry.event, self.agent_id)
                if sentence is not None:
                    rendered.append(sentence)
        kept, warning = truncate_events_to_budget(rendered, budget_tokens)
        self._segment3_cache = kept
        self._segment3_warning = warning
        self._segment3_cache_for_turn = (
            self.current_turn.turn_num if self.current_turn is not None else None
        )
