"""Qin four-role court workflow.

The chancellor and grand marshal independently advise, the imperial
counsellor reviews both advice messages, and the emperor returns the only
engine-facing decision.  Court messages are delivered through each role's
private AgentConversation and therefore render in segment 5.
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
from monopoly_agent_battle.llm.protocol import LLMClient, LLMMessage, LLMRequest
from monopoly_agent_battle.performance.random_generator import (
    PerformanceGenerator,
    random_officer_performance,
)

_CHANCELLOR = "chancellor"
_GRAND_MARSHAL = "grand_marshal"
_COUNSELLOR = "imperial_counsellor"
_EMPEROR = "emperor"
_ADVICE = "advice"
_COMMENT = "comment"
_FINAL = "final_decision"
_MAX_REASON_CHARS = 400

_PROMPT_ROOT = Path(__file__).resolve().parent / "agent_prompt_list"


def _load_prompt(relative_path: str) -> str:
    return (_PROMPT_ROOT / relative_path).read_text(encoding="utf-8").strip()


_ROLE_INSTRUCTIONS = {
    _CHANCELLOR: _load_prompt("Qin/Qin_chancellor.txt"),
    _GRAND_MARSHAL: _load_prompt("Qin/Qin_grand_marshal.txt"),
    _COUNSELLOR: _load_prompt("Qin/Qin_imperial_counsellor.txt"),
    _EMPEROR: _load_prompt("Qin/Qin_emperor.txt"),
}
_NORMAL_OUTPUT_REQUIREMENT = _load_prompt("normal_output_requirement.txt")
_COUNSELLOR_OUTPUT_REQUIREMENT = _load_prompt("Qin/Qin_cousellor_output_requirement.txt")
_COUNSELLOR_SPECIAL_CONTEXT = _load_prompt("Qin/Qin_cousellor_candidates.txt")


@dataclass(frozen=True, slots=True)
class QinCallTrace:
    decision_id: str
    role: str
    caller_role: str
    outcome: str
    content: str | None = None
    error: str | None = None
    decision_maker: str | None = None
    content_type: str | None = None


class QinCourtAgent:
    """Implement the confirmed Qin call order and visibility boundaries."""

    uses_llm = True

    def __init__(
        self,
        *,
        player_id: str,
        chancellor_client: LLMClient,
        chancellor_profile: ModelProfile,
        grand_marshal_client: LLMClient,
        grand_marshal_profile: ModelProfile,
        imperial_counsellor_client: LLMClient,
        imperial_counsellor_profile: ModelProfile,
        emperor_client: LLMClient,
        emperor_profile: ModelProfile,
        conversations: dict[str, AgentConversation],
        validation_retries: int = 2,
        performance_generator: PerformanceGenerator = random_officer_performance,
    ) -> None:
        self._player_id = player_id
        self._clients = {
            _CHANCELLOR: chancellor_client,
            _GRAND_MARSHAL: grand_marshal_client,
            _COUNSELLOR: imperial_counsellor_client,
            _EMPEROR: emperor_client,
        }
        self._profiles = {
            _CHANCELLOR: chancellor_profile,
            _GRAND_MARSHAL: grand_marshal_profile,
            _COUNSELLOR: imperial_counsellor_profile,
            _EMPEROR: emperor_profile,
        }
        self._conversations = conversations
        self._validation_retries = validation_retries
        self._performance_generator = performance_generator
        self._decision_id: str | None = None
        self._responses: dict[str, str] = {}
        self._trace: list[QinCallTrace] = []
        self._last_llm_call_count = 0
        self._last_warning: ContextWarning | None = None

    @property
    def player_id(self) -> str:
        return self._player_id

    @property
    def conversation(self) -> AgentConversation:
        return self._conversations[_EMPEROR]

    @property
    def last_llm_call_count(self) -> int:
        return self._last_llm_call_count

    @property
    def last_context_warning(self) -> ContextWarning | None:
        return self._last_warning

    def court_trace(self) -> dict[str, object]:
        return {
            "court": "qin",
            "decision_id": self._decision_id,
            "calls": [asdict(item) for item in self._trace],
        }

    def court_calls(self) -> list[dict[str, object]]:
        return [asdict(item) for item in self._trace]

    def record_final_decision(self, request: DecisionRequest, reply: str) -> None:
        """Record the one engine-facing reply and broadcast it to court roles."""
        self._responses[_EMPEROR] = reply
        self._deliver(request, _EMPEROR, _FINAL, reply, {_CHANCELLOR, _GRAND_MARSHAL, _COUNSELLOR})

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        self._prepare(request)
        self._last_llm_call_count = 0
        if not {_CHANCELLOR, _GRAND_MARSHAL} <= self._responses.keys():
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {
                    role: executor.submit(self._call_adviser, role, request)
                    for role in (_CHANCELLOR, _GRAND_MARSHAL)
                    if role not in self._responses
                }
                for role in (_CHANCELLOR, _GRAND_MARSHAL):
                    if role in futures:
                        futures[role].result()
        if _COUNSELLOR not in self._responses:
            self._call_counsellor(request)
        self._deliver(request, _COUNSELLOR, _COMMENT, self._responses[_COUNSELLOR], {_EMPEROR})
        response = self._call_emperor(request)
        self._deliver(request, _CHANCELLOR, _ADVICE, self._responses[_CHANCELLOR], {_GRAND_MARSHAL})
        self._deliver(
            request, _GRAND_MARSHAL, _ADVICE, self._responses[_GRAND_MARSHAL], {_CHANCELLOR}
        )
        self._deliver(
            request,
            _COUNSELLOR,
            _COMMENT,
            self._responses[_COUNSELLOR],
            {_CHANCELLOR, _GRAND_MARSHAL},
        )
        return response

    def _prepare(self, request: DecisionRequest) -> None:
        if self._decision_id == request.decision_id:
            return
        self._decision_id = request.decision_id
        self._responses = {}
        self._trace = []
        self._last_warning = None
        for conversation in self._conversations.values():
            if conversation.current_turn is None:
                conversation.start_turn(1)

    def _call_adviser(self, role: str, request: DecisionRequest) -> None:
        raw = self._call(role, request, self._messages(role, request))
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
            raw = self._call(role, request, self._messages(role, request))
            validation = parse_and_validate(raw, request)
            attempts += 1
        if not validation.valid:
            self._record_validation(
                role,
                request,
                raw,
                validation.error or "回复非法",
                build_feedback(validation, request),
            )
            default = next(option for option in request.options if option.is_default)
            normalized = json.dumps(
                {"selected_option": default_option_json(default), "reason": _truncate(raw)},
                ensure_ascii=False,
            )
        else:
            assert validation.option is not None
            normalized = json.dumps(
                {
                    "selected_option": {
                        "option": validation.option.option_id,
                        **(validation.target or {}),
                    },
                    "reason": _truncate(validation.response.reason if validation.response else ""),
                },
                ensure_ascii=False,
            )
        self._responses[role] = normalized
        self._append_own_decision(role, request, normalized)
        self._deliver(request, role, _ADVICE, normalized, {_COUNSELLOR, _EMPEROR})

    def _call_counsellor(self, request: DecisionRequest) -> None:
        raw = self._call(_COUNSELLOR, request, self._messages(_COUNSELLOR, request))
        parsed = _parse_counsellor(raw)
        attempts = 0
        while parsed is None and attempts < self._validation_retries:
            self._record_validation(
                _COUNSELLOR,
                request,
                raw,
                "御史大夫评价结构非法",
                "Error: 御史大夫评价结构非法，请按要求输出包含两项 assessments 的 JSON 对象。",
            )
            raw = self._call(_COUNSELLOR, request, self._messages(_COUNSELLOR, request))
            parsed = _parse_counsellor(raw)
            attempts += 1
        if parsed is None:
            self._record_validation(
                _COUNSELLOR,
                request,
                raw,
                "御史大夫评价结构非法",
                "Error: 御史大夫评价结构非法，请按要求输出包含两项 assessments 的 JSON 对象。",
            )
            parsed = _fallback_comment()
        self._responses[_COUNSELLOR] = parsed
        self._append_own_decision(_COUNSELLOR, request, parsed)
        self._deliver(request, _COUNSELLOR, _COMMENT, parsed, {_EMPEROR})

    def _call_emperor(self, request: DecisionRequest) -> str:
        messages = self._messages(_EMPEROR, request)
        raw = self._call(_EMPEROR, request, messages)
        validation = parse_and_validate(raw, request)
        if not validation.valid:
            self._record_validation(
                _EMPEROR,
                request,
                raw,
                validation.error or "回复非法",
                build_feedback(validation, request),
            )
            return raw
        assert validation.option is not None
        normalized = json.dumps(
            {
                "selected_option": {
                    "option": validation.option.option_id,
                    **(validation.target or {}),
                },
                "reason": _truncate(validation.response.reason if validation.response else ""),
            },
            ensure_ascii=False,
        )
        self._responses[_EMPEROR] = normalized
        return raw

    def _call(self, role: str, request: DecisionRequest, messages: tuple[LLMMessage, ...]) -> str:
        profile = self._profiles[role]
        caller = f"{self._player_id}.{role}"
        try:
            response = self._clients[role].complete(
                LLMRequest(
                    messages=messages,
                    model=profile.model,
                    caller_role=caller,
                    temperature=profile.temperature,
                    max_tokens=profile.max_tokens,
                    timeout_seconds=profile.timeout_seconds,
                )
            )
        except ConnectionError as error:
            self._last_llm_call_count += 1
            self._trace.append(
                QinCallTrace(
                    request.decision_id, role, caller, "connection_error", error=str(error)
                )
            )
            raise
        self._last_llm_call_count += 1
        self._trace.append(
            QinCallTrace(
                request.decision_id,
                role,
                caller,
                "success",
                response.content,
                decision_maker=role,
                content_type=_ADVICE
                if role in {_CHANCELLOR, _GRAND_MARSHAL}
                else (_COMMENT if role == _COUNSELLOR else _FINAL),
            )
        )
        return response.content

    def _messages(self, role: str, request: DecisionRequest) -> tuple[LLMMessage, ...]:
        pre_decision_context = self._performance_generator(request) if role == _COUNSELLOR else None
        messages, warning = compose_prompt(
            self._conversations[role],
            request,
            pre_decision_context=pre_decision_context,
            role_instruction=_ROLE_INSTRUCTIONS[role],
            segment3_prompt=(
                _COUNSELLOR_OUTPUT_REQUIREMENT
                if role == _COUNSELLOR
                else _NORMAL_OUTPUT_REQUIREMENT
            ),
            post_decision_context=(_COUNSELLOR_SPECIAL_CONTEXT if role == _COUNSELLOR else None),
        )
        self._last_warning = warning
        return messages

    def _append_own_decision(self, role: str, request: DecisionRequest, raw: str) -> None:
        self._conversations[role].append_decision(
            decision_id=request.decision_id,
            question_summary=render_decision_question(request),
            assistant_reply=raw,
        )

    def _deliver(
        self, request: DecisionRequest, role: str, content_type: str, raw: str, recipients: set[str]
    ) -> None:
        for recipient in recipients:
            self._conversations[recipient].append_internal_decision(
                internal_decision_id=f"{request.decision_id}:{role}:{content_type}",
                decision_id=request.decision_id,
                question_summary=render_decision_question(request),
                decision_maker=role,
                content_type=content_type,
                raw_content=raw,
            )

    def _record_validation(
        self,
        role: str,
        request: DecisionRequest,
        raw: str,
        error: str,
        feedback: str,
    ) -> None:
        self._trace.append(
            QinCallTrace(
                request.decision_id,
                role,
                f"{self._player_id}.{role}",
                "validation_error",
                raw,
                error=error,
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


def _parse_counsellor(raw: str) -> str | None:
    try:
        document_value: object = json.loads(raw)
        if not isinstance(document_value, dict):
            return None
        document = cast(dict[str, Any], document_value)
        assessments_value: Any = document.get("assessments")
        if not isinstance(assessments_value, list):
            return None
        assessments: list[Any] = list(cast(list[Any], assessments_value))
        if len(assessments) != 2:
            return None
        by_id: dict[str, dict[str, object]] = {}
        for value in assessments:
            if not isinstance(value, dict):
                return None
            typed_value = cast(dict[str, object], value)
            officer_id = typed_value.get("officer_id")
            if not isinstance(officer_id, str):
                return None
            by_id[officer_id] = typed_value
        if set(by_id) != {_CHANCELLOR, _GRAND_MARSHAL}:
            return None
        for officer in (_CHANCELLOR, _GRAND_MARSHAL):
            item = by_id[officer]
            if item.get("judgement") not in {"agree", "disagree", "neutral"}:
                return None
        normalized = {
            "reason": _truncate(str(document.get("reason", ""))),
            "assessments": [
                {
                    "officer_id": officer,
                    "judgement": by_id[officer]["judgement"],
                    "reason": _truncate(str(by_id[officer].get("reason", ""))),
                }
                for officer in (_CHANCELLOR, _GRAND_MARSHAL)
            ],
        }
        return json.dumps(normalized, ensure_ascii=False)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _fallback_comment() -> str:
    return json.dumps(
        {
            "reason": "御史大夫多次重试失败，无法回复",
            "assessments": [
                {"officer_id": _CHANCELLOR, "judgement": "neutral", "reason": ""},
                {"officer_id": _GRAND_MARSHAL, "judgement": "neutral", "reason": ""},
            ],
        },
        ensure_ascii=False,
    )
