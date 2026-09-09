"""GPT chat-completions client using the reasoning-effort interface."""

from __future__ import annotations

from typing import Any

from monopoly_agent_battle.llm.openai_compatible_client import OpenAICompatibleClient


class GptClient(OpenAICompatibleClient):
    """GPT endpoint: thinking mode is expressed as a reasoning effort.

    gpt-5.6-luna rejects the ``thinking`` toggle used by other vendors and the
    object form ``reasoning: {"effort": ...}``; thinking mode is requested with
    the string field ``reasoning_effort: "low"`` instead. The endpoint also
    requires ``max_completion_tokens`` instead of ``max_tokens``.
    """

    provider_name = "gpt"
    error_label = "GPT endpoint"
    max_tokens_field = "max_completion_tokens"

    def _apply_vendor_parameters(self, payload: dict[str, Any]) -> None:
        if self._thinking:
            payload["reasoning_effort"] = "low"
