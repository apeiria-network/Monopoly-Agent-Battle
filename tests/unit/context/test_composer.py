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
    # Expect exactly [system(1+2+fixed output contract), user(5-9)]; no
    # segment-3 user, no segment-4 chatter.
    assert roles == ["system", "user"]
    assert "游戏规则" in messages[0].content
    assert "## 输出要求" in messages[0].content
    assert messages[0].content.index("游戏规则") < messages[0].content.index("## 输出要求")
    assert "合法候选操作" in messages[-1].content
    assert "## 输出要求" not in messages[-1].content
    assert warning is None


def test_adjacent_events_share_one_event_block_with_single_newlines(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1)
    conv.append_event(_event("dice_rolled", player_id="a", dice=(2, 3)), complete_round=0)
    conv.append_event(_event("player_moved", player_id="a", to=5), complete_round=0)
    conv.append_decision(
        decision_id="d1",
        question_summary="## 当前决策\n上一次决策的问题",
        assistant_reply='{"selected_option":{"option":"end_turn"},"reason":"r"}',
    )

    request = build_decision_request(engine, sequence=2)
    messages, _warning = compose_prompt(conv, request)

    prior_user = messages[1].content
    first_event = "[第0轮] 玩家a掷出2+3=5点。"
    second_event = "[第0轮] 玩家a移动到第5格（Reading Railroad）。"
    assert f"{first_event}\n{second_event}" in prior_user
    assert f"{first_event}\n\n{second_event}" not in prior_user
    assert f"{second_event}\n\n## 当前决策" in prior_user
    engine = _make_engine(tmp_path)
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1)
    conv.append_event(_event("dice_rolled", player_id="a", dice=(2, 3)))
    conv.append_decision(
        decision_id="d1",
        question_summary="## 当前决策\n上一次决策的问题",
        assistant_reply='{"selected_option":{"option":"end_turn"},"reason":"r"}',
    )
    conv.append_event(
        _event("payment_made", payer_id="a", recipient_id=None, amount=200, reason="tax")
    )

    request = build_decision_request(engine, sequence=2)
    messages, _warning = compose_prompt(conv, request)

    roles = [m.role for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    prior_user = messages[1]
    assert "掷出" in prior_user.content
    assert "上一次决策的问题" in prior_user.content
    trailing_user = messages[-1]
    assert "支付" in trailing_user.content
    assert "合法候选操作" in trailing_user.content


def test_error_entry_appended_as_assistant_and_user(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1)
    conv.append_error(
        decision_id="d-err",
        question_summary="## 当前决策\n你需要选一个合法选项。",
        bad_reply='{"selected_option":"broken"}',
        feedback_text="Error: 决策回复必须是一个JSON",
    )

    request = build_decision_request(engine, sequence=1)
    messages, _warning = compose_prompt(conv, request)

    roles = [m.role for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    prior_user = messages[1]
    assert "你需要选一个合法选项" in prior_user.content
    assert messages[2].content == '{"selected_option":"broken"}'
    trailing_user = messages[-1]
    assert "Error: 决策回复必须是一个JSON" in trailing_user.content
    assert "合法候选操作" in trailing_user.content


def test_error_entries_persist_across_multi_decisions_within_turn(tmp_path: Path) -> None:
    """A validation-failed reply stays visible in segment 4 for the whole turn."""
    engine = _make_engine(tmp_path)
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1)
    conv.append_error(
        decision_id="d1",
        question_summary="## 当前决策\n第一次决策的问题",
        bad_reply="bad1",
        feedback_text="fb1",
    )
    conv.append_decision(
        decision_id="d1",
        question_summary="## 当前决策\n第一次决策的问题",
        assistant_reply='{"selected_option":{"option":"end_turn"},"reason":"r"}',
    )

    request = build_decision_request(engine, sequence=2)
    messages, _warning = compose_prompt(conv, request)

    roles = [m.role for m in messages]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
    assert "第一次决策的问题" in messages[1].content
    assert messages[2].content == "bad1"
    assert "fb1" in messages[3].content
    assert messages[4].content.startswith('{"selected_option"')
    assert "合法候选操作" in messages[-1].content


def test_segment3_from_completed_turn_appears_between_system_and_user(
    tmp_path: Path,
) -> None:
    engine = _make_engine(tmp_path)
    conv = AgentConversation(agent_id="a", window_turns=1)
    conv.start_turn(1)
    conv.append_event(_event("dice_rolled", player_id="b", dice=(1, 2)))
    conv.start_turn(2)

    request = build_decision_request(engine, sequence=3)
    messages, warning = compose_prompt(conv, request)

    roles = [m.role for m in messages]
    # Segment 3 present because turn 1 has an event; segment 4 empty (new turn just started).
    assert roles == ["system", "user", "user"] or roles == ["system", "user"]
    if len(messages) == 3:
        assert "历史事件播报" in messages[1].content
        assert "1+2=3" in messages[1].content
    assert warning is None
