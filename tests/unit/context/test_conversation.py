"""Unit tests for AgentConversation and ConversationMessage."""

from __future__ import annotations

import pytest

from monopoly_agent_battle.context.conversation import AgentConversation, ConversationMessage
from monopoly_agent_battle.domain.models import GameEvent


def test_conversation_message_creation() -> None:
    """Test creating a conversation message."""
    msg = ConversationMessage(
        role="user",
        content="Test content",
        turn=1,
        round_num=1,
        decision_id="decision-1",
    )
    assert msg.role == "user"
    assert msg.content == "Test content"
    assert msg.turn == 1
    assert msg.round_num == 1
    assert msg.decision_id == "decision-1"


def test_ai_conversation_empty() -> None:
    """Test creating an empty AI conversation."""
    conv = AgentConversation(agent_id="player-1")
    assert conv.agent_id == "player-1"
    assert conv.window_turns == 3
    assert conv.broadcast_history_turns == 10
    assert len(conv.messages) == 0
    assert len(conv.action_turns) == 0
    assert len(conv.round_events) == 0


def test_window_boundary_empty_history() -> None:
    """Test window boundary with no history."""
    conv = AgentConversation(agent_id="player-1")
    assert conv.get_window_boundary() == 0


def test_window_boundary_within_window() -> None:
    """Test window boundary when all turns are within window."""
    conv = AgentConversation(agent_id="player-1", window_turns=3)
    conv.action_turns = [1, 2]
    assert conv.get_window_boundary() == 0


def test_window_boundary_exactly_window() -> None:
    """Test window boundary with exactly window_turns turns."""
    conv = AgentConversation(agent_id="player-1", window_turns=3)
    conv.action_turns = [1, 2, 3]
    assert conv.get_window_boundary() == 0


def test_window_boundary_exceeds_window() -> None:
    """Test window boundary when history exceeds window."""
    conv = AgentConversation(agent_id="player-1", window_turns=3)
    conv.action_turns = [1, 2, 3, 4, 5]
    # Window should start at turn 3 (last 3 turns are 3, 4, 5)
    assert conv.get_window_boundary() == 3


def test_is_within_window() -> None:
    """Test checking if a turn is within window."""
    conv = AgentConversation(agent_id="player-1", window_turns=3)
    conv.action_turns = [1, 2, 3, 4, 5]

    assert not conv.is_within_window(1)
    assert not conv.is_within_window(2)
    assert conv.is_within_window(3)
    assert conv.is_within_window(4)
    assert conv.is_within_window(5)


def test_add_decision_request() -> None:
    """Test adding a decision request message."""
    conv = AgentConversation(agent_id="player-1")
    conv.add_decision_request(
        turn=1,
        round_num=1,
        content="Choose an action",
        decision_id="decision-1",
    )

    assert len(conv.messages) == 1
    assert conv.messages[0].role == "user"
    assert conv.messages[0].content == "Choose an action"
    assert conv.messages[0].turn == 1
    assert conv.messages[0].round_num == 1
    assert conv.messages[0].decision_id == "decision-1"
    assert conv.action_turns == [1]


def test_add_decision_response() -> None:
    """Test adding a decision response message."""
    conv = AgentConversation(agent_id="player-1")
    conv.add_decision_request(
        turn=1,
        round_num=1,
        content="Choose an action",
        decision_id="decision-1",
    )
    conv.add_decision_response(
        decision_id="decision-1",
        reasoning="I choose to roll dice",
    )

    assert len(conv.messages) == 2
    assert conv.messages[1].role == "assistant"
    assert conv.messages[1].content == "I choose to roll dice"
    assert conv.messages[1].turn == 1
    assert conv.messages[1].round_num == 1
    assert conv.messages[1].decision_id == "decision-1"


def test_add_decision_response_no_request() -> None:
    """Test adding a response without a corresponding request raises error."""
    conv = AgentConversation(agent_id="player-1")
    with pytest.raises(ValueError, match="Cannot find request for decision_id"):
        conv.add_decision_response(
            decision_id="nonexistent",
            reasoning="Invalid response",
        )


