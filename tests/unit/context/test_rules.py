"""Tests for the Stage 4C game-rules loader (segment 2 of the composer)."""

from __future__ import annotations

from monopoly_agent_battle.context import rules


def test_load_game_rules_returns_nonempty_markdown() -> None:
    text = rules.load_game_rules()
    assert text
    # A few known headings from doc/monopoly_rules_basic.md
    assert "游戏概述" in text
    assert "机会卡" in text


def test_load_game_rules_caches_between_calls() -> None:
    rules.reset_cache()
    first = rules.load_game_rules()
    second = rules.load_game_rules()
    assert first is second  # cache hit → same string object


def test_rules_version_constant_present() -> None:
    assert rules.GAME_RULES_VERSION == "v1"
