"""Kimi (Moonshot) chat-completions client with an explicit thinking toggle."""

from __future__ import annotations

from typing import Any

from monopoly_agent_battle.llm.openai_compatible_client import OpenAICompatibleClient


class KimiClient(OpenAICompatibleClient):
    """Kimi endpoint: the thinking switch must always be sent explicitly.

    kimi-k2.6 defaults to server-side thinking, so disabling requires
    ``thinking: {"type": "disabled"}`` rather than omitting the field.
    """

    provider_name = "kimi"
    error_label = "Kimi endpoint"

    def _apply_vendor_parameters(self, payload: dict[str, Any]) -> None:
        payload["thinking"] = {"type": "enabled" if self._thinking else "disabled"}