def test_get_messages_in_window() -> None:
    """Test getting messages within the window."""
    conv = AgentConversation(agent_id="player-1", window_turns=2)
    conv.add_decision_request(1, 1, "Request 1", "d1")
    conv.add_decision_response("d1", "Response 1")
    conv.add_decision_request(2, 2, "Request 2", "d2")
    conv.add_decision_response("d2", "Response 2")
    conv.add_decision_request(3, 3, "Request 3", "d3")
    conv.add_decision_response("d3", "Response 3")

    # Window boundary is turn 2 (last 2 turns: 2, 3)
    in_window = conv.get_messages_in_window()
    assert len(in_window) == 4  # 2 turns × 2 messages each
    assert all(msg.turn >= 2 for msg in in_window)


def test_get_messages_outside_window() -> None:
    """Test getting messages outside the window."""
    conv = AgentConversation(agent_id="player-1", window_turns=2)
    conv.add_decision_request(1, 1, "Request 1", "d1")
    conv.add_decision_response("d1", "Response 1")
    conv.add_decision_request(2, 2, "Request 2", "d2")
    conv.add_decision_response("d2", "Response 2")
    conv.add_decision_request(3, 3, "Request 3", "d3")
    conv.add_decision_response("d3", "Response 3")

    # Window boundary is turn 2
    outside_window = conv.get_messages_outside_window()
    assert len(outside_window) == 2  # Turn 1 messages
    assert all(msg.turn < 2 for msg in outside_window)


def test_add_round_events() -> None:
    """Test recording events for a round."""
    conv = AgentConversation(agent_id="player-1")
    event1 = GameEvent(event_type="dice_rolled", payload={"dice": (3, 4)})
    event2 = GameEvent(event_type="player_moved", payload={"to": 7})

    conv.add_round_events(1, [event1, event2])

    assert 1 in conv.round_events
    assert len(conv.round_events[1]) == 2
    assert conv.round_events[1][0].event_type == "dice_rolled"
    assert conv.round_events[1][1].event_type == "player_moved"


def test_add_round_events_multiple_calls() -> None:
    """Test adding events to the same round multiple times."""
    conv = AgentConversation(agent_id="player-1")
    event1 = GameEvent(event_type="dice_rolled", payload={"dice": (3, 4)})
    event2 = GameEvent(event_type="player_moved", payload={"to": 7})

    conv.add_round_events(1, [event1])
    conv.add_round_events(1, [event2])

    assert len(conv.round_events[1]) == 2


def test_get_broadcast_rounds_empty() -> None:
    """Test getting broadcast rounds when no events recorded."""
    conv = AgentConversation(agent_id="player-1")
    assert conv.get_broadcast_rounds() == []


def test_get_broadcast_rounds_within_limit() -> None:
    """Test getting broadcast rounds when history is within limit."""
    conv = AgentConversation(agent_id="player-1", broadcast_history_turns=10)
    for round_num in range(1, 6):
        conv.add_round_events(round_num, [])

    rounds = conv.get_broadcast_rounds()
    assert rounds == [1, 2, 3, 4, 5]


def test_get_broadcast_rounds_exceeds_limit() -> None:
    """Test getting broadcast rounds when history exceeds limit."""
    conv = AgentConversation(agent_id="player-1", broadcast_history_turns=3)
    for round_num in range(1, 6):
        conv.add_round_events(round_num, [])

    rounds = conv.get_broadcast_rounds()
    # Should return last 3 rounds
    assert rounds == [3, 4, 5]


def test_multiple_action_turns_same_turn_number() -> None:
    """Test that duplicate turn numbers are not added to action_turns."""
    conv = AgentConversation(agent_id="player-1")
    conv.add_decision_request(1, 1, "Request 1", "d1")
    conv.add_decision_request(1, 1, "Request 1b", "d1b")

    # Should only record turn 1 once
    assert conv.action_turns == [1]
