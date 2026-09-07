"""Tests for the Stage 4C validation-failure feedback templates."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.validation_feedback import build_feedback
from monopoly_agent_battle.decision.models import (
    DecisionOption,
    DecisionRequest,
    DecisionValidation,
    OptionTarget,
)
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


def _manual_validation(option: DecisionOption, raw_response: str) -> DecisionValidation:
    return DecisionValidation(
        response=None,
        option=option,
        error="target value is not legal for this option",
        raw_response=raw_response,
        target=None,
        error_category="invalid_target",
    )


def _option(
    kind: str, fields: tuple[str, ...], legal_values: tuple[tuple[object, ...], ...]
) -> DecisionOption:
    return DecisionOption(
        option_id="test-option",
        command_type="use_chance_card",
        parameters={},
        title="测试选项",
        preview="预览",
        response_format={},
        target=OptionTarget(
            kind=kind,
            fields=fields,
            command_fields=fields,
            legal_values=legal_values,
        ),
    )


def test_not_json_template(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    request = build_decision_request(engine, sequence=1)
    validation = parse_and_validate("not-json-at-all", request)
    assert validation.error_category == "not_json"
    assert build_feedback(validation, request) == "Error: 决策回复必须是一个JSON"


def test_missing_reason_template(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    request = build_decision_request(engine, sequence=1)
    validation = parse_and_validate('{"selected_option": {"option": "end_turn"}}', request)
    assert validation.error_category == "missing_reason"
    assert (
        build_feedback(validation, request)
        == "Error: 回复缺少必填的 reason 字段，reason 必须是字符串"
    )


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
    assert feedback.startswith("Error: 不合法的选项id。当前决策的合法范围为: [")
    for option in request.options:
        assert f'"{option.option_id}"' in feedback


def test_missing_target_template_lists_legal_values(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    request = build_decision_request(engine, sequence=1)
    # ``mortgage`` requires a target; omit it to trigger missing_target.
    mortgage_option = next(o for o in request.options if o.option_id == "mortgage")
    _ = mortgage_option  # sanity check
    validation = parse_and_validate(
        '{"selected_option": {"option": "mortgage"}, "reason": "缺目标"}', request
    )
    assert validation.error_category == "missing_target"
    assert (
        build_feedback(validation, request)
        == "Error: 未设定决策目标。本选项必须提供 target，合法值：position ∈ {1}。"
    )


def test_invalid_target_template_lists_legal_values_per_field(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    request = build_decision_request(engine, sequence=1)
    validation = parse_and_validate(
        '{"selected_option": {"option": "mortgage", "target": 999}, "reason": "非法目标"}',
        request,
    )
    assert validation.error_category == "invalid_target"
    assert (
        build_feedback(validation, request)
        == "Error: 错误的目标选择：你给出的 target「999」不合法。"
        "本选项的合法 target 值：position ∈ {1}，请从中重新选择。"
    )


def test_invalid_target_quotes_string_legal_values() -> None:
    option = _option(
        "player",
        ("target_player_id",),
        (("ming-court",), ("baseline-2",), ("baseline-3",)),
    )
    validation = _manual_validation(
        option,
        '{"selected_option": {"option": "test-option", "target": "baseline-5"}, "reason": "x"}',
    )
    request = cast(DecisionRequest, None)
    assert build_feedback(validation, request) == (
        "Error: 错误的目标选择：你给出的 target「baseline-5」不合法。"
        '本选项的合法 target 值：target_player_id ∈ {"ming-court", "baseline-2", "baseline-3"}，'
        "请从中重新选择。"
    )


def test_invalid_target_renders_pair_fields() -> None:
    option = _option(
        "position_pair",
        ("swap_in_position", "swap_out_position"),
        ((1, 16), (3, 27), (11, 16)),
    )
    validation = _manual_validation(
        option,
        '{"selected_option": {"option": "test-option", '
        '"target": {"swap_in_position": 5, "swap_out_position": 12}}, "reason": "x"}',
    )
    request = cast(DecisionRequest, None)
    chosen_echo = '{"swap_in_position": 5, "swap_out_position": 12}'
    assert build_feedback(validation, request) == (
        f"Error: 错误的目标选择：你给出的 target「{chosen_echo}」不合法。"
        "本选项的合法 target 值：swap_in_position ∈ {1, 3, 11}，swap_out_position ∈ {16, 27}，"
        "请从中重新选择。"
    )


def test_invalid_target_unparseable_reply_uses_format_template() -> None:
    option = _option("position", ("position",), ((1,),))
    validation = _manual_validation(option, "```json\n{broken")
    request = cast(DecisionRequest, None)
    assert build_feedback(validation, request) == (
        "Error: 错误的目标选择：target 格式不合法。"
        "本选项的合法 target 值：position ∈ {1}，请从中重新选择。"
    )
