"""Flat-ensemble controller: four independent baseline sessions + weighted vote.

The ``flat_ensemble`` agent (Courts-Battle-config-details §7.1.1) is the
"organized structure removed" control group.  It is composed of four LLM
sessions, each running the exact same baseline-level prompt and context
management system as the single-LLM baseline, called in parallel with **no
mutual visibility** — including historical outputs.  Their four replies are
aggregated by a deterministic weighted vote: the backbone session
(``leader``) carries weight 1.5 and the other three (``member_1/2/3``) carry
1.0 each.  The winning option becomes the engine-facing decision.

Each session records only its own reply in its own conversation (the runner's
final-decision auto-append to the first role's conversation is deduplicated
away), so no AI ever sees another AI's output or the vote result.  The final
decision's ``reason`` is a system-generated vote summary; each session's own
reason is preserved in the court trace for audit.  No rewriting, no
cross-session delivery, and no extra LLM call beyond the four parallel ones.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import cast

from monopoly_agent_battle.config.models import ModelProfile
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.context.token_guard import ContextWarning
from monopoly_agent_battle.context.validation_feedback import build_feedback
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.prompts import render_decision_question
from monopoly_agent_battle.decision.protocol import default_option_json, parse_and_validate
from monopoly_agent_battle.llm.protocol import LLMCallError, LLMClient, LLMRequest

MEMBER_1 = "member_1"
MEMBER_2 = "member_2"
MEMBER_3 = "member_3"
LEADER = "leader"  # backbone / base-model seat, weight 1.5
MEMBERS = (MEMBER_1, MEMBER_2, MEMBER_3, LEADER)
_WEIGHTS = {MEMBER_1: 1.0, MEMBER_2: 1.0, MEMBER_3: 1.0, LEADER: 1.5}
_ROLE_LABELS = {
    MEMBER_1: "成员一",
    MEMBER_2: "成员二",
    MEMBER_3: "成员三",
    LEADER: "基干AI",
}
_ADVICE = "advice"
_VALIDATION_FALLBACK_REASON = "系统采用默认合法选项。"
_CONNECTION_FALLBACK_REASON = "{label}重连次数耗尽，无法做出有效回复。"
_MAX_REASON_CHARS = 400


@dataclass(frozen=True, slots=True)
class FlatEnsembleCallTrace:
    """One private, serializable LLM role invocation in a flat-ensemble vote."""

    decision_id: str
    role: str
    caller_role: str
    outcome: str
    content: str | None = None
    error: str | None = None
    content_type: str | None = None
    decision_maker: str | None = None


def _truncate(reason: str) -> str:
    return reason[:_MAX_REASON_CHARS]


def _signature(raw: str) -> tuple[str, str]:
    """Return the option id and the sorted target JSON of one normalized reply."""
    document = cast(dict[str, object], json.loads(raw))
    selected = cast(dict[str, object], document["selected_option"])
    option = str(selected["option"])
    target = {key: selected[key] for key in selected if key != "option"}
    return option, json.dumps(target, sort_keys=True, ensure_ascii=False)


def weighted_vote(advice: dict[str, str]) -> dict[str, object]:
    """Return the weighted-vote result over the four sessions' advice.

    ``option`` and the complete ``target`` must match exactly to count as the
    same choice.  Weights 1.5 (backbone) / 1.0 (others) never produce a true
    tie, so the winner is always unique and deterministic.
    """
    totals: dict[str, float] = {}
    supporters: dict[str, list[str]] = {}
    for role in MEMBERS:
        raw = advice[role]
        sig = json.dumps(list(_signature(raw)), ensure_ascii=False)
        totals[sig] = totals.get(sig, 0.0) + _WEIGHTS[role]
        supporters.setdefault(sig, []).append(role)
    winner_key = max(totals, key=lambda key: (totals[key], key))
    option, target_json = json.loads(winner_key)
    return {
        "weights": dict(_WEIGHTS),
        "totals": totals,
        "supporters": supporters,
        "selected_option": {"option": option, **json.loads(target_json)},
    }


def _vote_reason(vote: dict[str, object]) -> str:
    """Render a deterministic, system-authored summary of the weighted vote."""
    totals = cast(dict[str, float], vote["totals"])
    supporters = cast(dict[str, list[str]], vote["supporters"])
    selected = cast(dict[str, object], vote["selected_option"])
    winner_key = max(totals, key=lambda key: (totals[key], key))
    winner_total = totals[winner_key]
    winner_supporters = supporters[winner_key]
    labels = "、".join(_ROLE_LABELS[role] for role in winner_supporters)
    return (
        f"系统加权投票裁定：选项 {selected['option']} 胜出"
        f"（{labels}支持，权重合计 {winner_total:g}；"
        f"基干AI权重1.5，其余各1.0）。"
    )


class FlatEnsembleAgent:
    """Four independent baseline sessions aggregated by a weighted vote."""

    uses_llm = True

    def __init__(
        self,
        *,
        player_id: str,
        member_1_client: LLMClient,
        member_1_profile: ModelProfile,
        member_2_client: LLMClient,
        member_2_profile: ModelProfile,
        member_3_client: LLMClient,
        member_3_profile: ModelProfile,
        leader_client: LLMClient,
        leader_profile: ModelProfile,
        conversations: dict[str, AgentConversation],
        validation_retries: int = 2,
        max_connection_retries: int = 2,
    ) -> None:
        self._player_id = player_id
        self._clients = {
            MEMBER_1: member_1_client,
            MEMBER_2: member_2_client,
            MEMBER_3: member_3_client,
            LEADER: leader_client,
        }
        self._profiles = {
            MEMBER_1: member_1_profile,
            MEMBER_2: member_2_profile,
            MEMBER_3: member_3_profile,
            LEADER: leader_profile,
        }
        self._conversations = conversations
        self._validation_retries = validation_retries
        self._max_connection_retries = max_connection_retries
        self._connection_failures = {role: 0 for role in MEMBERS}
        self._decision_id: str | None = None
        self._advice: dict[str, str] = {}
        self._vote: dict[str, object] | None = None
        self._final: str | None = None
        self._trace: list[FlatEnsembleCallTrace] = []
        self._last_llm_call_count = 0
        self._last_warning: ContextWarning | None = None

    @property
    def player_id(self) -> str:
        return self._player_id

    @property
    def conversation(self) -> AgentConversation:
        """Return the backbone (``leader``) session conversation."""
        return self._conversations[LEADER]

    @property
    def role_conversations(self) -> dict[str, AgentConversation]:
        return dict(self._conversations)

    @property
    def last_context_warning(self) -> ContextWarning | None:
        return self._last_warning

    @property
    def last_llm_call_count(self) -> int:
        return self._last_llm_call_count

    def court_trace(self) -> dict[str, object]:
        """Return the private audit record for the latest decision."""
        return {
            "court": "flat_ensemble",
            "decision_id": self._decision_id,
            "calls": [asdict(item) for item in self._trace],
            "vote": self._vote,
        }

    def court_calls(self) -> list[dict[str, object]]:
        """Return the raw role-call trace entries for tests and reports."""
        return [asdict(item) for item in self._trace]

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        self._prepare(request)
        self._last_llm_call_count = 0
        pending = [role for role in MEMBERS if role not in self._advice]
        if pending:
            with ThreadPoolExecutor(max_workers=len(pending)) as executor:
                futures = {role: executor.submit(self._advise, role, request) for role in pending}
                for role in pending:
                    futures[role].result()
            self._reorder_traces()
        if self._vote is None:
            self._vote = weighted_vote(self._advice)
        self._final = self._compose_final(self._vote)
        return self._final

    def _prepare(self, request: DecisionRequest) -> None:
        if self._decision_id == request.decision_id:
            return
        self._decision_id = request.decision_id
        self._advice = {}
        self._vote = None
        self._final = None
        self._connection_failures = {role: 0 for role in MEMBERS}
        self._trace = []
        self._last_warning = None
        for conversation in self._conversations.values():
            if conversation.current_turn is None:
                conversation.start_turn(1)

    def _advise(self, role: str, request: DecisionRequest) -> None:
        """Obtain one session's final advice, with retries and fallbacks."""
        try:
            raw = self._call(role, request)
            validation = parse_and_validate(raw, request)
            attempts = 0
            while not validation.valid and attempts < self._validation_retries:
                self._record_validation(
                    role,
                    request,
                    raw,
                    validation.error or "回复非法",
                    build_feedback(validation, request),
                )
                raw = self._call(role, request)
                validation = parse_and_validate(raw, request)
                attempts += 1
            if validation.valid:
                assert validation.option is not None and validation.response is not None
                selected_option: dict[str, object] = {"option": validation.option.option_id}
                if validation.response.target is not None:
                    selected_option["target"] = validation.response.target
                reason = _truncate(validation.response.reason)
                outcome = "success"
            else:
                self._record_validation(
                    role,
                    request,
                    raw,
                    validation.error or "回复非法",
                    build_feedback(validation, request),
                )
                default = next(option for option in request.options if option.is_default)
                selected_option = default_option_json(default)
                reason = _VALIDATION_FALLBACK_REASON
                outcome = "advice_normalized"
        except (ConnectionError, LLMCallError) as error:
            selected_option, reason, outcome = self._connection_fallback(role, request, error)
        normalized = json.dumps(
            {"selected_option": selected_option, "reason": reason}, ensure_ascii=False
        )
        self._advice[role] = normalized
        self._append_own(role, request, normalized)
        self._trace.append(
            FlatEnsembleCallTrace(
                request.decision_id,
                role,
                f"{self._player_id}.{role}",
                outcome,
                normalized,
                content_type=_ADVICE,
                decision_maker=role,
            )
        )

    def _call(self, role: str, request: DecisionRequest) -> str:
        profile = self._profiles[role]
        caller = f"{self._player_id}.{role}"
        messages, warning = compose_prompt(self._conversations[role], request)
        self._last_warning = warning
        try:
            response = self._clients[role].complete(
                LLMRequest(
                    messages=messages,
                    model=profile.model,
                    caller_role=caller,
                    seed=profile.seed,
                    temperature=profile.temperature,
                    max_tokens=profile.max_tokens,
                    timeout_seconds=profile.timeout_seconds,
                    decision_request=request,
                )
            )
        except (ConnectionError, LLMCallError) as error:
            self._last_llm_call_count += 1
            self._trace.append(
                FlatEnsembleCallTrace(
                    request.decision_id,
                    role,
                    caller,
                    "connection_error" if isinstance(error, ConnectionError) else "call_error",
                    error=str(error),
                )
            )
            raise
        self._last_llm_call_count += 1
        return response.content

    def _connection_fallback(
        self, role: str, request: DecisionRequest, error: Exception
    ) -> tuple[dict[str, object], str, str]:
        """Re-raise until the role's reconnect budget is exhausted, then advise."""
        self._connection_failures[role] += 1
        if self._connection_failures[role] <= self._max_connection_retries:
            raise error
        default = next(option for option in request.options if option.is_default)
        selected_option = default_option_json(default)
        reason = _CONNECTION_FALLBACK_REASON.format(label=_ROLE_LABELS[role])
        return selected_option, reason, "connection_fallback"

    def _compose_final(self, vote: dict[str, object]) -> str:
        return json.dumps(
            {"selected_option": vote["selected_option"], "reason": _vote_reason(vote)},
            ensure_ascii=False,
        )

    def _append_own(self, role: str, request: DecisionRequest, raw: str) -> None:
        self._conversations[role].append_decision(
            decision_id=request.decision_id,
            question_summary=render_decision_question(request),
            assistant_reply=raw,
        )

    def _record_validation(
        self,
        role: str,
        request: DecisionRequest,
        raw: str,
        error: str,
        feedback: str,
    ) -> None:
        self._conversations[role].append_error(
            decision_id=request.decision_id,
            question_summary=render_decision_question(request),
            bad_reply=raw,
            feedback_text=feedback,
        )
        self._trace.append(
            FlatEnsembleCallTrace(
                request.decision_id,
                role,
                f"{self._player_id}.{role}",
                "validation_error",
                raw,
                error=error,
                content_type=_ADVICE,
            )
        )

    def _reorder_traces(self) -> None:
        """Order trace entries by role for deterministic audits."""
        ordered: list[FlatEnsembleCallTrace] = []
        for role in MEMBERS:
            ordered.extend(item for item in self._trace if item.role == role)
        self._trace = ordered
