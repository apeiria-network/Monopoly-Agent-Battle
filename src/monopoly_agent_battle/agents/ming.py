"""Ming four-role cabinet workflow."""

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
from monopoly_agent_battle.decision.models import DecisionRequest, DecisionValidation
from monopoly_agent_battle.decision.prompts import render_decision_question
from monopoly_agent_battle.decision.protocol import default_option_json, parse_and_validate
from monopoly_agent_battle.llm.protocol import LLMClient, LLMRequest

_CHIEF = "chief_grand_secretary"
_SECRETARY_1 = "grand_secretary_1"
_SECRETARY_2 = "grand_secretary_2"
_EMPEROR = "emperor"
_MEMBERS = (_CHIEF, _SECRETARY_1, _SECRETARY_2)
_DRAFT = "draft"
_ADVICE = "advice"
_FINAL = "final_decision"
_VOTE = "vote_result"
_REDRAFT_INSTRUCTION = (
    "内阁意见不一致，请重新草拟决策，你可以参考其他官员的意见，也可以提出你的个人观点。"
)
_ADVICE_INSTRUCTION = "请你汇总3位官员的草拟决策，并为决策{selected_option}撰写对应决策理由"
_MAX_REASON_CHARS = 400

_PROMPT_ROOT = Path(__file__).resolve().parent / "agent_prompt_list"


def _load_prompt(path: str) -> str:
    return (_PROMPT_ROOT / path).read_text(encoding="utf-8").strip()


_ROLE_INSTRUCTIONS = {
    _CHIEF: _load_prompt("Ming/chief_grand_secretary.txt"),
    _SECRETARY_1: _load_prompt("Ming/grand_secretary.txt"),
    _SECRETARY_2: _load_prompt("Ming/grand_secretary.txt"),
    _EMPEROR: _load_prompt("Ming/emperor.txt"),
}


@dataclass(frozen=True, slots=True)
class MingCallTrace:
    decision_id: str
    role: str
    caller_role: str
    outcome: str
    content: str | None = None
    error: str | None = None
    phase: str | None = None
    decision_maker: str | None = None
    content_type: str | None = None


