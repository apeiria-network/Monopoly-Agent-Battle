"""Single-model baseline agent that answers decisions through an LLM client."""

from __future__ import annotations

from collections.abc import Callable

from monopoly_agent_battle.config.models import ModelProfile
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.prompts import render_decision_prompt
from monopoly_agent_battle.llm.protocol import LLMClient, LLMMessage, LLMRequest

PromptRenderer = Callable[[DecisionRequest], str]

_VALIDATION_FEEDBACK = "\n\n## 上次输出反馈\n你的上一次输出无效：{error}。请重新输出一个合法 JSON。"


class BaselineAgent:
    """A stateless single-model player controller driven by an LLM client.

    Receives the same player-visible context and decision candidates as the
    accepted Stage 3 prompt; a transient validation-failure feedback section may
    be appended for retries (A1). The agent never mutates game state.
    """

    def __init__(
        self,
        *,
        player_id: str,
        client: LLMClient,
        profile: ModelProfile,
        prompt_renderer: PromptRenderer = render_decision_prompt,
    ) -> None:
        self._player_id = player_id
        self._client = client
        self._profile = profile
        self._prompt_renderer = prompt_renderer

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        prompt = self._prompt_renderer(request)
        if feedback is not None:
            prompt += _VALIDATION_FEEDBACK.format(error=feedback)
        response = self._client.complete(
            LLMRequest(
                messages=(LLMMessage(role="user", content=prompt),),
                model=self._profile.model,
                caller_role=self._player_id,
                temperature=self._profile.temperature,
                max_tokens=self._profile.max_tokens,
                timeout_seconds=self._profile.timeout_seconds,
            )
        )
        return response.content
