"""Unit tests for prompt composer."""

from __future__ import annotations

from monopoly_agent_battle.context.composer import (
    _compose_broadcast_history,  # pyright: ignore[reportPrivateUsage]
    _compose_conversation_history,  # pyright: ignore[reportPrivateUsage]
    _compose_current_segments,  # pyright: ignore[reportPrivateUsage]
    _compose_system_segment,  # pyright: ignore[reportPrivateUsage]
    compose_prompt,
)
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import (
    DecisionKind,
    DecisionOption,
    DecisionRequest,
)
from monopoly_agent_battle.domain.models import GameEvent


def _make_minimal_request(player_id: str = "player-1") -> DecisionRequest:
    """Create a minimal DecisionRequest for testing."""
    return DecisionRequest(
        decision_id="decision-1",
        game_id="game-1",
        complete_rounds=1,
        player_id=player_id,
        phase="asset_management",
        kind=DecisionKind.ASSET_MANAGEMENT,
        question="选择操作",
        visible_state={
            "turn": {
                "complete_rounds": 1,
                "current_player_id": player_id,
            },
            "your_state": {
                "player_id": player_id,
                "seat": 1,
                "cash": 1500,
                "position": 0,
                "chance_cards": [],
                "community_get_out_of_jail_cards": [],
                "property_positions": [],
                "jail_status": "free",
                "jail_roll_attempts": 0,
            },
            "players": [],
            "board": [
                {
                    "position": 0,
                    "name": "GO",
                    "kind": "go",
                    "color_group": None,
                    "owner_id": None,
                    "building_level": 0,
                    "price": None,
                    "building_cost": None,
                    "rents": [],
                    "mortgaged": False,
                }
            ],
            "ongoing_effects": [],
        },
        options=(
            DecisionOption(
                option_id="end_turn",
                command_type="EndTurn",
                parameters={"player_id": player_id},
                title="结束回合",
                preview="结束本回合",
                response_format={"option": "{option_id}"},
                is_default=True,
            ),
        ),
        output_constraints={},
    )


def test_compose_system_segment() -> None:
    """Test composing system instruction and game rules."""
    content = _compose_system_segment("player-1", 1)

    # Should contain player instruction
    assert "player-1" in content
    assert "座位 1" in content
    assert "目标" in content

    # Should contain game rules
    assert "游戏规则" in content
    assert "大富翁游戏规则" in content
    assert "回合流程" in content


def test_compose_current_segments() -> None:
    """Test composing current state and decision segments."""
    request = _make_minimal_request()
    content = _compose_current_segments(request, validation_feedback=None)

    # Should contain all required sections
    assert "## 当前局面" in content
    assert "## 当前决策" in content
    assert "## 合法候选操作" in content
    assert "## 输出要求" in content

    # Should contain state information
    assert "现金：1500" in content
    assert "位置" in content


def test_compose_current_segments_with_feedback() -> None:
    """Test that validation feedback is appended."""
    request = _make_minimal_request()
    feedback = "选项编号无效"
    content = _compose_current_segments(request, validation_feedback=feedback)

    assert "## 上次输出反馈" in content
    assert "选项编号无效" in content


def test_compose_broadcast_history_empty() -> None:
    """Test broadcast history with no events."""
    conversation = AgentConversation(agent_id="player-1")
    history = _compose_broadcast_history(conversation)

    assert history == ""


def test_compose_broadcast_history_outside_window() -> None:
    """Test broadcast history renders events outside window."""
    conversation = AgentConversation(agent_id="player-1", window_turns=2)

    # Add events for rounds 1, 2, 3
    for round_num in range(1, 4):
        events = [
            GameEvent(
                event_type="dice_rolled",
                payload={"player_id": "player-1", "dice": (3, 4)},
            )
        ]
        conversation.add_round_events(round_num, events)

    # Add action turns 1, 2, 3 (last 2 are in window)
    conversation.add_decision_request(1, 1, "Request 1", "d1")
    conversation.add_decision_request(2, 2, "Request 2", "d2")
    conversation.add_decision_request(3, 3, "Request 3", "d3")

    history = _compose_broadcast_history(conversation)

    # Should only contain round 1 (outside window)
    assert "[第1轮]" in history
    # Rounds 2 and 3 should not be in broadcast (they're in window)
    assert "[第2轮]" not in history
    assert "[第3轮]" not in history


def test_compose_conversation_history_empty() -> None:
    """Test conversation history with no messages."""
    conversation = AgentConversation(agent_id="player-1")
    messages = _compose_conversation_history(conversation)

    assert len(messages) == 0


def test_compose_conversation_history_within_window() -> None:
    """Test conversation history renders messages within window."""
    conversation = AgentConversation(agent_id="player-1", window_turns=3)

    # Add 3 turns of conversation
    for turn in range(1, 4):
        conversation.add_decision_request(turn, turn, f"Request {turn}", f"d{turn}")
        conversation.add_decision_response(f"d{turn}", f"Response {turn}")

    messages = _compose_conversation_history(conversation)

    # Should have 6 messages (3 turns × 2 messages each)
    assert len(messages) == 6

    # Check structure
    assert messages[0].role == "user"
    assert "### 回合 1" in messages[0].content
    assert messages[1].role == "assistant"


