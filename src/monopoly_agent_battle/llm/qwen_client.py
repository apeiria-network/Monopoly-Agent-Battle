"""Qwen (Alibaba DashScope compatible mode) chat-completions client."""

from __future__ import annotations

from typing import Any

from monopoly_agent_battle.llm.openai_compatible_client import OpenAICompatibleClient


class QwenClient(OpenAICompatibleClient):
    """Qwen endpoint: the thinking switch must always be sent explicitly.

    Qwen models in DashScope compatible mode take a top-level boolean
    ``enable_thinking``; the server-side default differs across model
    generations, so both on and off are stated explicitly rather than
    relying on the endpoint default. Requesting thinking mode also pins
    ``reasoning_effort: "low"`` per the vendor reference payload.
    """

    provider_name = "qwen"
    error_label = "Qwen endpoint"

    def _apply_vendor_parameters(self, payload: dict[str, Any]) -> None:
        payload["enable_thinking"] = self._thinking
        if self._thinking:
            payload["reasoning_effort"] = "low"
