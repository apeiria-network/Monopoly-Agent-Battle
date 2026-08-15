import json
from pathlib import Path
from typing import cast

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.prompts import render_decision_prompt
from monopoly_agent_battle.decision.protocol import command_from_option, parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request, player_visible_state
from monopoly_agent_battle.domain.commands import (
    Mortgage,
    RollDice,
    SelectStolenChanceCard,
    UseChanceCard,
)
from monopoly_agent_battle.domain.models import (
    GameEvent,
    JailStatus,
    OngoingEffect,
    OngoingEffectKind,
    TurnPhase,
)
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


def test_visible_state_separates_private_cards_and_property_details(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].chance_cards.append("chance-waiver")
    engine.state.players["b"].chance_cards.append("chance-nuclear")
    engine.state.players["a"].properties.add(1)
    engine.state.properties[1].owner_id = "a"
    engine.state.properties[1].building_level = 2
    engine.state.properties[1].mortgaged = True

    visible = player_visible_state(engine, "a")

    your_state = cast(dict[str, object], visible["your_state"])
    players = cast(list[dict[str, object]], visible["players"])
    board = cast(list[dict[str, object]], visible["board"])
    assert your_state["chance_cards"] == [{"card_id": "chance-waiver", "name": "免费卡"}]
    assert your_state["property_positions"] == [1]
    assert [item["player_id"] for item in players] == ["b"]
    assert players[0]["chance_card_count"] == 1
    assert players[0]["property_positions"] == []
    assert board[1] == {
        "position": 1,
        "name": "Mediterranean Avenue",
        "kind": "street",
        "price": 60,
        "building_cost": 50,
        "rents": [2, 10, 30, 90, 160, 250],
        "tax": None,
        "color_group": "brown",
        "owner_id": "a",
        "building_level": 2,
        "mortgaged": True,
    }
    assert board[0]["owner_id"] is None
    assert board[0]["building_level"] is None
    assert board[0]["mortgaged"] is None
    assert "owner_id" not in cast(dict[str, object], visible["current_space"])
    serialized = json.dumps(visible, ensure_ascii=False)
    assert "chance-nuclear" not in serialized
    assert "chance_draw_pile" not in serialized
    assert "community_chest_draw_pile" not in serialized
    assert "your_private_state" not in visible
    assert "assets" not in serialized
    assert "survived_turns" not in serialized


