import json
from pathlib import Path
from typing import cast

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.prompts import render_decision_prompt
from monopoly_agent_battle.decision.protocol import parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request, player_visible_state
from monopoly_agent_battle.domain.commands import SelectStolenChanceCard, UseChanceCard
from monopoly_agent_battle.domain.models import JailStatus, TurnPhase
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
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)

    request = build_decision_request(engine, 1)

    assert request.phase == "asset_management"
    assert {option.command_type for option in request.options} == {"mortgage", "end_turn"}
    assert next(
        option for option in request.options if option.command_type == "end_turn"
    ).is_default


def test_response_requires_one_known_option_and_reason(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].jail_status = JailStatus.ROLLING
    request = build_decision_request(engine, 1)
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
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "## 决策上下文" in prompt
    assert "## 可见游戏状态" in prompt
    assert "## 合法候选操作" in prompt
    assert '"selected_option": "<合法候选项的 option_id>"' in prompt
    assert "decision-game" not in prompt
    assert "decision-" not in prompt
    assert "chance_draw_pile" not in prompt


def test_jail_request_keeps_dice_as_a_choice(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    player = engine.state.players["a"]
    player.jail_status = JailStatus.ROLLING
    player.cash = 100
    player.community_get_out_of_jail_cards.append("community-jail-free")

    request = build_decision_request(engine, 1)

    assert request.kind.value == "jail"
    assert {option.command_type for option in request.options} == {
        "roll_dice",
        "pay_jail_fine",
        "use_community_get_out_of_jail_card",
    }


def test_normal_rolling_and_turn_complete_do_not_build_requests(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)

    try:
        build_decision_request(engine, 1)
    except RuntimeError as error:
        assert "automatic flow" in str(error)
    else:
        raise AssertionError("normal rolling must not create a decision request")

    engine.state.turn_phase = TurnPhase.TURN_COMPLETE
    try:
        build_decision_request(engine, 1)
    except RuntimeError as error:
        assert "automatic flow" in str(error)
    else:
        raise AssertionError("turn completion must not create a decision request")


def test_payment_context_omits_internal_operation_id(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].cash = 0
    engine.state.properties[5].owner_id = "a"
    engine.state.players["a"].properties.add(5)
    events = []
    engine._queue_payment(  # pyright: ignore[reportPrivateUsage]
        engine.state.players["a"],
        1,
        None,
        "test_payment",
        TurnPhase.ASSET_MANAGEMENT,
        None,
        events,
    )
    engine._drain_settlement_operations(events)  # pyright: ignore[reportPrivateUsage]

    request = build_decision_request(engine, 1)
    payment_due = cast(dict[str, object], request.visible_state["payment_due"])

    assert request.kind.value == "payment_resolution"
    assert payment_due == {"amount": 1, "reason": "test_payment", "recipient_id": None}
    assert "operation_id" not in json.dumps(request.visible_state)


def test_theft_selection_reveals_target_cards_only_during_selection(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.players["a"].chance_cards.append("chance-steal")
    engine.state.players["b"].chance_cards.extend(["chance-build", "chance-tax"])
    engine.random.randint = lambda _low, _high: 4  # type: ignore[method-assign]

    engine.execute(UseChanceCard("a", "chance-steal", target_player_id="b"))
    request = build_decision_request(engine, 1)
    serialized = json.dumps(request.visible_state, ensure_ascii=False)

    assert request.kind.value == "theft_card_selection"
    assert request.visible_state["theft_selection"] == {
        "target_player_id": "b",
        "target_chance_cards": [
            {"card_id": "chance-build", "name": "建房卡"},
            {"card_id": "chance-tax", "name": "查税卡"},
        ],
    }
    assert {option.command_type for option in request.options} == {"select_stolen_chance_card"}
    assert "chance-build" in serialized

    engine.execute(SelectStolenChanceCard("a", "chance-build"))
    normal_state = player_visible_state(engine, "a")

    assert "theft_selection" not in normal_state
    assert "chance-tax" not in json.dumps(normal_state, ensure_ascii=False)


def test_option_previews_describe_visible_immediate_effects(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    engine.state.players["a"].chance_cards.append("chance-jail")

    request = build_decision_request(engine, 1)
    by_type = {option.command_type: option for option in request.options}

    assert by_type["mortgage"].effect_preview["cash_change"] == 60
    chance_option = next(
        option
        for option in request.options
        if option.command_type == "use_chance_card" and option.parameters["target_player_id"] == "b"
    )
    assert chance_option.effect_preview == {
        "card_name": "陷害卡",
        "effect": "jail_player",
        "target_player_id": "b",
        "target_name": "b",
        "target_position": 0,
    }
    assert "目标为 b" in chance_option.summary
    assert by_type["end_turn"].effect_preview["chance_card_count"] == 1
