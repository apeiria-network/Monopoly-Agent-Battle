"""Provisional two-role Shang court controller.

The prompt wording in this module is intentionally a temporary technical
placeholder. It establishes the approved role/context boundary only and must
be manually rewritten and reviewed before being treated as final content.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from monopoly_agent_battle.config.models import ModelProfile
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.context.token_guard import ContextWarning
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.prompts import render_decision_question
from monopoly_agent_battle.llm.protocol import LLMClient, LLMMessage, LLMRequest

_GREAT_PRIEST_ROLE = "great_priest"
_EMPEROR_ROLE = "emperor"
_ORACLE_CONTENT_TYPE = "oracle"
_FINAL_DECISION_CONTENT_TYPE = "final_decision"

_PROVISIONAL_PRIEST_SYSTEM_PROMPT = """你是商代朝廷中的大祭司。以下仅为暂定技术提示词，
后续必须人工重写。你只根据本次收到的当前决策问题给皇帝写一段简短、神谕式的启示或提醒。
不得输出 JSON，不得选择或推荐任何具体候选操作，不得编造游戏状态，不得要求隐藏信息，
不得修改游戏状态，也不得声明最终决策。"""

_ORACLE_SECTION_HEADER = "## 朝廷内部神谕（仅供皇帝本次决策参考）"


@dataclass(frozen=True, slots=True)
class CourtCallTrace:
    """One private, serializable LLM role invocation in a court consultation."""

    decision_id: str
    role: str
    caller_role: str
    outcome: str
    content: str | None = None
    error: str | None = None
    decision_maker: str | None = None
    content_type: str | None = None


class ShangCourtAgent:
    """Call a Great Priest once, then let the Emperor make the final choice.

    A completed priest stage is retained per decision ID. Therefore the runner
    may re-invoke this controller after an Emperor connection failure or an
    Emperor validation failure without repeating the priest call.
    """

    uses_llm = True

    def __init__(
        self,
        *,
        player_id: str,
        great_priest_client: LLMClient,
        great_priest_profile: ModelProfile,
        emperor_client: LLMClient,
        emperor_profile: ModelProfile,
        emperor_conversation: AgentConversation,
    ) -> None:
        self._player_id = player_id
        self._great_priest_client = great_priest_client
        self._great_priest_profile = great_priest_profile
        self._emperor_client = emperor_client
        self._emperor_profile = emperor_profile
        self._emperor_conversation = emperor_conversation
        self._current_decision_id: str | None = None
        self._oracle: str | None = None
        self._trace: list[CourtCallTrace] = []
        self._last_llm_call_count = 0
        self._last_warning: ContextWarning | None = None

    @property
    def player_id(self) -> str:
        return self._player_id

    @property
    def conversation(self) -> AgentConversation:
        """Return the Emperor-only player-visible conversation."""
        return self._emperor_conversation

    @property
    def last_context_warning(self) -> ContextWarning | None:
        """Expose the Emperor's segment-3 overflow warning to the runner."""
        return self._last_warning

    @property
    def last_llm_call_count(self) -> int:
        """Return actual role calls made by the latest controller invocation."""
        return self._last_llm_call_count

    def court_trace(self) -> dict[str, object]:
        """Return the private trace accumulated for the active decision."""
        return {
            "court": "shang",
            "decision_id": self._current_decision_id,
            "calls": [asdict(entry) for entry in self._trace],
        }

    def court_calls(self) -> list[dict[str, str | None]]:
        """Return typed copies of the private role-call records for local inspection."""
        return [asdict(entry) for entry in self._trace]

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        self._prepare_decision(request.decision_id)
        self._last_llm_call_count = 0
        if self._oracle is None:
            self._oracle = self._request_oracle(request)
        return self._request_emperor(request)

    def _prepare_decision(self, decision_id: str) -> None:
        if self._current_decision_id == decision_id:
            return
        self._current_decision_id = decision_id
        self._oracle = None
        self._trace = []
        self._last_warning = None

    def _request_oracle(self, request: DecisionRequest) -> str:
        caller_role = f"{self._player_id}.great_priest"
        llm_request = LLMRequest(
            messages=(
                LLMMessage(role="system", content=_PROVISIONAL_PRIEST_SYSTEM_PROMPT),
                LLMMessage(role="user", content=render_decision_question(request)),
            ),
            model=self._great_priest_profile.model,
            caller_role=caller_role,
            seed=self._great_priest_profile.seed,
            temperature=self._great_priest_profile.temperature,
            max_tokens=self._great_priest_profile.max_tokens,
            timeout_seconds=self._great_priest_profile.timeout_seconds,
        )
        self._last_llm_call_count += 1
        try:
            response = self._great_priest_client.complete(llm_request)
        except ConnectionError as error:
            self._trace.append(
                CourtCallTrace(
                    decision_id=request.decision_id,
                    role="great_priest",
                    caller_role=caller_role,
                    outcome="connection_error",
                    error=str(error),
                )
            )
            raise
        self._trace.append(
            CourtCallTrace(
                decision_id=request.decision_id,
                role=_GREAT_PRIEST_ROLE,
                caller_role=caller_role,
                outcome="success",
                content=response.content,
                decision_maker=_GREAT_PRIEST_ROLE,
                content_type=_ORACLE_CONTENT_TYPE,
            )
        )
        self._deliver_internal_message(
            request,
            decision_maker=_GREAT_PRIEST_ROLE,
            content_type=_ORACLE_CONTENT_TYPE,
            raw_content=response.content,
        )
        return response.content

    def _request_emperor(self, request: DecisionRequest) -> str:
        assert self._oracle is not None
        messages, warning = compose_prompt(self._emperor_conversation, request)
        self._last_warning = warning
        emperor_messages = messages
        if not any(
            self._oracle in message.content for message in messages if message.role == "user"
        ):
            emperor_messages = (*messages[:-1], self._with_oracle(messages[-1], self._oracle))
        caller_role = f"{self._player_id}.emperor"
        llm_request = LLMRequest(
            messages=emperor_messages,
            model=self._emperor_profile.model,
            caller_role=caller_role,
            seed=self._emperor_profile.seed,
            temperature=self._emperor_profile.temperature,
            max_tokens=self._emperor_profile.max_tokens,
            timeout_seconds=self._emperor_profile.timeout_seconds,
        )
        self._last_llm_call_count += 1
        try:
            response = self._emperor_client.complete(llm_request)
        except ConnectionError as error:
            self._trace.append(
                CourtCallTrace(
                    decision_id=request.decision_id,
                    role="emperor",
                    caller_role=caller_role,
                    outcome="connection_error",
                    error=str(error),
                )
            )
            raise
        self._trace.append(
            CourtCallTrace(
                decision_id=request.decision_id,
                role="emperor",
                caller_role=caller_role,
                outcome="success",
                content=response.content,
                decision_maker=_EMPEROR_ROLE,
                content_type=_FINAL_DECISION_CONTENT_TYPE,
            )
        )
        return response.content

    def _deliver_internal_message(
        self,
        request: DecisionRequest,
        *,
        decision_maker: str,
        content_type: str,
        raw_content: str,
    ) -> bool:
        """Deliver a Court-AI result through the private conversation channel."""
        return self._emperor_conversation.append_internal_decision(
            internal_decision_id=f"{request.decision_id}:{decision_maker}:{content_type}",
            decision_id=request.decision_id,
            question_summary=render_decision_question(request),
            decision_maker=decision_maker,
            content_type=content_type,
            raw_content=raw_content,
        )

    @staticmethod
    def _with_oracle(message: LLMMessage, oracle: str) -> LLMMessage:
        """Append a current-decision-only oracle without altering candidates."""
        if message.role != "user":
            raise AssertionError("composed Emperor prompt must end with a user message")
        return LLMMessage(
            role="user",
            content=message.content + "\n\n" + _ORACLE_SECTION_HEADER + "\n" + oracle,
        )
