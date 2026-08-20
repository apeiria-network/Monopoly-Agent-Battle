"""Tests for the Stage 4C validation-failure feedback templates."""

from __future__ import annotations

from pathlib import Path

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.validation_feedback import build_feedback
from monopoly_agent_battle.decision.protocol import parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine


def _make_engine(tmp_path: Path) -> GameEngine:
    config = GameConfig(
        game_id="feedback-test",
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
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    return engine


def test_not_json_template(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    request = build_decision_request(engine, sequence=1)
    validation = parse_and_validate("not-json-at-all", request)
    assert validation.error_category == "not_json"
    assert build_feedback(validation, request) == "Error: 决策回复必须是一个JSON"


def test_missing_option_template(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    request = build_decision_request(engine, sequence=1)
    validation = parse_and_validate(
        '{"selected_option": {"target": 1}, "reason": "缺 option"}', request
    )
    assert validation.error_category == "missing_option"
    assert build_feedback(validation, request) == "Error: 未设定决策选项id"


def test_invalid_option_template_lists_all_candidates(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    request = build_decision_request(engine, sequence=1)
    validation = parse_and_validate(
        '{"selected_option": {"option": "not-a-real-option"}, "reason": "x"}', request
    )
    assert validation.error_category == "invalid_option"
    feedback = build_feedback(validation, request)
    assert feedback.startswith("Error: 不合法的选项id。当前决策的合法范围为: ")
    for option in request.options:
        assert option.option_id in feedback


def test_missing_target_template(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    request = build_decision_request(engine, sequence=1)
    # ``mortgage`` requires a target; omit it to trigger missing_target.
    mortgage_option = next(o for o in request.options if o.option_id == "mortgage")
    _ = mortgage_option  # sanity check
    validation = parse_and_validate(
        '{"selected_option": {"option": "mortgage"}, "reason": "缺目标"}', request
    )
    assert validation.error_category == "missing_target"
    assert build_feedback(validation, request) == "Error: 未设定决策目标"


def test_invalid_target_template_lists_legal_values_per_field(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    request = build_decision_request(engine, sequence=1)
    validation = parse_and_validate(
        '{"selected_option": {"option": "mortgage", "target": 999}, "reason": "非法目标"}',
        request,
    )
    assert validation.error_category == "invalid_target"
    feedback = build_feedback(validation, request)
    assert feedback.startswith("Error: 错误的目标选择。目标字段结构为：")
    # Single-target: the field name and its full legal list appear.
    assert "position" in feedback
    assert "1" in feedback  # 1 is a legal mortgage target
    # No misleading single "example" — the specific illegal value (999) is not echoed.
    assert "999" not in feedback
