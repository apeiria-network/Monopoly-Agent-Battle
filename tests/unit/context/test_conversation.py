"""Tests for the Stage 4C AgentConversation history model."""

from __future__ import annotations

import pytest

from monopoly_agent_battle.context.conversation import (
    AgentConversation,
    DecisionEntry,
    EventEntry,
    InternalDecisionEntry,
)
from monopoly_agent_battle.context.token_guard import estimate_tokens
from monopoly_agent_battle.domain.models import GameEvent


def _event(event_type: str, **payload: object) -> GameEvent:
    return GameEvent(event_type=event_type, payload=payload)


def test_start_turn_finalises_prior_turn_and_creates_new() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1)
    conv.append_event(_event("dice_rolled", player_id="a", dice=(3, 4)))
    conv.start_turn(2)

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
        conv.append_decision(decision_id="d1", question_summary="Q1", assistant_reply="reply")


def test_entries_preserve_time_order() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1)
    conv.append_event(_event("dice_rolled", player_id="a", dice=(2, 3)))
    conv.append_decision(decision_id="d1", question_summary="Q1", assistant_reply="A1")
    conv.append_event(
        _event("payment_made", payer_id="a", recipient_id=None, amount=10, reason="tax")
    )
    conv.append_decision(decision_id="d2", question_summary="Q2", assistant_reply="A2")

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
    conv.start_turn(1)
    # A whitelisted event that renders identically for observer/self:
    conv.append_event(_event("dice_rolled", player_id="a", dice=(2, 3)))
    conv.append_event(_event("dice_rolled", player_id="b", dice=(4, 5)))
    conv.start_turn(2)

    sentences = conv.segment3_sentences
    assert len(sentences) == 2
    assert any("2+3=5" in s for s in sentences)
    assert any("4+5=9" in s for s in sentences)
    assert conv.segment3_warning is None


def test_segment3_cache_strictly_caps_history_at_500_tokens_and_keeps_tail() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1)
    for _ in range(50):
        conv.append_event(_event("dice_rolled", player_id="a", dice=(6, 6)))
    conv.start_turn(2)

    sentences = conv.segment3_sentences
    assert len(sentences) < 50
    assert sentences[-1].endswith("玩家a掷出6+6=12点。")
    assert estimate_tokens("\n".join(sentences)) <= 500
    assert conv.segment3_warning is not None
    assert conv.segment3_warning.kind == "segment3_overflow"


def test_segment3_cache_remains_stable_within_an_action_turn() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1)
    for _ in range(50):
        conv.append_event(_event("dice_rolled", player_id="a", dice=(6, 6)))
    conv.start_turn(2)
    before = conv.segment3_sentences

    conv.append_event(_event("dice_rolled", player_id="a", dice=(1, 2)))

    assert conv.segment3_sentences == before


def test_append_error_records_in_current_turn_only() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1)
    conv.append_error(
        decision_id="d1",
        question_summary="Q1",
        bad_reply="bad-json",
        feedback_text="try again",
    )

    assert conv.current_turn is not None
    entry = conv.current_turn.entries[-1]
    from monopoly_agent_battle.context.conversation import ErrorEntry

    assert isinstance(entry, ErrorEntry)
    assert entry.decision_id == "d1"
    assert entry.question_summary == "Q1"
    assert entry.bad_reply == "bad-json"
    assert entry.feedback_text == "try again"


def test_append_error_before_start_turn_is_ignored() -> None:
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.append_error(decision_id="d1", question_summary="Q1", bad_reply="bad", feedback_text="fb")
    assert conv.current_turn is None
    assert conv.completed_turns == []


def test_internal_decision_is_private_and_idempotent() -> None:
    receiver = AgentConversation(agent_id="court", window_turns=1)
    other_receiver = AgentConversation(agent_id="other", window_turns=1)
    receiver.start_turn(1)
    other_receiver.start_turn(1)

    kwargs = {
        "internal_decision_id": "d1:chancellor:proposal",
        "decision_id": "d1",
        "question_summary": "## 当前决策\n处理当前事务。",
        "decision_maker": "chancellor",
        "content_type": "proposal",
        "raw_content": '{"reason":"建议如此"}',
    }
    assert receiver.append_internal_decision(**kwargs) is True
    assert receiver.append_internal_decision(**kwargs) is False

    assert receiver.current_turn is not None
    assert len(receiver.current_turn.entries) == 1
    entry = receiver.current_turn.entries[0]
    assert isinstance(entry, InternalDecisionEntry)
    assert entry.decision_maker == "chancellor"
    assert entry.content_type == "proposal"
    assert other_receiver.current_turn is not None
    assert other_receiver.current_turn.entries == []


def test_internal_decision_idempotency_survives_turn_boundary() -> None:
    conv = AgentConversation(agent_id="court", window_turns=1)
    conv.start_turn(1)
    kwargs = {
        "internal_decision_id": "d1:reviewer:review",
        "decision_id": "d1",
        "question_summary": "Q1",
        "decision_maker": "reviewer",
        "content_type": "review",
        "raw_content": "意见",
    }
    assert conv.append_internal_decision(**kwargs) is True
    conv.start_turn(2)

    assert conv.append_internal_decision(**kwargs) is False


def test_segment3_skips_error_entries_from_completed_turns() -> None:
    """Errors from prior turns must never leak into the compressed history."""
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1)
    conv.append_event(_event("dice_rolled", player_id="a", dice=(3, 4)))
    conv.append_error(decision_id="d1", question_summary="Q1", bad_reply="bad", feedback_text="fb")
    conv.start_turn(2)

    sentences = conv.segment3_sentences
    assert len(sentences) == 1
    assert "3+4=7" in sentences[0]
    assert all("bad" not in s and "fb" not in s for s in sentences)
