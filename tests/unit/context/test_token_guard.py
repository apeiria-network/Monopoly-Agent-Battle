"""Unit tests for token estimation and budget protection."""

from __future__ import annotations

import pytest

from monopoly_agent_battle.context.token_guard import (
    TokenLimitExceededError,
    apply_token_limit,
    estimate_tokens,
)


def test_estimate_tokens_empty() -> None:
    """Test estimating tokens for empty string."""
    assert estimate_tokens("") == 0


def test_estimate_tokens_english_only() -> None:
    """Test estimating tokens for English text."""
    # "Hello world" = 11 chars × 0.3 = 3.3 → 3 tokens
    tokens = estimate_tokens("Hello world")
    assert tokens == 3


def test_estimate_tokens_chinese_only() -> None:
    """Test estimating tokens for Chinese text."""
    # "你好世界" = 4 chars × 1.5 = 6 tokens
    tokens = estimate_tokens("你好世界")
    assert tokens == 6


def test_estimate_tokens_mixed() -> None:
    """Test estimating tokens for mixed Chinese and English."""
    # "玩家 player-1 获得 200 资金"
    # Chinese: 玩家获得资金 = 6 chars × 1.5 = 9
    # Other: " player-1  200 " = 15 chars × 0.3 = 4.5
    # Total = 13.5 → 13
    tokens = estimate_tokens("玩家 player-1 获得 200 资金")
    assert 12 <= tokens <= 14  # Allow small variation


def test_estimate_tokens_common_cjk_range() -> None:
    """Test that common CJK characters are counted correctly."""
    # Test characters from different parts of CJK range
    assert estimate_tokens("一") == 1  # U+4E00 (start)
    assert estimate_tokens("龥") == 1  # Near end
    assert estimate_tokens("中文测试") == 6  # 4 chars × 1.5


def test_apply_token_limit_under_budget() -> None:
    """Test that segments under budget are returned unchanged."""
    segments = {
        "system": "Short system prompt",
        "rules": "Short rules",
        "current_state": "Current state",
        "current_decision": "Decision",
    }

    result = apply_token_limit(segments, token_cap=1000)
    assert result == segments


def test_apply_token_limit_protected_exceeds() -> None:
    """Test that exceeding budget with protected segments raises error."""
    segments = {
        "system": "A" * 1000,  # ~300 tokens
        "rules": "B" * 1000,  # ~300 tokens
        "current_state": "C" * 1000,  # ~300 tokens
        "current_decision": "D" * 1000,  # ~300 tokens
    }

    with pytest.raises(
        TokenLimitExceededError,
        match="Protected segments require .* tokens",
    ):
        apply_token_limit(segments, token_cap=100)


def test_apply_token_limit_trims_broadcast_history() -> None:
    """Test that broadcast_history is trimmed when over budget."""
    segments = {
        "system": "S" * 100,
        "rules": "R" * 100,
        "broadcast_history": "[第1轮]\n事件1\n[第2轮]\n事件2\n[第3轮]\n事件3"
        * 10,  # Make it larger
        "current_state": "State",
        "current_decision": "Decision",
    }

    # Set a tight budget that requires trimming
    result = apply_token_limit(segments, token_cap=100)

    # broadcast_history should be trimmed or removed
    original_len = len(segments["broadcast_history"])
    result_len = len(result.get("broadcast_history", ""))
    assert result_len < original_len  # Should be trimmed or removed

    # Protected segments should remain
    assert result["system"] == segments["system"]
    assert result["rules"] == segments["rules"]


def test_apply_token_limit_removes_broadcast_if_needed() -> None:
    """Test that broadcast_history is removed entirely if no rounds fit."""
    segments = {
        "system": "S" * 100,
        "rules": "R" * 100,
        "broadcast_history": "[第1轮]\n" + "事件" * 500,  # Very large
        "current_state": "State",
        "current_decision": "Decision",
    }

    result = apply_token_limit(segments, token_cap=80)

    # broadcast_history should be removed entirely
    assert "broadcast_history" not in result


def test_apply_token_limit_trims_conversation_history() -> None:
    """Test that conversation_history is trimmed when over budget."""
    segments = {
        "system": "S" * 100,
        "rules": "R" * 100,
        "conversation_history": ("### 回合 1\n内容1\n### 回合 2\n内容2\n### 回合 3\n内容3" * 10),
        "current_state": "State",
        "current_decision": "Decision",
    }

    result = apply_token_limit(segments, token_cap=100)

    # conversation_history should be trimmed or removed
    original_len = len(segments["conversation_history"])
    result_len = len(result.get("conversation_history", ""))
    assert result_len < original_len


