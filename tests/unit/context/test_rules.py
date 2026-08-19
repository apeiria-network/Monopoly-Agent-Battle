"""Unit tests for game rules loading."""

from __future__ import annotations

from monopoly_agent_battle.context.rules import load_game_rules


def test_load_game_rules() -> None:
    """Test loading game rules from monopoly_rules_basic.md."""
    rules = load_game_rules()

    # Should not be empty
    assert len(rules) > 0

    # Should contain key sections from the rules
    assert "大富翁游戏规则" in rules
    assert "游戏概述" in rules
    assert "回合流程" in rules
    assert "棋盘格子效果" in rules

    # Should be markdown format
    assert "#" in rules  # Header markers


def test_load_game_rules_cached() -> None:
    """Test that load_game_rules uses LRU cache."""
    rules1 = load_game_rules()
    rules2 = load_game_rules()

    # Should return the same object (cached)
    assert rules1 is rules2


def test_load_game_rules_contains_essential_info() -> None:
    """Test that rules contain essential game information."""
    rules = load_game_rules()

    # Check for essential game mechanics
    assert "1500" in rules  # Initial cash
    assert "GO" in rules or "起点" in rules
    assert "监狱" in rules
    assert "机会" in rules
    assert "社区基金" in rules or "公益基金" in rules