class MingCourtAgent:
    """Execute the confirmed Ming cabinet workflow."""

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
        self._decision_id: str | None = None
        self._first: dict[str, str] = {}
        self._final_drafts: dict[str, str] = {}
        self._vote: dict[str, object] | None = None
        self._advice: str | None = None
        self._final_raw: str | None = None
        self._trace: list[MingCallTrace] = []
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
            "court": "ming",
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
        if not self._first:
            self._parallel_drafts(request, "first")
        if not _all_same(self._first):
            self._parallel_redrafts(request)
        self._final_drafts = dict(self._first)
        self._vote = (
            _weighted_vote(self._final_drafts) if not _all_same(self._final_drafts) else None
        )
        if self._vote is not None:
            self._record_vote_history(request, self._vote)
        self._conversations[_CHIEF].append_context(
            _ADVICE_INSTRUCTION.format(
                selected_option=json.dumps(
                    _expected_result(self._final_drafts, self._vote),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        )
        self._advice = self._call_advice(request)
        self._deliver(
            request, _CHIEF, _ADVICE, self._advice, {_SECRETARY_1, _SECRETARY_2, _EMPEROR}
        )
        if self._final_raw is not None and feedback is None:
            return self._final_raw
        self._final_raw = self._validated_call(_EMPEROR, request, "final")
        return self._final_raw

    def _prepare(self, request: DecisionRequest) -> None:
        if self._decision_id == request.decision_id:
            return
        self._decision_id = request.decision_id
        self._first = {}
        self._final_drafts = {}
        self._vote = None
        self._advice = None
        self._final_raw = None
        self._trace = []
        self._final_recorded = False
        for conversation in self._conversations.values():
            if conversation.current_turn is None:
                conversation.start_turn(1)

    def _parallel_drafts(self, request: DecisionRequest, phase: str) -> None:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                role: executor.submit(self._draft, role, request, phase) for role in _MEMBERS
            }
            for role in _MEMBERS:
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
        for role in _MEMBERS:
            self._conversations[role].append_context(_REDRAFT_INSTRUCTION)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {role: executor.submit(self._redraft, role, request) for role in _MEMBERS}
            for role in _MEMBERS:
                self._first[role] = futures[role].result()
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
        raw = self._validated_call(role, request, phase)
        self._append_own(role, request, raw)
        return raw

    def _redraft(self, role: str, request: DecisionRequest) -> str:
        raw = self._validated_call(role, request, "redraft")
        self._append_own(role, request, raw)
        return raw

    def _call_advice(self, request: DecisionRequest) -> str:
        expected = _expected_result(self._final_drafts, self._vote)
        material = ""
        raw = self._call(_CHIEF, request, "advice", material)
        validation = parse_and_validate(raw, request)
        attempts = 0
        while attempts < self._validation_retries:
            mismatch = validation.valid and _validation_signature(
                validation
            ) != _expected_signature(expected)
            if validation.valid and not mismatch:
                break
            error = (
                "首辅汇总未采用内阁确定结果。"
                if mismatch
                else validation.error or "首辅汇总回复非法"
            )
            feedback = (
                "请严格采用内阁确定的 selected_option。"
                if mismatch
                else build_feedback(validation, request)
            )
            self._record_error(_CHIEF, request, raw, error, feedback, "advice")
            raw = self._call(_CHIEF, request, "advice", material)
            validation = parse_and_validate(raw, request)
            attempts += 1
        if validation.valid and _validation_signature(validation) == _expected_signature(expected):
            reason = _response_reason(validation)
        else:
            reason = (
                "系统采用内阁一致结果。" if self._vote is None else "系统采用内阁加权投票结果。"
            )
        normalized = json.dumps({"selected_option": expected, "reason": reason}, ensure_ascii=False)
        self._trace.append(
            MingCallTrace(
                request.decision_id,
                _CHIEF,
                f"{self._player_id}.{_CHIEF}",
                "advice_normalized",
                normalized,
                phase="advice",
                decision_maker=_CHIEF,
                content_type=_ADVICE,
            )
        )
        self._append_advice_own(request, normalized)
        return normalized

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
            return json.dumps(
                {
                    "selected_option": default_option_json(option),
                    "reason": "系统采用默认合法选项。",
                },
                ensure_ascii=False,
            )
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
                )
            )
        except ConnectionError as error:
            self._last_llm_call_count += 1
            self._trace.append(
                MingCallTrace(
                    request.decision_id,
                    role,
                    caller,
                    "connection_error",
                    error=str(error),
                    phase=phase,
                )
            )
            raise
        self._last_llm_call_count += 1
        content_type = {
            "first": _DRAFT,
            "redraft": _DRAFT,
            "advice": _ADVICE,
            "final": _FINAL,
        }.get(phase, _DRAFT)
        self._trace.append(
            MingCallTrace(
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

    def _append_advice_own(self, request: DecisionRequest, raw: str) -> None:
        self._conversations[_CHIEF].append_decision(
            decision_id=request.decision_id,
            question_summary=render_decision_question(request),
            assistant_reply=raw,
            allow_duplicate_decision_id=True,
        )

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
            MingCallTrace(
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


def _response_reason(validation: object) -> str:
    response = getattr(validation, "response", None)
    reason = getattr(response, "reason", "")
    return _truncate(str(reason))


def _expected_result(drafts: dict[str, str], vote: dict[str, object] | None) -> dict[str, object]:
    if vote is not None:
        selected = vote["selected_option"]
        assert isinstance(selected, dict)
        return dict(cast(dict[str, object], selected))
    raw = next(iter(drafts.values()))
    value = cast(dict[str, Any], json.loads(raw))
    selected = cast(dict[str, object], value["selected_option"])
    return dict(selected)


def _expected_signature(expected: dict[str, object]) -> tuple[str, str]:
    option = expected.get("option")
    target = expected.get("target")
    return str(option), json.dumps(target, ensure_ascii=False, sort_keys=True)


def _validation_signature(validation: DecisionValidation) -> tuple[str, str]:
    assert validation.response is not None
    return validation.response.selected_option, json.dumps(
        validation.response.target, ensure_ascii=False, sort_keys=True
    )


def _signature(raw: str) -> tuple[str, str]:
    value = cast(dict[str, Any], json.loads(raw))
    selected = cast(dict[str, Any], value["selected_option"])
    target = {key: selected[key] for key in selected if key != "option"}
    return str(selected["option"]), json.dumps(target, sort_keys=True, ensure_ascii=False)


def _all_same(values: dict[str, str]) -> bool:
    return len({_signature(raw) for raw in values.values()}) == 1


def _weighted_vote(values: dict[str, str]) -> dict[str, object]:
    weights = {_CHIEF: 1.5, _SECRETARY_1: 1.0, _SECRETARY_2: 1.0}
    totals: dict[str, float] = {}
    for role, raw in values.items():
        option, target = _signature(raw)
        key = json.dumps([option, target], ensure_ascii=False)
        totals[key] = totals.get(key, 0.0) + weights[role]
    winner = max(totals, key=lambda key: (totals[key], key))
    option, target = json.loads(winner)
    return {
        "weights": weights,
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
