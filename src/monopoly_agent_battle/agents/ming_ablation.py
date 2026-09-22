"""Ming ablation (去汇总) cabinet workflow: drafts + vote revealed to the emperor.

Identical to ``ming.py`` up to the weighted vote; the chief's summary advice
step is removed. Instead the emperor receives one trusted "cabinet_opinions"
block containing the three FINAL drafts (each carrying system-appended
``decision_maker``/``content_type`` fields, opinions first) followed by the
weighted-vote tally (chief 1.5, secretaries 1.0 each). On a three-way split
(1.5:1.0:1.0) the weighted plurality is the chief's opinion and the tally says
so explicitly. Past decisions' blocks stay in the emperor's conversation
history, mirroring how the old advice JSON persisted.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from monopoly_agent_battle.config.models import ModelProfile
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.context.token_guard import ContextWarning
from monopoly_agent_battle.context.validation_feedback import build_feedback
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.prompts import render_decision_question
from monopoly_agent_battle.decision.protocol import default_option_json, parse_and_validate
from monopoly_agent_battle.llm.protocol import LLMCallError, LLMClient, LLMRequest

_CHIEF = "chief_grand_secretary"
_SECRETARY_1 = "grand_secretary_1"
_SECRETARY_2 = "grand_secretary_2"
_EMPEROR = "emperor"
_MEMBERS = (_CHIEF, _SECRETARY_1, _SECRETARY_2)
_ROLE_LABELS = {_CHIEF: "首辅", _SECRETARY_1: "大学士一", _SECRETARY_2: "大学士二"}
_DRAFT = "draft"
_FINAL = "final_decision"
_VOTE = "vote_result"
_CABINET = "cabinet_opinions"
_WEIGHTS = {_CHIEF: 1.5, _SECRETARY_1: 1.0, _SECRETARY_2: 1.0}
_REDRAFT_INSTRUCTION = (
    "内阁意见不一致，请重新草拟决策，你可以参考其他官员的意见，也可以提出你的个人观点。"
)
_MAX_REASON_CHARS = 400

_PROMPT_ROOT = Path(__file__).resolve().parent / "agent_prompt_list"


def _load_prompt(path: str) -> str:
    return (_PROMPT_ROOT / path).read_text(encoding="utf-8").strip()


_ROLE_INSTRUCTIONS = {
    _CHIEF: _load_prompt("MingAblation/chief_grand_secretary.txt"),
    _SECRETARY_1: _load_prompt("MingAblation/grand_secretary.txt"),
    _SECRETARY_2: _load_prompt("MingAblation/grand_secretary.txt"),
    _EMPEROR: _load_prompt("MingAblation/emperor.txt"),
}
_NORMAL_OUTPUT = _load_prompt("normal_output_requirement.txt")


@dataclass(frozen=True, slots=True)
class MingAblationCallTrace:
    decision_id: str
    role: str
    caller_role: str
    outcome: str
    content: str | None = None
    error: str | None = None
    phase: str | None = None
    decision_maker: str | None = None
    content_type: str | None = None


class MingAblationCourtAgent:
    """Ming ablation: drafts + redraft + weighted vote, NO chief summary."""

    uses_llm = True

    def __init__(
        self,
        *,
        player_id: str,
        chief_client: LLMClient,
        chief_profile: ModelProfile,
        secretary_1_client: LLMClient,
        secretary_1_profile: ModelProfile,
        secretary_2_client: LLMClient,
        secretary_2_profile: ModelProfile,
        emperor_client: LLMClient,
        emperor_profile: ModelProfile,
        conversations: dict[str, AgentConversation],
        validation_retries: int = 2,
        max_connection_retries: int = 2,
    ) -> None:
        self._player_id = player_id
        self._clients = {
            _CHIEF: chief_client,
            _SECRETARY_1: secretary_1_client,
            _SECRETARY_2: secretary_2_client,
            _EMPEROR: emperor_client,
        }
        self._profiles = {
            _CHIEF: chief_profile,
            _SECRETARY_1: secretary_1_profile,
            _SECRETARY_2: secretary_2_profile,
            _EMPEROR: emperor_profile,
        }
        self._conversations = conversations
        self._validation_retries = validation_retries
        self._max_connection_retries = max_connection_retries
        self._connection_failures = {role: 0 for role in _MEMBERS}
        self._decision_id: str | None = None
        self._first: dict[str, str] = {}
        self._redrafted_roles: set[str] = set()
        self._redraft_instructed: set[str] = set()
        self._final_drafts: dict[str, str] = {}
        self._vote: dict[str, object] | None = None
        self._final_raw: str | None = None
        self._trace: list[MingAblationCallTrace] = []
        self._last_llm_call_count = 0
        self._last_warning: ContextWarning | None = None
        self._final_recorded = False

    @property
    def player_id(self) -> str:
        return self._player_id

    @property
    def conversation(self) -> AgentConversation:
        return self._conversations[_EMPEROR]

    @property
    def role_conversations(self) -> dict[str, AgentConversation]:
        return self._conversations

    @property
    def last_llm_call_count(self) -> int:
        return self._last_llm_call_count

    @property
    def last_context_warning(self) -> ContextWarning | None:
        return self._last_warning

    def court_trace(self) -> dict[str, object]:
        return {
            "court": "ming_ablation",
            "decision_id": self._decision_id,
            "calls": [asdict(item) for item in self._trace],
        }

    def record_final_decision(self, request: DecisionRequest, reply: str) -> None:
        self._final_raw = reply
        if self._final_recorded:
            return
        self._final_recorded = True
        self._conversations[_EMPEROR].append_decision(
            decision_id=request.decision_id,
            question_summary=render_decision_question(request),
            assistant_reply=reply,
        )
        self._deliver(request, _EMPEROR, _FINAL, reply, set(_MEMBERS))

    def _record_vote_history(self, request: DecisionRequest, vote: dict[str, object]) -> None:
        raw = _render_vote_result(vote)
        for role in _MEMBERS:
            self._conversations[role].append_internal_decision(
                internal_decision_id=f"{request.decision_id}:vote_result:history",
                decision_id=request.decision_id,
                question_summary=render_decision_question(request),
                decision_maker="system",
                content_type=_VOTE,
                raw_content=raw,
            )

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        self._prepare(request)
        self._last_llm_call_count = 0
        if feedback:
            self._conversations[_EMPEROR].append_error(
                decision_id=request.decision_id,
                question_summary=render_decision_question(request),
                bad_reply="",
                feedback_text=feedback,
            )
        if any(role not in self._first for role in _MEMBERS):
            self._parallel_drafts(request, "first")
        if not _all_same(self._first):
            self._parallel_redrafts(request)
        self._final_drafts = dict(self._first)
        self._vote = (
            _weighted_vote(self._final_drafts) if not _all_same(self._final_drafts) else None
        )
        if self._vote is not None:
            self._record_vote_history(request, self._vote)
        self._deliver_cabinet_block(request)
        if self._final_raw is not None and feedback is None:
            return self._final_raw
        self._final_raw = self._validated_call(_EMPEROR, request, "final")
        return self._final_raw

    def _prepare(self, request: DecisionRequest) -> None:
        if self._decision_id == request.decision_id:
            return
        self._decision_id = request.decision_id
        self._first = {}
        self._redrafted_roles = set()
        self._redraft_instructed = set()
        self._final_drafts = {}
        self._vote = None
        self._final_raw = None
        self._trace = []
        self._final_recorded = False
        self._connection_failures = {role: 0 for role in _MEMBERS}
        for conversation in self._conversations.values():
            if conversation.current_turn is None:
                conversation.start_turn(1)

    def _parallel_drafts(self, request: DecisionRequest, phase: str) -> None:
        pending = [role for role in _MEMBERS if role not in self._first]
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {role: executor.submit(self._draft, role, request, phase) for role in pending}
            for role in pending:
                self._first[role] = futures[role].result()
        for role in _MEMBERS:
            self._deliver(
                request,
                role,
                _DRAFT,
                self._first[role],
                {str(member) for member in _MEMBERS if member != role},
                delivery_key="first",
            )

    def _parallel_redrafts(self, request: DecisionRequest) -> None:
        for role in _MEMBERS:
            self._deliver(
                request,
                role,
                _DRAFT,
                self._first[role],
                {str(member) for member in _MEMBERS if member != role},
                delivery_key="first",
            )
        pending = [role for role in _MEMBERS if role not in self._redrafted_roles]
        for role in pending:
            if role not in self._redraft_instructed:
                self._conversations[role].append_context(_REDRAFT_INSTRUCTION)
                self._redraft_instructed.add(role)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {role: executor.submit(self._redraft, role, request) for role in pending}
            for role in pending:
                self._first[role] = futures[role].result()
                self._redrafted_roles.add(role)
        for role in _MEMBERS:
            self._deliver(
                request,
                role,
                _DRAFT,
                self._first[role],
                {str(member) for member in _MEMBERS if member != role},
                delivery_key="redraft",
            )

    def _draft(self, role: str, request: DecisionRequest, phase: str) -> str:
        try:
            raw = self._validated_call(role, request, phase)
        except (ConnectionError, LLMCallError) as error:
            raw = self._member_connection_fallback(role, request, phase, error)
        self._append_own(role, request, raw)
        return raw

    def _redraft(self, role: str, request: DecisionRequest) -> str:
        try:
            raw = self._validated_call(role, request, "redraft")
        except (ConnectionError, LLMCallError) as error:
            raw = self._member_connection_fallback(role, request, "redraft", error)
        self._append_own(role, request, raw)
        return raw

    def _member_connection_fallback(
        self,
        role: str,
        request: DecisionRequest,
        phase: str,
        error: ConnectionError | LLMCallError,
    ) -> str:
        """Re-raise until call failures are exhausted, then draft a system fallback."""
        self._connection_failures[role] += 1
        if self._connection_failures[role] <= self._max_connection_retries:
            raise error
        option = next(item for item in request.options if item.is_default)
        fallback = json.dumps(
            {
                "selected_option": default_option_json(option),
                "reason": f"{_ROLE_LABELS[role]}重连次数耗尽，无法做出有效回复。",
            },
            ensure_ascii=False,
        )
        self._trace.append(
            MingAblationCallTrace(
                request.decision_id,
                role,
                f"{self._player_id}.{role}",
                "connection_fallback",
                fallback,
                phase=phase,
                decision_maker=role,
                content_type=_DRAFT,
            )
        )
        return fallback

    def _deliver_cabinet_block(self, request: DecisionRequest) -> None:
        block = _render_cabinet_block(self._final_drafts, self._vote)
        self._conversations[_EMPEROR].append_internal_decision(
            internal_decision_id=f"{request.decision_id}:system:{_CABINET}",
            decision_id=request.decision_id,
            question_summary=render_decision_question(request),
            decision_maker="system",
            content_type=_CABINET,
            raw_content=block,
        )

    def _validated_call(
        self,
        role: str,
        request: DecisionRequest,
        phase: str,
        extra: str | None = None,
    ) -> str:
        raw = self._call(role, request, phase, extra)
        validation = parse_and_validate(raw, request)
        attempts = 0
        while not validation.valid and attempts < self._validation_retries:
            self._record_error(
                role,
                request,
                raw,
                validation.error or "回复非法",
                build_feedback(validation, request),
                phase,
            )
            raw = self._call(role, request, phase, extra)
            validation = parse_and_validate(raw, request)
            attempts += 1
        if not validation.valid:
            self._record_error(
                role,
                request,
                raw,
                validation.error or "回复非法",
                build_feedback(validation, request),
                phase,
            )
            option = next(item for item in request.options if item.is_default)
            fallback = json.dumps(
                {
                    "selected_option": default_option_json(option),
                    "reason": "系统采用默认合法选项。",
                },
                ensure_ascii=False,
            )
            self._trace.append(
                MingAblationCallTrace(
                    request.decision_id,
                    role,
                    f"{self._player_id}.{role}",
                    "advice_normalized",
                    fallback,
                    phase=phase,
                    decision_maker=role,
                    content_type=_DRAFT,
                )
            )
            return fallback
        assert validation.option is not None
        selected_option: dict[str, object] = {"option": validation.option.option_id}
        if validation.response is not None and validation.response.target is not None:
            selected_option["target"] = validation.response.target
        return json.dumps(
            {
                "selected_option": selected_option,
                "reason": _truncate(validation.response.reason if validation.response else ""),
            },
            ensure_ascii=False,
        )

    def _call(
        self,
        role: str,
        request: DecisionRequest,
        phase: str,
        extra: str | None = None,
    ) -> str:
        messages, warning = compose_prompt(
            self._conversations[role],
            request,
            pre_decision_context=extra,
            role_instruction=_ROLE_INSTRUCTIONS[role],
            segment3_prompt=_NORMAL_OUTPUT,
        )
        self._last_warning = warning
        profile = self._profiles[role]
        caller = f"{self._player_id}.{role}"
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
                MingAblationCallTrace(
                    request.decision_id,
                    role,
                    caller,
                    "connection_error" if isinstance(error, ConnectionError) else "call_error",
                    error=str(error),
                    phase=phase,
                )
            )
            raise
        self._last_llm_call_count += 1
        content_type = _FINAL if phase == "final" else _DRAFT
        self._trace.append(
            MingAblationCallTrace(
                request.decision_id,
                role,
                caller,
                "success",
                response.content,
                phase=phase,
                decision_maker=role,
                content_type=content_type,
            )
        )
        return response.content

    def _append_own(self, role: str, request: DecisionRequest, raw: str) -> None:
        self._conversations[role].append_decision(
            decision_id=request.decision_id,
            question_summary=render_decision_question(request),
            assistant_reply=raw,
            allow_duplicate_decision_id=True,
        )

    def _deliver(
        self,
        request: DecisionRequest,
        role: str,
        content_type: str,
        raw: str,
        recipients: set[str],
        *,
        delivery_key: str | None = None,
    ) -> None:
        key = delivery_key or content_type
        for recipient in recipients:
            self._conversations[recipient].append_internal_decision(
                internal_decision_id=f"{request.decision_id}:{role}:{key}",
                decision_id=request.decision_id,
                question_summary=render_decision_question(request),
                decision_maker=role,
                content_type=content_type,
                raw_content=raw,
            )

    def _record_error(
        self,
        role: str,
        request: DecisionRequest,
        raw: str,
        error: str,
        feedback: str,
        phase: str,
    ) -> None:
        self._trace.append(
            MingAblationCallTrace(
                request.decision_id,
                role,
                f"{self._player_id}.{role}",
                "validation_error",
                raw,
                error=error,
                phase=phase,
            )
        )
        self._conversations[role].append_error(
            decision_id=request.decision_id,
            question_summary=render_decision_question(request),
            bad_reply=raw,
            feedback_text=feedback,
        )


def _truncate(value: str) -> str:
    return value[:_MAX_REASON_CHARS]


def _signature(raw: str) -> tuple[str, str]:
    value = cast(dict[str, Any], json.loads(raw))
    selected = cast(dict[str, Any], value["selected_option"])
    target = {key: selected[key] for key in selected if key != "option"}
    return str(selected["option"]), json.dumps(target, sort_keys=True, ensure_ascii=False)


def _all_same(values: dict[str, str]) -> bool:
    return len({_signature(raw) for raw in values.values()}) == 1


def _weighted_vote(values: dict[str, str]) -> dict[str, object]:
    totals: dict[str, float] = {}
    for role, raw in values.items():
        option, target = _signature(raw)
        key = json.dumps([option, target], ensure_ascii=False)
        totals[key] = totals.get(key, 0.0) + _WEIGHTS[role]
    winner = max(totals, key=lambda key: (totals[key], key))
    option, target = json.loads(winner)
    return {
        "weights": dict(_WEIGHTS),
        "totals": totals,
        "selected_option": {"option": option, **json.loads(target)},
    }


def _render_vote_result(vote: dict[str, object]) -> str:
    selected = vote.get("selected_option")
    totals = vote.get("totals")
    if not isinstance(selected, dict) or not isinstance(totals, dict):
        return "内阁最终表决结果：\n{selected_option:{option: unknown}} 共计0票"
    typed_totals = cast(dict[str, float], totals)
    lines = ["内阁最终表决结果："]
    for key, count in typed_totals.items():
        option = str(key)
        target: dict[str, object] = {}
        try:
            option_data = json.loads(str(key))
            option = str(option_data[0])
            target_data = json.loads(option_data[1])
            if isinstance(target_data, dict):
                target = cast(dict[str, object], target_data)
        except (json.JSONDecodeError, IndexError, TypeError):
            pass
        rendered = "{selected_option:{" + f"option: {option}"
        for field, value in target.items():
            rendered += f", {field}: {value}"
        rendered += "}}"
        lines.append(f"{rendered} 共计{count}票")
    return "\n".join(lines)


def _option_text(raw: str) -> str:
    """Render one draft's option+target compactly for the tally line."""
    value = cast(dict[str, Any], json.loads(raw))
    selected = cast(dict[str, Any], value["selected_option"])
    option = str(selected["option"])
    target = selected.get("target")
    if target is None or target == {}:
        return option
    if isinstance(target, bool):
        return f"{option}（{json.dumps(target, ensure_ascii=False)}）"
    if isinstance(target, int | float):
        return f"{option}（格子 {target}）"
    if isinstance(target, str):
        return f"{option}（{target}）"
    return f"{option}（{json.dumps(target, ensure_ascii=False, separators=(',', ':'))}）"