def test_compose_prompt_no_history() -> None:
    """Test composing prompt with no conversation history."""
    conversation = AgentConversation(agent_id="player-1")
    request = _make_minimal_request()

    messages = compose_prompt(conversation, request)

    # Should have system + current decision only (no history segments)
    assert len(messages) == 2
    assert messages[0].role == "system"
    assert messages[1].role == "user"

    # System should contain rules
    assert "游戏规则" in messages[0].content

    # User message should contain current state
    assert "当前局面" in messages[1].content


def test_compose_prompt_with_history() -> None:
    """Test composing prompt with conversation history."""
    conversation = AgentConversation(agent_id="player-1", window_turns=2)

    # Add some history
    conversation.add_decision_request(1, 1, "Request 1", "d1")
    conversation.add_decision_response("d1", "Response 1")
    conversation.add_decision_request(2, 2, "Request 2", "d2")
    conversation.add_decision_response("d2", "Response 2")

    # Add events
    events = [
        GameEvent(
            event_type="dice_rolled",
            payload={"player_id": "player-1", "dice": (3, 4)},
        )
    ]
    conversation.add_round_events(1, events)

    request = _make_minimal_request()
    messages = compose_prompt(conversation, request)

    # Should have: system + broadcast_history_user + ack + conv_history + current
    # system, (broadcast_user, ack), window_messages, current_user
    assert messages[0].role == "system"

    # Should have history segments
    has_broadcast = any("历史事件播报" in msg.content for msg in messages if msg.role == "user")
    has_conversation = any("### 回合" in msg.content for msg in messages if msg.role == "user")

    # At least one history segment should be present
    assert has_broadcast or has_conversation


def test_compose_prompt_with_validation_feedback() -> None:
    """Test that validation feedback is included in final message."""
    conversation = AgentConversation(agent_id="player-1")
    request = _make_minimal_request()
    feedback = "选项编号无效"

    messages = compose_prompt(conversation, request, validation_feedback=feedback)

    # Last message should contain feedback
    last_message = messages[-1]
    assert last_message.role == "user"
    assert "上次输出反馈" in last_message.content
    assert "选项编号无效" in last_message.content


def test_compose_prompt_with_token_cap() -> None:
    """Test that token cap is applied and history is trimmed."""
    conversation = AgentConversation(agent_id="player-1", window_turns=3)

    # Add large history
    for turn in range(1, 11):
        conversation.add_decision_request(turn, turn, f"Request {turn}" * 100, f"d{turn}")
        conversation.add_decision_response(f"d{turn}", f"Response {turn}" * 100)

        events = [
            GameEvent(
                event_type="dice_rolled",
                payload={"player_id": "player-1", "dice": (i, i + 1)},
            )
            for i in range(10)
        ]
        conversation.add_round_events(turn, events)

    request = _make_minimal_request()

    # Compose with large enough cap to fit protected segments but trim history
    # Protected segments (system + current_state) ~= 3600 tokens from previous error
    messages = compose_prompt(conversation, request, token_cap=5000)

    # Should still have system and current decision (protected)
    assert messages[0].role == "system"
    assert messages[-1].role == "user"
    assert "当前局面" in messages[-1].content

    # History should be trimmed compared to no cap
    messages_no_cap = compose_prompt(conversation, request, token_cap=None)

    # With cap should have fewer messages or shorter content
    total_with_cap = sum(len(msg.content) for msg in messages)
    total_no_cap = sum(len(msg.content) for msg in messages_no_cap)
    assert total_with_cap < total_no_cap


def test_compose_prompt_token_cap_preserves_protected() -> None:
    """Test that token cap never trims protected segments."""
    conversation = AgentConversation(agent_id="player-1")
    request = _make_minimal_request()

    # Even with very tight cap (but still enough for protected segments)
    # Protected segments need ~3635 tokens, so use 4000
    messages = compose_prompt(conversation, request, token_cap=4000)

    # System and current state should still be present
    assert any(msg.role == "system" for msg in messages)
    assert any("当前局面" in msg.content for msg in messages if msg.role == "user")


def test_compose_broadcast_skips_window_rounds() -> None:
    """Test that broadcast history skips rounds that overlap with window."""
    conversation = AgentConversation(agent_id="player-1", window_turns=2)

    # Add events for rounds 1, 2, 3
    for round_num in range(1, 4):
        events = [
            GameEvent(
                event_type="turn_started",
                payload={"player_id": "player-1"},
            )
        ]
        conversation.add_round_events(round_num, events)

    # Add turns that map to rounds (turn N in round N for simplicity)
    conversation.add_decision_request(1, 1, "R1", "d1")
    conversation.add_decision_request(2, 2, "R2", "d2")
    conversation.add_decision_request(3, 3, "R3", "d3")

    # Window boundary is turn 2 (last 2 turns: 2, 3)
    # So rounds 2 and 3 should be skipped in broadcast

    history = _compose_broadcast_history(conversation)

    # Only round 1 should appear
    assert "[第1轮]" in history
    assert "[第2轮]" not in history
    assert "[第3轮]" not in history


def test_compose_conversation_preserves_turn_markers() -> None:
    """Test that conversation history preserves turn structure."""
    conversation = AgentConversation(agent_id="player-1")

    conversation.add_decision_request(5, 3, "Request at turn 5", "d5")
    conversation.add_decision_response("d5", "Response at turn 5")

    messages = _compose_conversation_history(conversation)

    # First message should have turn marker
    assert messages[0].role == "user"
    assert "### 回合 5" in messages[0].content

    # Second message should not duplicate marker
    assert messages[1].role == "assistant"
    assert "### 回合" not in messages[1].content
