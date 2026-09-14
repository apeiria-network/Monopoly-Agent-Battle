"""Shang2 four-role court workflow (three ministers + system omens + emperor).

The redesigned Shang court (requirements 5.5.8): three ministers share one
role prompt and independently advise on the current external decision.  Once
all three replies are final, the system deduplicates the suggested options
(option id + complete target), deterministically assigns one oracle omen per
distinct suggestion and delivers oracle-bearing copies only to the emperor —
ministers never see the ``oracle`` field.  The emperor then returns the only
engine-facing decision.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from monopoly_agent_battle.config.models import ModelProfile
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.context.token_guard import ContextWarning
from monopoly_agent_battle.context.validation_feedback import build_feedback
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.prompts import render_decision_question
from monopoly_agent_battle.decision.protocol import (
    default_option_json,
    parse_and_validate,
)
from monopoly_agent_battle.llm.protocol import LLMCallError, LLMClient, LLMRequest

MINISTER_1 = "minister_1"
MINISTER_2 = "minister_2"
MINISTER_3 = "minister_3"
MINISTERS = (MINISTER_1, MINISTER_2, MINISTER_3)
EMPEROR = "emperor"
_ADVICE = "advice"
_FINAL = "final_decision"
_ORACLE_DELIVERY = "advice:oracle"
_OMENS = ("大吉", "中吉", "小吉", "小凶", "中凶", "大凶")
_ORACLE_VERSION = "shang2-oracle-v1"
_MAX_REASON_CHARS = 400
_VALIDATION_FALLBACK_REASON = "系统采用默认合法选项。"
_ROLE_LABELS = {
    MINISTER_1: "大臣一",
    MINISTER_2: "大臣二",
    MINISTER_3: "大臣三",
}

_PROMPT_ROOT = Path(__file__).resolve().parent / "agent_prompt_list"


def _load_prompt(relative_path: str) -> str:
    return (_PROMPT_ROOT / relative_path).read_text(encoding="utf-8").strip()


_MINISTER_INSTRUCTION = _load_prompt("Shang2/Shang2_minister.txt")
_EMPEROR_INSTRUCTION = _load_prompt("Shang2/Shang2_emperor.txt")
_NORMAL_OUTPUT_REQUIREMENT = _load_prompt("normal_output_requirement.txt")


def oracle_for(
    *, seed: int, player_id: str, decision_id: str, option: str, target: object
) -> tuple[str, str]:
    """Return the deterministic omen plus its auditable derivation material.

    The omen is derived from SHA-256 over the game seed, the court player, the
    decision id and the complete suggestion signature.  It never consumes the
    engine RNG stream, so the same configuration reproduces the same omens and
    retries within one decision cannot change an already assigned omen.
    """
    selected_option: dict[str, object] = {"option": option, "target": target}
    material = json.dumps(
        [_ORACLE_VERSION, seed, player_id, decision_id, selected_option],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return _OMENS[int.from_bytes(digest[:8], "big") % len(_OMENS)], material


def _advice_signature(advice: str) -> tuple[str, object]:
    """Return the option id and response-format target of one normalized advice."""
    document = cast(dict[str, object], json.loads(advice))
    selected = cast(dict[str, object], document["selected_option"])
    return str(selected["option"]), selected.get("target")


def _with_oracle(advice: str, omen: str) -> str:
    """Attach the emperor-only oracle field to one normalized advice copy."""
    document = cast(dict[str, object], json.loads(advice))
    document["oracle"] = omen
    return json.dumps(document, ensure_ascii=False)


def _truncate(reason: str) -> str:
    return reason[:_MAX_REASON_CHARS]


@dataclass(frozen=True, slots=True)
class Shang2CallTrace:
    """One private, serializable LLM role invocation in a Shang2 consultation."""

    decision_id: str
    role: str
    caller_role: str
    outcome: str
    content: str | None = None
    error: str | None = None
    decision_maker: str | None = None
    content_type: str | None = None


class Shang2CourtAgent:
    """Implement the redesigned Shang court: three ministers, omens, one emperor.

    Completed minister stages are retained per decision id, so the runner may
    re-invoke this controller after an emperor connection or validation
    failure without repeating any minister call or changing assigned omens.
    """

    uses_llm = True

    def __init__(
        self,
        *,
        player_id: str,
        seed: int,
        minister_1_client: LLMClient,
        minister_1_profile: ModelProfile,
        minister_2_client: LLMClient,
        minister_2_profile: ModelProfile,
        minister_3_client: LLMClient,
        minister_3_profile: ModelProfile,
        emperor_client: LLMClient,
        emperor_profile: ModelProfile,
        conversations: dict[str, AgentConversation],
        validation_retries: int = 2,
        max_connection_retries: int = 2,
    ) -> None:
        self._player_id = player_id
        self._seed = seed
        self._clients = {
            MINISTER_1: minister_1_client,
            MINISTER_2: minister_2_client,
            MINISTER_3: minister_3_client,
            EMPEROR: emperor_client,
        }
        self._profiles = {
            MINISTER_1: minister_1_profile,
            MINISTER_2: minister_2_profile,
            MINISTER_3: minister_3_profile,
            EMPEROR: emperor_profile,
        }
        self._conversations = conversations
        self._validation_retries = validation_retries
        self._max_connection_retries = max_connection_retries
        self._connection_failures = {role: 0 for role in MINISTERS}
        self._decision_id: str | None = None
        self._advice: dict[str, str] = {}
        self._advice_published = False
        self._oracle_records: list[dict[str, object]] = []
        self._trace: list[Shang2CallTrace] = []
        self._last_llm_call_count = 0
        self._last_warning: ContextWarning | None = None

    @property
    def player_id(self) -> str:
        return self._player_id

    @property
    def conversation(self) -> AgentConversation:
        """Return the Emperor-only player-visible conversation."""
        return self._conversations[EMPEROR]

    @property
    def role_conversations(self) -> dict[str, AgentConversation]:
        return dict(self._conversations)

    @property
    def last_context_warning(self) -> ContextWarning | None:
        """Expose the most recent segment-3 overflow warning to the runner."""
        return self._last_warning

    @property
    def last_llm_call_count(self) -> int:
        """Return actual role calls made by the latest controller invocation."""
        return self._last_llm_call_count

    def court_trace(self) -> dict[str, object]:
        """Return the private court audit record for the latest decision."""
        return {
            "court": "shang2",
            "decision_id": self._decision_id,
            "calls": [asdict(item) for item in self._trace],
            "oracles": self._oracle_records,
        }

    def court_calls(self) -> list[dict[str, object]]:
        """Return the raw role-call trace entries for tests and reports."""
        return [asdict(item) for item in self._trace]

    def record_final_decision(self, request: DecisionRequest, reply: str) -> None:
        """Broadcast the engine-facing emperor reply to the three ministers."""
        self._deliver(request, EMPEROR, _FINAL, reply, set(MINISTERS))

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        self._prepare(request)
        self._last_llm_call_count = 0
        pending = [role for role in MINISTERS if role not in self._advice]
        if pending:
            with ThreadPoolExecutor(max_workers=len(pending)) as executor:
                futures = {role: executor.submit(self._advise, role, request) for role in pending}
                for role in pending:
                    futures[role].result()
            self._reorder_minister_traces()
            self._publish_advice(request)
        return self._call_emperor(request)

    def _prepare(self, request: DecisionRequest) -> None:
        if self._decision_id == request.decision_id:
            return
        self._decision_id = request.decision_id
        self._advice = {}
        self._advice_published = False
        self._oracle_records = []
        self._connection_failures = {role: 0 for role in MINISTERS}
        self._trace = []
        self._last_warning = None
        for conversation in self._conversations.values():
            if conversation.current_turn is None:
                conversation.start_turn(1)

    def _advise(self, role: str, request: DecisionRequest) -> None:
        """Obtain one minister's final advice, with retries and fallbacks."""
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
        except (ConnectionError, LLMCallError) as error:
            selected_option, reason = self._connection_fallback(role, request, error)
        normalized = json.dumps(
            {"selected_option": selected_option, "reason": reason}, ensure_ascii=False
        )
        self._advice[role] = normalized
        self._append_own_decision(role, request, normalized)

    def _call(self, role: str, request: DecisionRequest) -> str:
        profile = self._profiles[role]
        caller = f"{self._player_id}.{role}"
        instruction = _MINISTER_INSTRUCTION if role in MINISTERS else _EMPEROR_INSTRUCTION
        messages, warning = compose_prompt(
            self._conversations[role],
            request,
            role_instruction=instruction,
            segment3_prompt=_NORMAL_OUTPUT_REQUIREMENT,
        )
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
                Shang2CallTrace(
                    decision_id=request.decision_id,
                    role=role,
                    caller_role=caller,
                    outcome=(
                        "connection_error" if isinstance(error, ConnectionError) else "call_error"
                    ),
                    error=str(error),
                )
            )
            raise
        self._last_llm_call_count += 1
        self._trace.append(
            Shang2CallTrace(
                decision_id=request.decision_id,
                role=role,
                caller_role=caller,
                outcome="success",
                content=response.content,
                decision_maker=role,
                content_type=_ADVICE if role in MINISTERS else _FINAL,
            )
        )
        return response.content

    def _call_emperor(self, request: DecisionRequest) -> str:
        """Return the emperor's raw reply; the runner validates it upstream."""
        return self._call(EMPEROR, request)

    def _connection_fallback(
        self, role: str, request: DecisionRequest, error: Exception
    ) -> tuple[dict[str, object], str]:
        """Re-raise until the role's reconnect budget is exhausted."""
        self._connection_failures[role] += 1
        if self._connection_failures[role] <= self._max_connection_retries:
            raise error
        default = next(option for option in request.options if option.is_default)
        selected_option = default_option_json(default)
        reason = f"{_ROLE_LABELS[role]}重连次数耗尽，无法做出有效回复。"
        fallback = json.dumps(
            {"selected_option": selected_option, "reason": reason}, ensure_ascii=False
        )
        self._trace.append(
            Shang2CallTrace(
                decision_id=request.decision_id,
                role=role,
                caller_role=f"{self._player_id}.{role}",
                outcome="connection_fallback",
                content=fallback,
                decision_maker=role,
                content_type=_ADVICE,
            )
        )
        return selected_option, reason

    def _publish_advice(self, request: DecisionRequest) -> None:
        """Deduplicate suggestions, assign omens and deliver the advice copies.

        The emperor receives oracle-bearing copies; every other minister
        receives the plain copies without the ``oracle`` field.  Both variants
        share one ``internal_decision_id`` namespace suffix so retries cannot
        duplicate an already-delivered message.
        """
        if self._advice_published:
            return
        self._advice_published = True
        omens: dict[str, str] = {}
        for role in MINISTERS:
            advice = self._advice[role]
            option, target = _advice_signature(advice)
            key = json.dumps(
                {"option": option, "target": target}, ensure_ascii=False, sort_keys=True
            )
            omen = omens.get(key)
            if omen is None:
                omen, material = oracle_for(
                    seed=self._seed,
                    player_id=self._player_id,
                    decision_id=request.decision_id,
                    option=option,
                    target=target,
                )
                omens[key] = omen
                self._oracle_records.append(
                    {"option": option, "target": target, "oracle": omen, "derivation": material}
                )
            self._deliver(request, role, _ORACLE_DELIVERY, _with_oracle(advice, omen), {EMPEROR})
            peers = {other for other in MINISTERS if other != role}
            self._deliver(request, role, _ADVICE, advice, peers)

    def _reorder_minister_traces(self) -> None:
        """Order minister trace entries by role for deterministic audits."""
        minister_traces = [item for item in self._trace if item.role in MINISTERS]
        other_traces = [item for item in self._trace if item.role not in MINISTERS]
        self._trace = [
            trace for role in MINISTERS for trace in minister_traces if trace.role == role
        ] + other_traces

    def _deliver(
        self,
        request: DecisionRequest,
        role: str,
        content_type: str,
        raw_content: str,
        recipients: set[str],
    ) -> None:
        for recipient in recipients:
            self._conversations[recipient].append_internal_decision(
                internal_decision_id=f"{request.decision_id}:{role}:{content_type}",
                decision_id=request.decision_id,
                question_summary=render_decision_question(request),
                decision_maker=role,
                content_type=content_type,
                raw_content=raw_content,
            )

    def _append_own_decision(self, role: str, request: DecisionRequest, raw: str) -> None:
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
            Shang2CallTrace(
                decision_id=request.decision_id,
                role=role,
                caller_role=f"{self._player_id}.{role}",
                outcome="validation_error",
                content=raw,
                error=error,
                decision_maker=role,
                content_type=_ADVICE,
            )
        )