def test_current_space_rent_is_only_outstanding_rent(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 3

    assert (
        cast(dict[str, object], player_visible_state(engine, "a")["current_space"])["rent"] is None
    )

    engine.state.players["a"].position = 1
    engine.state.players["a"].properties.add(1)
    engine.state.properties[1].owner_id = "a"
    engine.state.players["b"].properties.add(3)
    engine.state.properties[3].owner_id = "b"
    engine.state.players["a"].cash = 0
    engine.state.turn_phase = TurnPhase.ROLLING
    engine.random.randint = lambda _low, _high: 1  # type: ignore[method-assign]
    engine.execute(RollDice("a"))

    visible = player_visible_state(engine, "a")
    assert engine.state.turn_phase is TurnPhase.PAYMENT_RESOLUTION
    assert cast(dict[str, object], visible["current_space"])["rent"] == 4


def test_current_space_rent_is_none_when_waived_or_frozen(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 2
    engine.state.players["a"].cash = 0
    engine.state.players["a"].rent_waivers = 1
    engine.state.players["b"].properties.add(3)
    engine.state.properties[3].owner_id = "b"
    engine.random.randint = lambda _low, _high: 1  # type: ignore[method-assign]

    engine.execute(RollDice("a"))

    assert (
        cast(dict[str, object], player_visible_state(engine, "a")["current_space"])["rent"] is None
    )

    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 2
    engine.state.players["a"].cash = 0
    engine.state.players["b"].properties.add(3)
    engine.state.properties[3].owner_id = "b"
    engine.state.ongoing_effects.append(
        OngoingEffect(
            kind=OngoingEffectKind.RENT_FREEZE,
            source_player_id="b",
            remaining_turns=1,
            activation_turn=0,
            color_group="brown",
        )
    )
    engine.random.randint = lambda _low, _high: 1  # type: ignore[method-assign]

    engine.execute(RollDice("a"))

    assert (
        cast(dict[str, object], player_visible_state(engine, "a")["current_space"])["rent"] is None
    )


def test_current_space_rent_sums_unpaid_alliance_shares(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    engine.state.players["a"].properties.add(1)
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].cash = 0
    engine.state.players["b"].properties.add(3)
    engine.state.properties[3].owner_id = "b"
    engine.state.ongoing_effects.append(
        OngoingEffect(
            kind=OngoingEffectKind.ALLIANCE,
            source_player_id="b",
            target_player_id="a",
            remaining_turns=1,
            activation_turn=0,
        )
    )
    engine.random.randint = lambda _low, _high: 1  # type: ignore[method-assign]

    engine.execute(RollDice("a"))

    visible = player_visible_state(engine, "a")
    assert engine.state.turn_phase is TurnPhase.PAYMENT_RESOLUTION
    assert cast(dict[str, object], visible["current_space"])["rent"] == 4


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
        json.dumps({"selected_option": {"option": "roll_dice"}, "reasoning": "继续掷骰推进回合。"}),
        request,
    )
    illegal = parse_and_validate(
        json.dumps({"selected_option": {"option": "build-1"}, "reasoning": "越权操作。"}), request
    )
    altered = parse_and_validate(
        json.dumps(
            {"selected_option": {"option": "roll_dice", "extra": 1}, "reasoning": "篡改结构。"}
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

    assert "## 当前局面" in prompt
    assert "你的状态" in prompt
    assert "## 当前决策" in prompt
    assert "## 合法候选操作" in prompt
    assert '"option": "<合法候选项的 option_id>"' in prompt
    assert "decision-game" not in prompt
    assert "decision-" not in prompt
    assert "chance_draw_pile" not in prompt
    assert "其余可见状态" not in prompt


def test_prompt_jail_decision_text(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    player = engine.state.players["a"]
    player.jail_status = JailStatus.ROLLING
    player.jail_roll_attempts = 1

    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "你可以选择掷出双骰或支付 50 现金出狱。" in prompt
    assert "你还有 2 / 3 次掷骰子" in prompt


def test_prompt_payment_decision_text(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].cash = 0
    engine.state.players["a"].properties.add(5)
    engine.state.properties[5].owner_id = "a"
    events: list[GameEvent] = []
    engine._queue_payment(  # pyright: ignore[reportPrivateUsage]
        engine.state.players["a"],
        4,
        None,
        "rent",
        TurnPhase.ASSET_MANAGEMENT,
        None,
        events,
    )
    engine._drain_settlement_operations(events)  # pyright: ignore[reportPrivateUsage]

    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "你有一笔 4 元款项需支付（rent，收款方 银行）" in prompt
    assert "请出售建筑或抵押地产来筹足款项。" in prompt


def test_prompt_forced_discard_decision_text(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.FORCED_DISCARD
    engine.state.players["a"].chance_cards.extend(
        ["chance-waiver", "chance-build", "chance-tax", "chance-steal", "chance-jail"]
    )

    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "当前持有 5 张机会卡，超过 4 张上限" in prompt
    assert "必须弃置到 4 张后才能结束回合。" in prompt


def test_prompt_asset_management_decision_text(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT

    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "现在是你的资产管理阶段" in prompt
    assert "或结束本回合。" in prompt


def test_prompt_contains_role_and_goal(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "你正在代表玩家「a」（座位 1）参与一局大富翁。" in prompt
    assert "你的目标：在回合上限结束时拥有最高净资产。" in prompt
    assert (
        "净资产 = 现金 + 未抵押地产的购买价 + 所有已建成建筑的价值（房屋单价 × 建筑层数）。"
        in prompt
    )


def test_prompt_renders_your_state_naturally(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    player = engine.state.players["a"]
    player.cash = 1234
    player.position = 1
    player.chance_cards.append("chance-waiver")
    player.community_get_out_of_jail_cards.append("community-jail-free")
    player.properties.add(1)
    engine.state.properties[1].owner_id = "a"

    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "当前为第 0 回合，处于玩家「a」的行动回合。" in prompt
    assert "现金：1234" in prompt
    assert "位置：格子 1（Mediterranean Avenue，街道）" in prompt
    assert "持有机会卡：chance-waiver" in prompt
    assert "持有出狱卡数量：1" in prompt
    assert "持有地产：格子 1（Mediterranean Avenue）" in prompt
    assert "剩余监狱回合数" not in prompt


def test_prompt_shows_remaining_jail_rolls_when_jailed(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    player = engine.state.players["a"]
    player.jail_status = JailStatus.ROLLING
    player.jail_roll_attempts = 1

    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "剩余监狱回合数：2" in prompt


def test_prompt_renders_other_players_naturally(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    other = engine.state.players["b"]
    other.cash = 999
    other.position = 3
    other.chance_cards.append("chance-nuclear")
    other.properties.add(3)
    engine.state.properties[3].owner_id = "b"

    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "其他玩家状态" in prompt
    assert "玩家「b」" in prompt
    assert "现金：999" in prompt
    assert "位置：格子 3（Baltic Avenue，街道）" in prompt
    assert "持有机会卡数量：1" in prompt
    assert "持有出狱卡数量：0" in prompt
    assert "持有地产：格子 3（Baltic Avenue）" in prompt
    assert "chance-nuclear" not in prompt


def test_prompt_renders_board_table(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.players["a"].properties.add(1)
    engine.state.properties[1].owner_id = "a"
    engine.state.properties[1].building_level = 2
    engine.state.players["b"].properties.add(6)
    engine.state.properties[6].owner_id = "b"
    engine.state.properties[6].mortgaged = True
    engine.state.ongoing_effects.append(
        OngoingEffect(
            kind=OngoingEffectKind.RENT_FREEZE,
            source_player_id="b",
            remaining_turns=2,
            activation_turn=0,
            color_group="brown",
        )
    )

    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "棋盘状态" in prompt
    assert "| 0 | GO | 起点 | - | - | - | - | - | - | - |" in prompt
    assert (
        "| 1 | Mediterranean Avenue | 街道 | brown | a | 2 | 60 | 50 | "
        "2 / 10 / 30 / 90 / 160 / 250 | 查封（剩余 2 回合） |"
    ) in prompt
    assert (
        "| 6 | Oriental Avenue | 街道 | light_blue | b | 0 | 100 | 50 | "
        "6 / 30 / 90 / 270 / 400 / 550 | 抵押 |"
    ) in prompt


def test_prompt_renders_other_player_jail_and_alliance(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    other = engine.state.players["b"]
    other.jail_status = JailStatus.ROLLING
    other.jail_roll_attempts = 1
    engine.state.ongoing_effects.append(
        OngoingEffect(
            kind=OngoingEffectKind.ALLIANCE,
            source_player_id="b",
            target_player_id="a",
            remaining_turns=3,
            activation_turn=0,
        )
    )

    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "玩家「b」" in prompt
    assert "持续效果：同盟效果剩余 3 回合，期间与玩家「a」平分收入" in prompt
    assert "剩余监狱回合数：2" in prompt


def test_prompt_shows_alliance_effect_for_player(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.ongoing_effects.append(
        OngoingEffect(
            kind=OngoingEffectKind.ALLIANCE,
            source_player_id="a",
            target_player_id="b",
            remaining_turns=2,
            activation_turn=0,
        )
    )

    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "持续效果：同盟效果剩余 2 回合，期间与玩家「b」平分收入" in prompt


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
    events: list[GameEvent] = []
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


def test_options_carry_engine_legal_target_specs(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    engine.state.players["a"].chance_cards.append("chance-jail")

    request = build_decision_request(engine, 1)
    by_type = {option.command_type: option for option in request.options}

    mortgage = by_type["mortgage"]
    assert mortgage.target is not None
    assert mortgage.target.kind == "position"
    assert mortgage.target.fields == ("position",)
    assert mortgage.target.legal_values == ((1,),)

    jail_option = next(
        option for option in request.options if option.command_type == "use_chance_card"
    )
    assert jail_option.target is not None
    assert jail_option.target.kind == "player"
    assert jail_option.target.legal_values == (("b",),)

    assert by_type["end_turn"].target is None


def test_selected_target_is_validated_and_reconstructed(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    request = build_decision_request(engine, 1)

    valid = parse_and_validate(
        json.dumps(
            {"selected_option": {"option": "mortgage", "target": 1}, "reasoning": "抵押地块。"}
        ),
        request,
    )
    assert valid.valid
    assert valid.option is not None
    command = command_from_option(request, valid.option, valid.target)
    assert isinstance(command, Mortgage)
    assert command.position == 1

    invalid = parse_and_validate(
        json.dumps(
            {"selected_option": {"option": "mortgage", "target": 99}, "reasoning": "非法目标。"}
        ),
        request,
    )
    assert not invalid.valid
