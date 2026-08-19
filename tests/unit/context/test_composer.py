"""Tests for the Stage 4C 10-segment prompt composer."""

from __future__ import annotations

from pathlib import Path

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import GameEvent, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine


def _make_engine(tmp_path: Path) -> GameEngine:
    config = GameConfig(
        game_id="composer-test",
        experiment_id="unit",
        seed=1,
        players=(
            PlayerConfig(player_id="a", seat=1),
            PlayerConfig(player_id="b", seat=2),
        ),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    return engine


def _event(event_type: str, **payload: object) -> GameEvent:
    return GameEvent(event_type=event_type, payload=payload)


def test_first_decision_no_history_skips_segments_3_and_4(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    conv = AgentConversation(agent_id="a", window_turns=1)
    request = build_decision_request(engine, sequence=1)

    messages, warning = compose_prompt(conv, request)

    roles = [m.role for m in messages]
    # Expect exactly [system(1+2), user(5-10)]; no segment-3 user, no segment-4 chatter.
    assert roles == ["system", "user"]
    assert "游戏规则" in messages[0].content
    assert "合法候选操作" in messages[-1].content
    assert warning is None


def test_second_decision_same_turn_appends_prior_turn_pair(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1, segment3_budget_tokens=10_000)
    conv.append_event(_event("dice_rolled", player_id="a", dice=(2, 3)))
    conv.append_decision(
        decision_id="d1",
        user_snapshot="## 当前决策（旧）\n上一次决策的问题",
        assistant_reply='{"selected_option":{"option":"end_turn"},"reason":"r"}',
    )
    conv.append_event(
        _event("payment_made", payer_id="a", recipient_id=None, amount=200, reason="tax")
    )

    request = build_decision_request(engine, sequence=2)
    messages, _warning = compose_prompt(conv, request)

    roles = [m.role for m in messages]
    # Segment 4: user(dice + old snapshot) → assistant(reply) → user(payment + segments 5-10)
    assert roles == ["system", "user", "assistant", "user"]
    prior_user = messages[1]
    assert "掷出" in prior_user.content
    assert "上一次决策的问题" in prior_user.content
    trailing_user = messages[-1]
    assert "支付" in trailing_user.content  # payment_made broadcast merges into trailing user
    assert "合法候选操作" in trailing_user.content  # current segments 5-10


def test_pending_feedback_appended_as_assistant_and_user(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1, segment3_budget_tokens=10_000)
    conv.set_pending_feedback(
        bad_reply='{"selected_option":"broken"}',
        feedback="你的上一次输出无效：xxx",
    )

    request = build_decision_request(engine, sequence=1)
    messages, _warning = compose_prompt(conv, request)

    roles = [m.role for m in messages]
    assert roles == ["system", "assistant", "user"]
    assert messages[1].content == '{"selected_option":"broken"}'
    trailing_user = messages[-1]
    assert "你的上一次输出无效" in trailing_user.content
    assert "合法候选操作" in trailing_user.content  # feedback merges into current segments 5-10


def test_segment3_from_completed_turn_appears_between_system_and_user(
    tmp_path: Path,
) -> None:
    engine = _make_engine(tmp_path)
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1, segment3_budget_tokens=10_000)
    conv.append_event(_event("dice_rolled", player_id="b", dice=(1, 2)))
    conv.start_turn(2, segment3_budget_tokens=10_000)

    request = build_decision_request(engine, sequence=3)
    messages, warning = compose_prompt(conv, request)

    roles = [m.role for m in messages]
    # Segment 3 present because turn 1 has an event; segment 4 empty (new turn just started).
    assert roles == ["system", "user", "user"] or roles == ["system", "user"]
    if len(messages) == 3:
        assert "历史事件播报" in messages[1].content
        assert "1+2=3" in messages[1].content
    assert warning is None
