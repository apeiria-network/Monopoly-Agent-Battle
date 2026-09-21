"""DeepSeek chat-completions client with thinking-mode assembly."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from monopoly_agent_battle.llm.openai_compatible_client import OpenAICompatibleClient
from monopoly_agent_battle.llm.protocol import LLMRequest, LLMResponse

# Outbound model-name aliases: frozen game configs keep the short name, while
# the endpoint serves the deployment under a versioned name. The client
# rewrites only the outbound request model, so existing configs switch to the
# versioned deployment with no edits.
_MODEL_NAME_ALIASES: dict[str, str] = {
    "deepseek-v4-flash": "deepseek-v4-flash-0731",
}


class DeepSeekClient(OpenAICompatibleClient):
    """DeepSeek endpoint: thinking mode with a fixed low reasoning effort.

    DeepSeek accepts ``thinking: {"type": "enabled"}`` and additionally
    honors a top-level ``reasoning_effort`` selector (``low``/``high``/
    ``max``).  Projects currently pin ``low`` to keep thinking budgets
    bounded so that thinking tokens plus the visible reply stay well
    inside ``max_tokens``; when thinking is disabled neither field is
    sent. Outbound model names are translated via ``_MODEL_NAME_ALIASES``.
    """

    provider_name = "deepseek"
    error_label = "DeepSeek endpoint"

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Rewrite an aliased model name, then send via the shared client."""
        aliased = _MODEL_NAME_ALIASES.get(request.model)
        if aliased is not None:
            request = replace(request, model=aliased)
        return super().complete(request)

    def _apply_vendor_parameters(self, payload: dict[str, Any]) -> None:
        super()._apply_vendor_parameters(payload)
        if self._thinking:
            payload["reasoning_effort"] = "low"
