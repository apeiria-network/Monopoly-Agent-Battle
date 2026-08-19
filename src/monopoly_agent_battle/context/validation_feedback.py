"""Validation-failure feedback template for Stage 4C conversation retries.

The feedback lifecycle itself (set/clear on the AgentConversation) is managed
by the decision runner; this module owns only the user-visible template so its
wording lives next to the other context-facing text.
"""

from __future__ import annotations

_TEMPLATE = "你的上一次输出无效：{error}。请重新输出一个合法 JSON。"


def build_feedback(error: str) -> str:
    """Return the user-facing validation-failure message for a retry."""
    return _TEMPLATE.format(error=error)
