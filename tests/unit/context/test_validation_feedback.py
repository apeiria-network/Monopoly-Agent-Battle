"""Unit tests for validation feedback formatting."""

from __future__ import annotations

from monopoly_agent_battle.context.validation_feedback import format_validation_feedback


def test_format_validation_feedback_first_attempt() -> None:
    """Test formatting feedback for the first retry attempt."""
    feedback = format_validation_feedback(1, "选项编号无效")

    assert "第 1 次重试" in feedback
    assert "选项编号无效" in feedback
    assert "⚠️" in feedback
    assert "请重新检查" in feedback


def test_format_validation_feedback_second_attempt() -> None:
    """Test formatting feedback for the second retry attempt."""
    feedback = format_validation_feedback(2, "参数缺失：target_position")

    assert "第 2 次重试" in feedback
    assert "参数缺失：target_position" in feedback


def test_format_validation_feedback_contains_structure() -> None:
    """Test that feedback contains expected structure."""
    feedback = format_validation_feedback(1, "测试错误")

    # Should contain warning symbol
    assert "⚠️" in feedback
    # Should contain retry count
    assert "第 1 次重试" in feedback
    # Should contain error message
    assert "测试错误" in feedback
    # Should contain guidance
    assert "请重新检查合法候选项列表" in feedback
    assert "选择有效的 option_id 和参数" in feedback


def test_format_validation_feedback_multiline_error() -> None:
    """Test formatting with a multiline error message."""
    error = "JSON 解析失败：\n  期望 }, 实际 ]"
    feedback = format_validation_feedback(1, error)

    assert error in feedback
    assert "第 1 次重试" in feedback
