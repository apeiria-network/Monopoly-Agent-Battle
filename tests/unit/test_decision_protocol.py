import json
from pathlib import Path
from typing import cast

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.prompts import render_decision_prompt
from monopoly_agent_battle.decision.protocol import parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request, player_visible_state
from monopoly_agent_battle.domain.commands import EndTurn
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine


def make_engine(tmp_path: Path) -> GameEngine:
    config = GameConfig(
        game_id="decision-game",
        experiment_id="decision-experiment",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )
    return GameEngine(config)


def test_roll_request_exposes_only_player_private_cards(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].chance_cards.append("chance-waiver")
    engine.state.players["b"].chance_cards.append("chance-nuclear")

    visible = player_visible_state(engine, "a")

    private_state = cast(dict[str, object], visible["your_private_state"])
    players = cast(list[dict[str, object]], visible["players"])
    assert private_state["chance_cards"] == [{"card_id": "chance-waiver", "name": "免费卡"}]
    assert players[1]["chance_card_count"] == 1
    serialized = json.dumps(visible, ensure_ascii=False)
    assert "chance-nuclear" not in serialized
    assert "chance_draw_pile" not in serialized
    assert "community_chest_draw_pile" not in serialized


def test_asset_request_only_lists_engine_legal_options(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.buildable_position = 1
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)

    request = build_decision_request(engine, 1)

    assert request.phase == "asset_management"
    assert {option.command_type for option in request.options} == {"build", "mortgage", "end_turn"}
    assert next(
        option for option in request.options if option.command_type == "end_turn"
    ).is_default


def test_response_requires_one_known_option_and_reason(tmp_path: Path) -> None:
    request = build_decision_request(make_engine(tmp_path), 1)
    valid = parse_and_validate(
        json.dumps({"selected_option": "roll_dice", "reasoning": "继续掷骰推进回合。"}), request
    )
    illegal = parse_and_validate(
        json.dumps({"selected_option": "build-1", "reasoning": "越权操作。"}), request
    )
    altered = parse_and_validate(
        json.dumps(
            {
                "selected_option": "roll_dice",
                "parameters": {"position": 1},
                "reasoning": "篡改参数。",
            }
        ),
        request,
    )

    assert valid.valid
    assert not illegal.valid
    assert not altered.valid


def test_prompt_contains_request_and_fixed_response_contract(tmp_path: Path) -> None:
    prompt = render_decision_prompt(build_decision_request(make_engine(tmp_path), 1))

    assert "## 决策上下文" in prompt
    assert "## 可见游戏状态" in prompt
    assert "## 合法候选操作" in prompt
    assert '"selected_option": "<合法候选项的 option_id>"' in prompt
    assert "chance_draw_pile" not in prompt


def test_turn_complete_request_can_only_end_turn(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.TURN_COMPLETE

    request = build_decision_request(engine, 1)

    assert len(request.options) == 1
    assert request.options[0].command_type == "end_turn"
    assert request.options[0].parameters == {}
    assert isinstance(EndTurn("a"), EndTurn)