def test_apply_token_limit_trims_both_histories() -> None:
    """Test that both histories can be trimmed in sequence."""
    segments = {
        "system": "S" * 50,
        "rules": "R" * 50,
        "broadcast_history": "[第1轮]\n事件1\n[第2轮]\n事件2\n[第3轮]\n事件3",
        "conversation_history": "### 回合 1\n内容1\n### 回合 2\n内容2",
        "current_state": "State",
        "current_decision": "Decision",
    }

    result = apply_token_limit(segments, token_cap=50)

    # At least one history should be trimmed
    broadcast_trimmed = "broadcast_history" not in result or len(
        result.get("broadcast_history", "")
    ) < len(segments["broadcast_history"])
    conversation_trimmed = "conversation_history" not in result or len(
        result.get("conversation_history", "")
    ) < len(segments["conversation_history"])

    assert broadcast_trimmed or conversation_trimmed


def test_apply_token_limit_preserves_latest_rounds() -> None:
    """Test that trimming preserves the most recent rounds."""
    segments = {
        "system": "System",
        "rules": "Rules",
        "broadcast_history": "[第1轮]\n事件A\n[第2轮]\n事件B\n[第3轮]\n事件C",
        "current_state": "State",
        "current_decision": "Decision",
    }

    result = apply_token_limit(segments, token_cap=50)

    # If broadcast_history exists, it should contain later rounds
    if "broadcast_history" in result:
        content = result["broadcast_history"]
        # Should keep round 3 if any round is kept
        if "[第" in content:
            assert "第3轮" in content or "第2轮" in content


def test_apply_token_limit_preserves_latest_turns() -> None:
    """Test that trimming preserves the most recent turns."""
    segments = {
        "system": "System",
        "rules": "Rules",
        "conversation_history": "### 回合 1\n内容A\n### 回合 2\n内容B\n### 回合 3\n内容C",
        "current_state": "State",
        "current_decision": "Decision",
    }

    result = apply_token_limit(segments, token_cap=50)

    # If conversation_history exists, it should contain later turns
    if "conversation_history" in result:
        content = result["conversation_history"]
        # Should keep turn 3 if any turn is kept
        if "回合" in content:
            assert "回合 3" in content or "回合 2" in content


def test_estimate_tokens_realistic_prompt() -> None:
    """Test token estimation on a realistic prompt segment."""
    text = """
## 你的状态

- 玩家ID：player-1
- 现金：1500
- 位置：第0格（GO）
- 监狱状态：自由
- 持有卡牌：无
    """

    tokens = estimate_tokens(text)
    # Should be roughly 30-50 tokens for this short segment
    assert 20 <= tokens <= 60


def test_apply_token_limit_custom_protected() -> None:
    """Test using custom protected segments."""
    segments = {
        "segment_a": "A" * 100,
        "segment_b": "B" * 100,
        "segment_c": "C" * 100,
    }

    # Protect only segment_a
    result = apply_token_limit(
        segments,
        token_cap=50,
        protected_segments=frozenset({"segment_a"}),
    )

    # segment_a should remain, others may be removed/trimmed
    assert result["segment_a"] == segments["segment_a"]


def test_apply_token_limit_no_histories() -> None:
    """Test that function works when history segments are absent."""
    segments = {
        "system": "System",
        "rules": "Rules",
        "current_state": "State",
        "current_decision": "Decision",
    }

    result = apply_token_limit(segments, token_cap=1000)
    assert result == segments


def test_broadcast_history_trimming_keeps_structure() -> None:
    """Test that broadcast history trimming preserves round structure."""
    segments = {
        "system": "S",
        "rules": "R",
        "broadcast_history": (
            "[第1轮]\n玩家A掷骰\n玩家B掷骰\n"
            "[第2轮]\n玩家C掷骰\n玩家D掷骰\n"
            "[第3轮]\n玩家A购地\n玩家B购地"
        ),
        "current_state": "State",
        "current_decision": "Decision",
    }

    result = apply_token_limit(segments, token_cap=40)

    # If any broadcast_history remains, it should have round markers
    if "broadcast_history" in result and result["broadcast_history"]:
        assert "[第" in result["broadcast_history"]
        assert "轮]" in result["broadcast_history"]
