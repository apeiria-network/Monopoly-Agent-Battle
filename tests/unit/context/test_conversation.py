"""Tests for the Stage 4C AgentConversation history model."""

from __future__ import annotations

import pytest

from monopoly_agent_battle.context.conversation import (
    AgentConversation,
    DecisionEntry,
    EventEntry,
)
from monopoly_agent_battle.domain.models import GameEvent


def _event(event_type: str, **payload: object) -> GameEvent:
    return GameEvent(event_type=event_type, payload=payload)


def test_start_turn_finalises_prior_turn_and_creates_new() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1, segment3_budget_tokens=10_000)
    conv.append_event(_event("dice_rolled", player_id="a", dice=(3, 4)))
    conv.start_turn(2, segment3_budget_tokens=10_000)

    assert len(conv.completed_turns) == 1
    assert conv.completed_turns[0].turn_num == 1
    assert conv.current_turn is not None
    assert conv.current_turn.turn_num == 2
    assert conv.current_turn.entries == []


def test_append_event_before_start_turn_is_ignored() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.append_event(_event("dice_rolled", player_id="a", dice=(1, 2)))
    # No current_turn → silently dropped, no crash.
    assert conv.current_turn is None
    assert conv.completed_turns == []


def test_append_decision_requires_active_turn() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    with pytest.raises(RuntimeError, match="before start_turn"):
        conv.append_decision(decision_id="d1", user_snapshot="snap", assistant_reply="reply")


def test_entries_preserve_time_order() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1, segment3_budget_tokens=10_000)
    conv.append_event(_event("dice_rolled", player_id="a", dice=(2, 3)))
    conv.append_decision(decision_id="d1", user_snapshot="Q1", assistant_reply="A1")
    conv.append_event(
        _event("payment_made", payer_id="a", recipient_id=None, amount=10, reason="tax")
    )
    conv.append_decision(decision_id="d2", user_snapshot="Q2", assistant_reply="A2")

    assert conv.current_turn is not None
    entries = conv.current_turn.entries
    assert isinstance(entries[0], EventEntry)
    assert isinstance(entries[1], DecisionEntry)
    assert isinstance(entries[2], EventEntry)
    assert isinstance(entries[3], DecisionEntry)
    assert entries[1].decision_id == "d1"
    assert entries[3].decision_id == "d2"


def test_segment3_cache_renders_completed_turn_events_with_viewer_scope() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1, segment3_budget_tokens=10_000)
    # A whitelisted event that renders identically for observer/self:
    conv.append_event(_event("dice_rolled", player_id="a", dice=(2, 3)))
    conv.append_event(_event("dice_rolled", player_id="b", dice=(4, 5)))
    conv.start_turn(2, segment3_budget_tokens=10_000)

    sentences = conv.segment3_sentences
    assert len(sentences) == 2
    assert any("2+3=5" in s for s in sentences)
    assert any("4+5=9" in s for s in sentences)
    assert conv.segment3_warning is None


def test_segment3_cache_applies_budget_and_emits_warning() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1, segment3_budget_tokens=10_000)
    for _ in range(5):
        conv.append_event(_event("dice_rolled", player_id="a", dice=(6, 6)))
    conv.start_turn(2, segment3_budget_tokens=15)  # forcefully small

    # At most 1 sentence fits under 15 tokens (each is ~13 chars ≥ 13 tokens).
    assert len(conv.segment3_sentences) < 5
    assert conv.segment3_warning is None or conv.segment3_warning.kind == "segment3_overflow"


def test_pending_feedback_lifecycle() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.set_pending_feedback(bad_reply="bad-json", feedback="try again")
    assert conv.pending_bad_reply == "bad-json"
    assert conv.pending_feedback == "try again"

    conv.clear_pending_feedback()
    assert conv.pending_bad_reply is None
    assert conv.pending_feedback is None
