"""GPT chat-completions client using the reasoning-effort interface."""

from __future__ import annotations

from typing import Any

from monopoly_agent_battle.llm.openai_compatible_client import OpenAICompatibleClient


class GptClient(OpenAICompatibleClient):
    """GPT endpoint: thinking mode is expressed as a reasoning effort.

    gpt-5.6-luna expects ``reasoning: {"effort": "low"}`` when thinking mode
    is requested, instead of the ``thinking`` toggle used by other vendors.
    """

    provider_name = "gpt"
    error_label = "GPT endpoint"

    def _apply_vendor_parameters(self, payload: dict[str, Any]) -> None:
        if self._thinking:
            payload["reasoning"] = {"effort": "low"}
