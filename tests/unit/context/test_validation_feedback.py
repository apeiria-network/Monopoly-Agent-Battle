"""Tests for the Stage 4C validation-failure feedback template."""

from __future__ import annotations

from monopoly_agent_battle.context.validation_feedback import build_feedback


def test_build_feedback_wraps_error_in_template() -> None:
    text = build_feedback("response is not valid JSON")
    assert "response is not valid JSON" in text
    assert text.startswith("你的上一次输出无效")
    assert "请重新输出一个合法 JSON" in text
