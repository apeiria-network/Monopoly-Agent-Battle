"""Tests for the Stage 4C token estimator and segment-3 event truncator."""

from __future__ import annotations

from monopoly_agent_battle.context.token_guard import (
    estimate_tokens,
    truncate_events_to_budget,
)


def test_estimate_tokens_empty_returns_zero() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_pure_chinese_one_per_char() -> None:
    assert estimate_tokens("大富翁") == 3


def test_estimate_tokens_pure_english_rounds_up() -> None:
    # 4 ASCII chars → ceil(4/4) = 1 token
    assert estimate_tokens("abcd") == 1
    # 5 ASCII chars → ceil(5/4) = 2 tokens
    assert estimate_tokens("abcde") == 2


def test_estimate_tokens_mixed() -> None:
    # 3 Chinese chars + 4 ASCII = 3 + 1 = 4
    assert estimate_tokens("大富翁abcd") == 4


def test_truncate_events_within_budget_returns_all() -> None:
    events = ("玩家a掷出3+4=7点。", "玩家a移动到第7格。")
    kept, warning = truncate_events_to_budget(events, budget_tokens=1000)
    assert kept == events
    assert warning is None


def test_truncate_events_drops_earliest_first() -> None:
    events = ("玩家a掷出3+4=7点。", "玩家a移动到第7格。", "玩家b掷出1+2=3点。")
    # Force a tight budget so at least one earliest event must be dropped.
    # Each sentence ~ 12-14 tokens; budget=20 keeps at most one.
    kept, warning = truncate_events_to_budget(events, budget_tokens=20)
    # The last event is always tail; the earliest is the one dropped.
    assert kept[-1] == events[-1]
    assert events[0] not in kept
    assert warning is not None
    assert warning.kind == "segment3_overflow"


def test_truncate_events_zero_budget_emits_warning() -> None:
    events = ("玩家a掷出3+4=7点。",)
    kept, warning = truncate_events_to_budget(events, budget_tokens=0)
    assert kept == ()
    assert warning is not None
    assert warning.kind == "segment3_overflow"


def test_truncate_events_empty_input_no_warning() -> None:
    kept, warning = truncate_events_to_budget((), budget_tokens=0)
    assert kept == ()
    assert warning is None


def test_truncate_events_single_tail_over_budget_is_dropped() -> None:
    # The cap is strict: one oversized event must not overflow segment 3.
    events = ("这" * 100,)
    kept, warning = truncate_events_to_budget(events, budget_tokens=20)
    assert kept == ()
    assert warning is not None
    assert warning.kind == "segment3_overflow"
