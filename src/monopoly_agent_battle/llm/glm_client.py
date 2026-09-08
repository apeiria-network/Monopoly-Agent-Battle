"""GLM (Zhipu) chat-completions client with thinking-mode assembly."""

from __future__ import annotations

from typing import Any

from monopoly_agent_battle.llm.openai_compatible_client import OpenAICompatibleClient


class GlmClient(OpenAICompatibleClient):
    """GLM endpoint: thinking can only be opted into, not disabled.

    glm-5.3-flash accepts ``thinking: {"type": "enabled"}`` together with a
    fixed ``reasoning_effort: "low"`` when thinking mode is requested; no
    disable form is currently supported, so a disabled profile sends neither
    field and the server default applies.
    """

    provider_name = "glm"
    error_label = "GLM endpoint"

    def _apply_vendor_parameters(self, payload: dict[str, Any]) -> None:
        if self._thinking:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = "low"
