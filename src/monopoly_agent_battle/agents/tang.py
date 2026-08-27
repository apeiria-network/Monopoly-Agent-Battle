"""Tang three-role court workflow."""

from __future__ import annotations

import json
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

_ZHONGSHU = "zhongshu"
_MENXIA = "menxia"
_EMPEROR = "emperor"
_DRAFT = "draft"
_REVIEW = "review"
_FINAL = "final_decision"
_MAX_ROUNDS = 3
_MAX_REASON_CHARS = 400
_PROMPT_ROOT = Path(__file__).resolve().parent / "agent_prompt_list"


def _load_prompt(path: str) -> str:
    return (_PROMPT_ROOT / path).read_text(encoding="utf-8").strip()


_ROLE_INSTRUCTIONS = {
    _ZHONGSHU: _load_prompt("Tang/Tang_zhongshu.txt"),
    _MENXIA: _load_prompt("Tang/Tang_menxia.txt"),
    _EMPEROR: _load_prompt("Tang/Tang_emperor.txt"),
}
_NORMAL_OUTPUT = _load_prompt("normal_output_requirement.txt")
_MENXIA_OUTPUT = _load_prompt("Tang/Tang_menxia_output_requirement.txt")
_MENXIA_OPTIONS = """## 门下省特殊候选项
门下省只审核中书省草案，不选择游戏操作。请在以下候选中选择一个：
- agree：认可草案，提交皇帝裁决
- disagree：否决草案，退回中书省重拟
返回 selected_option.option，且不得填写 target。"""


@dataclass(frozen=True, slots=True)
class TangCallTrace:
    decision_id: str
    role: str
    caller_role: str
    outcome: str
    content: str | None = None
    error: str | None = None
    round: int | None = None
    decision_maker: str | None = None
    content_type: str | None = None


@dataclass(slots=True)
class _Round:
    draft: str
    review: str
    verdict: str


