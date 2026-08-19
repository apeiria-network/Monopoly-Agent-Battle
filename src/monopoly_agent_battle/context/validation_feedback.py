"""Format validation error feedback for AI retries (Stage 4C)."""

from __future__ import annotations


def format_validation_feedback(attempt: int, error_message: str) -> str:
    """Format validation error as Chinese feedback for the AI to retry.

    Args:
        attempt: The retry attempt number (1-indexed)
        error_message: The validation error message from parse_and_validate

    Returns:
        Formatted Chinese feedback string to append to the user message
    """
    return (
        f"⚠️ 上次输出存在问题（第 {attempt} 次重试）：\n"
        f"- {error_message}\n"
        f"\n"
        f"请重新检查合法候选项列表，选择有效的 option_id 和参数。"
    )
