"""Single-model baseline agent that answers decisions through an LLM client.

Stage 4C: the agent renders the current decision through the 10-segment
``compose_prompt`` composer using an ``AgentConversation`` held by the runner.
Validation-failure feedback is managed on the conversation by the runner; the
agent itself is stateless and only maps ``(request, conversation) → LLM call``.
"""

from __future__ import annotations

from monopoly_agent_battle.config.models import ModelProfile
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.context.token_guard import ContextWarning
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.llm.protocol import LLMClient, LLMRequest


class BaselineAgent:
    """A stateless single-model player controller driven by an LLM client.

    Holds a reference to its ``AgentConversation`` (the runner keeps the same
    reference for turn-boundary and feedback management). On each call the
    agent composes the full 10-segment prompt, dispatches it to the LLM, and
    returns the raw text response. Any segment-3 overflow warning surfaced by
    the composer is exposed for the runner to log to ``runtime.jsonl``.
    """

    uses_llm = True

    def __init__(
        self,
        *,
        player_id: str,
        client: LLMClient,
        profile: ModelProfile,
        conversation: AgentConversation,
    ) -> None:
        self._player_id = player_id
        self._client = client
        self._profile = profile
        self._conversation = conversation
        self._last_warning: ContextWarning | None = None

    @property
    def player_id(self) -> str:
        return self._player_id

    @property
    def conversation(self) -> AgentConversation:
        return self._conversation

    @property
    def last_context_warning(self) -> ContextWarning | None:
        """Segment-3 overflow advisory (if any) from the most recent call."""
        return self._last_warning

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        messages, warning = compose_prompt(self._conversation, request)
        self._last_warning = warning
        response = self._client.complete(
            LLMRequest(
                messages=messages,
                model=self._profile.model,
                caller_role=self._player_id,
                seed=self._profile.seed,
                temperature=self._profile.temperature,
                max_tokens=self._profile.max_tokens,
                timeout_seconds=self._profile.timeout_seconds,
                decision_request=request,
            )
        )
        return response.content
