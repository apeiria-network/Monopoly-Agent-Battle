"""DeepSeek chat-completions client with thinking-mode assembly."""

from __future__ import annotations

from typing import Any

from monopoly_agent_battle.llm.openai_compatible_client import OpenAICompatibleClient


class DeepSeekClient(OpenAICompatibleClient):
    """DeepSeek endpoint: thinking mode with a fixed low reasoning effort.

    DeepSeek accepts ``thinking: {"type": "enabled"}`` and additionally
    honors a top-level ``reasoning_effort`` selector (``low``/``high``/
    ``max``).  Projects currently pin ``low`` to keep thinking budgets
    bounded so that thinking tokens plus the visible reply stay well
    inside ``max_tokens``; when thinking is disabled neither field is
    sent.
    """

    provider_name = "deepseek"
    error_label = "DeepSeek endpoint"

    def _apply_vendor_parameters(self, payload: dict[str, Any]) -> None:
        super()._apply_vendor_parameters(payload)
        if self._thinking:
            payload["reasoning_effort"] = "low"