class TangCourtAgent:
    uses_llm = True

    def __init__(
        self,
        *,
        player_id: str,
        zhongshu_client: LLMClient,
        zhongshu_profile: ModelProfile,
        menxia_client: LLMClient,
        menxia_profile: ModelProfile,
        emperor_client: LLMClient,
        emperor_profile: ModelProfile,
        conversations: dict[str, AgentConversation],
        validation_retries: int = 2,
    ) -> None:
        self._player_id = player_id
        self._clients = {
            _ZHONGSHU: zhongshu_client,
            _MENXIA: menxia_client,
            _EMPEROR: emperor_client,
        }
        self._profiles = {
            _ZHONGSHU: zhongshu_profile,
            _MENXIA: menxia_profile,
            _EMPEROR: emperor_profile,
        }
        self._conversations = conversations
        self._validation_retries = validation_retries
        self._decision_id: str | None = None
        self._rounds: list[_Round] = []
        self._final_raw: str | None = None
        self._trace: list[TangCallTrace] = []
        self._last_llm_call_count = 0
        self._last_warning: ContextWarning | None = None
        self._final_recorded = False
        self._current_draft: str | None = None

    def player_id(self) -> str:
        return self._player_id

    @property
    def conversation(self) -> AgentConversation:
        return self._conversations[_EMPEROR]

    @property
    def last_llm_call_count(self) -> int:
        return self._last_llm_call_count

    @property
    def role_conversations(self) -> dict[str, AgentConversation]:
        return self._conversations

    @property
    def last_context_warning(self) -> ContextWarning | None:
        return self._last_warning

    def court_trace(self) -> dict[str, object]:
        return {
            "court": "tang",
            "decision_id": self._decision_id,
            "calls": [asdict(x) for x in self._trace],
        }

    def court_calls(self) -> list[dict[str, object]]:
        return [asdict(x) for x in self._trace]

    def record_final_decision(self, request: DecisionRequest, reply: str) -> None:
        self._final_raw = reply
        if self._final_recorded:
            return
        self._final_recorded = True
        rounds = (
            self._rounds
            if len(self._rounds) == _MAX_ROUNDS
            and all(item.verdict == "disagree" for item in self._rounds)
            else self._rounds[-1:]
        )
        for index, item in enumerate(rounds, 1):
            for role, content_type, raw in (
                (_ZHONGSHU, _DRAFT, item.draft),
                (_MENXIA, _REVIEW, item.review),
            ):
                self._conversations[_EMPEROR].insert_internal_decision_before_decision(
                    internal_decision_id=(
                        f"{request.decision_id}:emperor_history:{index}:{content_type}"
                    ),
                    decision_id=request.decision_id,
                    question_summary=render_decision_question(request),
                    decision_maker=role,
                    content_type=content_type,
                    raw_content=raw,
                )
        self._deliver(request, _EMPEROR, _FINAL, reply, {_ZHONGSHU, _MENXIA})

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        self._prepare(request)
        if feedback:
            self._conversations[_EMPEROR].append_error(
                decision_id=request.decision_id,
                question_summary=render_decision_question(request),
                bad_reply="",
                feedback_text=feedback,
            )
        self._last_llm_call_count = 0
        while len(self._rounds) < _MAX_ROUNDS:
            if self._current_draft is None:
                if self._rounds and self._rounds[-1].verdict != "disagree":
                    break
                self._call_draft(request, len(self._rounds) + 1)
            review, verdict = self._call_review(request, len(self._rounds) + 1)
            self._rounds.append(_Round(self._current_draft or "", review, verdict))
            self._current_draft = None
            if verdict == "agree":
                break
        if self._final_raw is not None and feedback is None:
            return self._final_raw
        return self._call_emperor(request)

    def _prepare(self, request: DecisionRequest) -> None:
        if self._decision_id == request.decision_id:
            return
        self._decision_id = request.decision_id
        self._rounds = []
        self._final_raw = None
        self._trace = []
        self._final_recorded = False
        self._current_draft = None
        for conversation in self._conversations.values():
            if conversation.current_turn is None:
                conversation.start_turn(1)

    def _call_draft(self, request: DecisionRequest, round_number: int) -> str:
        role = _ZHONGSHU
        raw = self._validated_engine_call(
            role, request, self._messages(role, request, round_number), round_number
        )
        self._append_own(role, request, raw)
        self._current_draft = raw
        self._deliver(request, role, _DRAFT, raw, {_MENXIA})
        return raw

    def _call_review(self, request: DecisionRequest, round_number: int) -> tuple[str, str]:
        role = _MENXIA
        raw = self._validated_review_call(request, round_number)
        parsed = _parse_review(raw)
        if parsed is None:
            raw = _fallback_review()
            verdict = "disagree"
        else:
            verdict, raw = parsed
        self._append_own(role, request, raw)
        self._deliver(request, role, _REVIEW, raw, {_ZHONGSHU})
        return raw, verdict

    def _validated_review_call(self, request: DecisionRequest, round_number: int) -> str:
        raw = self._call(
            _MENXIA, request, self._messages(_MENXIA, request, round_number), round_number
        )
        validation_feedback = (
            "Error: 门下省审核结构非法，请按要求输出仅含 reason 与 agree/disagree 的 JSON。"
        )
        for _ in range(self._validation_retries):
            if _parse_review(raw) is not None:
                return raw
            self._record_error(
                _MENXIA,
                request,
                raw,
                "门下省审核结构非法",
                validation_feedback,
                round_number,
            )
            raw = self._call(
                _MENXIA, request, self._messages(_MENXIA, request, round_number), round_number
            )
        if _parse_review(raw) is None:
            self._record_error(
                _MENXIA,
                request,
                raw,
                "门下省审核结构非法",
                validation_feedback,
                round_number,
            )
        return raw

    def _call_emperor(self, request: DecisionRequest) -> str:
        role = _EMPEROR
        raw = self._call(
            role, request, self._messages(role, request, len(self._rounds)), len(self._rounds)
        )
        return raw

    def _validated_engine_call(
        self,
        role: str,
        request: DecisionRequest,
        messages: tuple[LLMMessage, ...],
        round_number: int,
    ) -> str:
        raw = self._call(role, request, messages, round_number)
        validation = parse_and_validate(raw, request)
        attempts = 0
        while not validation.valid and attempts < self._validation_retries:
            self._record_error(
                role,
                request,
                raw,
                validation.error or "回复非法",
                build_feedback(validation, request),
                round_number,
            )
            raw = self._call(
                role, request, self._messages(role, request, round_number), round_number
            )
            validation = parse_and_validate(raw, request)
            attempts += 1
        if not validation.valid:
            self._record_error(
                role,
                request,
                raw,
                validation.error or "回复非法",
                build_feedback(validation, request),
                round_number,
            )
            default = next(option for option in request.options if option.is_default)
            return json.dumps(
                {
                    "selected_option": default_option_json(default),
                    "reason": "中书省多次回复非法，采用默认合法草案。",
                },
                ensure_ascii=False,
            )
        assert validation.option is not None
        return json.dumps(
            {
                "selected_option": {
                    "option": validation.option.option_id,
                    **(validation.target or {}),
                },
                "reason": _truncate(validation.response.reason if validation.response else ""),
            },
            ensure_ascii=False,
        )

    def _call(
        self,
        role: str,
        request: DecisionRequest,
        messages: tuple[LLMMessage, ...],
        round_number: int,
    ) -> str:
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
                TangCallTrace(
                    request.decision_id,
                    role,
                    caller,
                    "connection_error",
                    error=str(error),
                    round=round_number,
                )
            )
            raise
        self._last_llm_call_count += 1
        content_type = _DRAFT if role == _ZHONGSHU else _REVIEW if role == _MENXIA else _FINAL
        self._trace.append(
            TangCallTrace(
                request.decision_id,
                role,
                caller,
                "success",
                response.content,
                round=round_number,
                decision_maker=role,
                content_type=content_type,
            )
        )
        return response.content

    def _messages(
        self, role: str, request: DecisionRequest, round_number: int
    ) -> tuple[LLMMessage, ...]:
        pre = None
        if role == _EMPEROR:
            rounds = (
                self._rounds
                if len(self._rounds) == _MAX_ROUNDS
                and all(x.verdict == "disagree" for x in self._rounds)
                else self._rounds[-1:]
            )
            pre = _trusted_round_context(
                rounds, 1 if len(rounds) == _MAX_ROUNDS else len(self._rounds)
            )
        elif role == _MENXIA and self._current_draft is not None:
            pre = "## 当前中书省草案\n" + self._current_draft
        messages, warning = compose_prompt(
            self._conversations[role],
            request,
            pre_decision_context=pre,
            role_instruction=_ROLE_INSTRUCTIONS[role],
            segment3_prompt=_MENXIA_OUTPUT if role == _MENXIA else _NORMAL_OUTPUT,
            post_decision_context=_MENXIA_OPTIONS if role == _MENXIA else None,
        )
        self._last_warning = warning
        return messages

    def _append_own(self, role: str, request: DecisionRequest, raw: str) -> None:
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
                internal_decision_id=(
                    f"{request.decision_id}:{role}:{content_type}:{len(self._rounds) + 1}"
                ),
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
        round_number: int | None = None,
    ) -> None:
        self._trace.append(
            TangCallTrace(
                request.decision_id,
                role,
                f"{self._player_id}.{role}",
                "validation_error",
                raw,
                error=error,
                round=round_number,
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


def _trusted_round_context(rounds: list[_Round], start_round: int = 1) -> str:
    chunks: list[str] = []
    for i, item in enumerate(rounds, start_round):
        for role, content_type, raw in (
            (_ZHONGSHU, _DRAFT, item.draft),
            (_MENXIA, _REVIEW, item.review),
        ):
            try:
                value = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                value = raw
            if isinstance(value, dict):
                rendered: object = dict(cast(dict[str, Any], value))
                rendered["decision_maker"] = role
                rendered["content_type"] = content_type
            else:
                rendered = {
                    "content": value,
                    "decision_maker": role,
                    "content_type": content_type,
                }
            heading = "中书省草案" if content_type == _DRAFT else "门下省审核"
            chunks.append(f"## 第{i}轮{heading}\n" + json.dumps(rendered, ensure_ascii=False))
    return "\n\n".join(chunks)


def _parse_review(raw: str) -> tuple[str, str] | None:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            return None
        document = cast(dict[str, Any], value)
        reason = document.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return None
        selected_value = document.get("selected_option")
        if not isinstance(selected_value, dict):
            return None
        selected = cast(dict[str, Any], selected_value)
        option = selected.get("option")
        if set(selected) != {"option"} or option not in {"agree", "disagree"}:
            return None
        normalized = json.dumps(
            {
                "reason": reason[:_MAX_REASON_CHARS],
                "selected_option": {"option": option},
            },
            ensure_ascii=False,
        )
        return str(option), normalized
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _fallback_review() -> str:
    return json.dumps(
        {
            "reason": "门下省多次审核回复非法，否决当前草案。",
            "selected_option": {"option": "disagree"},
        },
        ensure_ascii=False,
    )