def _render_cabinet_block(final_drafts: dict[str, str], vote: dict[str, object] | None) -> str:
    """The 内阁意见与投票 block delivered to the emperor (approved v3 draft)."""
    lines = [
        "## 内阁意见与投票",
        "以下为首辅与两名大学士的最终意见及加权投票结果（首辅权重 1.5，大学士各 1.0，共 3.5 票）。",
        "",
    ]
    for role in _MEMBERS:
        opinion = cast(dict[str, Any], json.loads(final_drafts[role]))
        opinion["decision_maker"] = role
        opinion["content_type"] = _DRAFT
        raw = json.dumps(opinion, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"{_ROLE_LABELS[role]}：")
        lines.append(raw)
        lines.append("")
    lines.append(_render_tally(final_drafts, vote))
    return "\n".join(lines)


def _render_tally(final_drafts: dict[str, str], vote: dict[str, object] | None) -> str:
    if vote is None:
        option = _option_text(next(iter(final_drafts.values())))
        return f"投票结果：{option} 得 3.5 票（首辅、大学士一、大学士二），全票通过。"
    by_option: dict[str, list[str]] = {}
    for role in _MEMBERS:
        option, target = _signature(final_drafts[role])
        key = json.dumps([option, target], ensure_ascii=False)
        by_option.setdefault(key, []).append(role)
    if len(by_option) == len(_MEMBERS):
        parts = [
            f"首辅意见 {_option_text(final_drafts[_CHIEF])} 得 1.5 票",
            f"大学士一 {_option_text(final_drafts[_SECRETARY_1])} 得 1.0 票",
            f"大学士二 {_option_text(final_drafts[_SECRETARY_2])} 得 1.0 票",
        ]
        detail = "，".join(parts)
        chief_option = _option_text(final_drafts[_CHIEF])
        return f"投票结果：三方意见各不相同；{detail}。加权多数为首辅意见：{chief_option}。"
    ranked = sorted(
        by_option.items(),
        key=lambda item: -sum(_WEIGHTS[role] for role in item[1]),
    )
    segments: list[str] = []
    for _key, roles in ranked:
        weight = sum(_WEIGHTS[role] for role in roles)
        labels = "、".join(_ROLE_LABELS[role] for role in roles)
        segments.append(f"{_option_text(final_drafts[roles[0]])} 得 {weight} 票（{labels}）")
    winner = _option_text(final_drafts[ranked[0][1][0]])
    return f"投票结果：{'；'.join(segments)}。加权多数：{winner}。"
