import json
import re
from pathlib import Path
from typing import Any, cast

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.prompts import options_from_prompt, render_decision_prompt
from monopoly_agent_battle.decision.protocol import (
    command_from_option,
    default_option_json,
    option_json,
    parse_and_validate,
)
from monopoly_agent_battle.decision.requests import build_decision_request, player_visible_state
from monopoly_agent_battle.domain.commands import (
    DiscardChanceCard,
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
from monopoly_agent_battle.game.cards.classic_cards import CARDS_BY_ID
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
        json.dumps({"selected_option": {"option": "roll_dice"}, "reason": "继续掷骰推进回合。"}),
        request,
    )
    illegal = parse_and_validate(
        json.dumps({"selected_option": {"option": "build-1"}, "reason": "越权操作。"}), request
    )
    # Extra fields inside selected_option are silently ignored (§4C-remake rules).
    extra_field_ok = parse_and_validate(
        json.dumps(
            {"selected_option": {"option": "roll_dice", "extra": 1}, "reason": "多余字段被忽略。"}
        ),
        request,
    )

    assert valid.valid
    assert not illegal.valid
    assert extra_field_ok.valid


def test_response_tolerates_markdown_code_fence(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].jail_status = JailStatus.ROLLING
    request = build_decision_request(engine, 1)
    inner = json.dumps({"selected_option": {"option": "roll_dice"}, "reason": "掷骰推进。"})

    fenced_json = parse_and_validate(f"```json\n{inner}\n```", request)
    fenced_bare = parse_and_validate(f"```\n{inner}\n```", request)
    fenced_padded = parse_and_validate(f"  ```json\n{inner}\n```  ", request)
    plain = parse_and_validate(inner, request)
    # A genuinely broken reply must still fail even if it looks fenced.
    broken = parse_and_validate("```json\nnot-json\n```", request)

    assert fenced_json.valid
    assert fenced_bare.valid
    assert fenced_padded.valid
    assert plain.valid
    assert not broken.valid
    assert broken.error_category == "not_json"


def test_prompt_contains_request_and_fixed_response_contract(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "## 当前局面" in prompt
    assert "你的状态" in prompt
    assert "## 当前决策" in prompt
    assert "## 合法候选操作" in prompt
    assert "## 输出要求" in prompt
    assert prompt.index("游戏规则") < prompt.index("## 输出要求")
    assert prompt.index("## 输出要求") < prompt.index("## 当前局面")
    assert '"option_id"' in prompt
    assert '"title"' in prompt
    assert '"preview"' in prompt
    assert '"response_format"' in prompt
    assert '"reason"' in prompt
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


def test_forced_discard_lists_each_held_card_as_its_own_candidate(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.FORCED_DISCARD
    held_cards = [
        "chance-waiver",
        "chance-build",
        "chance-tax",
        "chance-steal",
        "chance-jail",
    ]
    engine.state.players["a"].chance_cards.extend(held_cards)

    request = build_decision_request(engine, 1)
    discard_options = [
        option for option in request.options if option.command_type == "discard_chance_card"
    ]

    assert [option.option_id for option in discard_options] == [
        f"discard_chance_card-{card_id}" for card_id in held_cards
    ]
    assert [option.parameters for option in discard_options] == [
        {"card_id": card_id} for card_id in held_cards
    ]
    assert all(option.target is None for option in discard_options)
    assert all(
        CARDS_BY_ID[card_id].name in option.title
        for card_id, option in zip(held_cards, discard_options, strict=True)
    )

    for card_id, option in zip(held_cards, discard_options, strict=True):
        validation = parse_and_validate(
            json.dumps({"selected_option": {"option": option.option_id}, "reason": "弃置该卡。"}),
            request,
        )
        assert validation.valid
        assert validation.option == option
        command = command_from_option(request, option, validation.target)
        assert command == DiscardChanceCard("a", card_id)

    assert default_option_json(discard_options[0]) == {
        "option": "discard_chance_card-chance-waiver"
    }


def test_asset_management_keeps_cards_separate_and_folds_each_card_targets(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    player = engine.state.players["a"]
    player.properties.update({1, 3})
    engine.state.properties[1].owner_id = "a"
    engine.state.properties[3].owner_id = "a"
    player.chance_cards.extend(["chance-jail", "chance-build"])

    request = build_decision_request(engine, 1)
    chance_options = [
        option for option in request.options if option.command_type == "use_chance_card"
    ]
    by_card_id = {cast(str, option.parameters["card_id"]): option for option in chance_options}

    assert list(by_card_id) == ["chance-jail", "chance-build"]
    assert len(chance_options) == 2
    assert by_card_id["chance-jail"].option_id == "use_chance_card-chance-jail"
    assert by_card_id["chance-build"].option_id == "use_chance_card-chance-build"
    assert by_card_id["chance-jail"].target is not None
    assert by_card_id["chance-jail"].target.legal_values == (("b",),)
    assert by_card_id["chance-build"].target is not None
    assert by_card_id["chance-build"].target.legal_values == ((1,), (3,))

    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.FORCED_DISCARD
    engine.state.players["a"].chance_cards.extend(
        ["chance-waiver", "chance-build", "chance-tax", "chance-steal", "chance-jail"]
    )

    prompt = render_decision_prompt(build_decision_request(engine, 1))

    assert "当前持有 5 张机会卡，超过 4 张上限" in prompt
    assert "必须弃置到 4 张后才能结束回合。" in prompt
    candidates = cast(list[dict[str, Any]], options_from_prompt(prompt))
    discard_candidates = [
        candidate
        for candidate in candidates
        if candidate["option_id"].startswith("discard_chance_card-")
    ]
    assert len(discard_candidates) == 5
    for candidate, card_id in zip(
        discard_candidates,
        ["chance-waiver", "chance-build", "chance-tax", "chance-steal", "chance-jail"],
        strict=True,
    ):
        assert set(candidate) == {"option_id", "title", "preview", "response_format"}
        assert CARDS_BY_ID[card_id].name in candidate["title"]
        assert candidate["response_format"]["selected_option"] == {
            "option": f"discard_chance_card-{card_id}"
        }


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

    assert "你正在一局大富翁对局中为一方玩家效力，与另外 1-3 名玩家在同一棋盘上竞争。" in prompt
    assert "你在本局代表玩家a" in prompt
    assert "当其余玩家全部破产时，最后存活者立即获胜" in prompt
    assert (
        "净资产 = 现金 + 全部地产的购买价 + 全部已建成建筑的价值（房屋单价 × 建筑层数）"
        "− 抵押中地产的购买价。" in prompt
    )
    assert "候选均不理想时也必须选出相对最优的一个，不得弃权。" in prompt
    assert "座位" not in prompt.split("## 游戏规则")[0]


def test_prompt_v1_profile_keeps_legacy_identity(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    prompt = render_decision_prompt(build_decision_request(engine, 1), prompt_profile="full-v1")

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


def block(text: str, start: str, ends: tuple[str, ...]) -> str:
    """截取 start 标记之后、任一 end 标记之前的文本块。start 不存在时显式失败。"""
    _, sep, rest = text.partition(start)
    assert sep, f"起始标记不存在: {start!r}"
    cut = len(rest)
    for marker in ends:
        idx = rest.find(marker)
        if 0 <= idx < cut:
            cut = idx
    return rest[:cut]


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
    b_block = block(prompt, "玩家「b」", ("## ",))  # b 的块：到下一节为止
    assert "现金：999" in b_block  # 现在绑死在 b 名下
    assert "位置：格子 3（Baltic Avenue，街道）" in b_block
    assert "持有机会卡数量：1" in b_block
    assert "持有出狱卡数量：0" in b_block
    assert "持有地产：格子 3（Baltic Avenue）" in b_block
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
    # Dynamic table lists only owned spaces; 6-column format:
    # 格 / 类型 / 所有者 / 建筑 / 状态 / 当前租金
    assert "| 1 | 街道 | a | 2 | 查封（剩余 2 回合） | 0（查封） |" in prompt
    assert "| 6 | 街道 | b | 0 | 抵押 | 0（抵押） |" in prompt
    # Non-owned spaces (including position 0 GO) are no longer listed as rows.
    dynamic_prompt = prompt.split("## 当前局面\n", 1)[1]
    assert "\n| 0 | 起点" not in dynamic_prompt
    assert "（未列出的地产均无主。）" in prompt


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
    theft_option = request.options[0]
    assert theft_option.parameters == {}
    assert theft_option.target is not None
    assert theft_option.target.legal_values == (("chance-build",), ("chance-tax",))
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


def test_prompt_renders_target_instructions(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    engine.state.properties[3].owner_id = "b"
    engine.state.players["b"].properties.add(3)
    engine.state.players["a"].chance_cards.append("chance-swap-property")

    prompt = render_decision_prompt(build_decision_request(engine, 1))
    options = cast(list[dict[str, Any]], options_from_prompt(prompt))
    by_option_id = {option["option_id"]: option for option in options}

    # 改法：拆成三步独立断言，每一步的期望值都是字面量
    mortgage_selected = by_option_id["mortgage"]["response_format"]["selected_option"]
    assert set(mortgage_selected.keys()) == {"option", "target"}  # 结构：多/少字段都抓得到
    assert mortgage_selected["option"] == "mortgage"  # 值：写死
    assert (
        mortgage_selected["target"] == "填写需要抵押的目标格子编号"
    )  # 占位文本（wording.py 约定）

    swap_option_id = "use_chance_card-chance-swap-property"
    swap_selected = by_option_id[swap_option_id]["response_format"]["selected_option"]
    assert swap_selected["option"] == swap_option_id
    # 多字段 target 不只断言键，连每个字段的占位文本一起写死
    assert swap_selected["target"] == {
        "swap_in_position": "填写换入的目标格子id",
        "swap_out_position": "填写换出的目标格子id",
    }


def test_selected_target_is_validated_and_reconstructed(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    request = build_decision_request(engine, 1)

    valid = parse_and_validate(
        json.dumps(
            {"selected_option": {"option": "mortgage", "target": 1}, "reason": "抵押地块。"}
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
            {"selected_option": {"option": "mortgage", "target": 99}, "reason": "非法目标。"}
        ),
        request,
    )
    assert not invalid.valid


def test_option_json_encodes_selected_legal_target_tuple(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    player = engine.state.players["a"]
    player.properties.add(1)
    engine.state.properties[1].owner_id = "a"
    engine.state.players["b"].properties.add(3)
    engine.state.properties[3].owner_id = "b"
    player.chance_cards.append("chance-swap-property")
    request = build_decision_request(engine, 1)
    option = next(
        candidate
        for candidate in request.options
        if candidate.option_id == "use_chance_card-chance-swap-property"
    )
    assert option.target is not None

    selected = option_json(option, option.target.legal_values[-1])

    assert selected == {
        "option": "use_chance_card-chance-swap-property",
        "target": dict(zip(option.target.fields, option.target.legal_values[-1], strict=True)),
    }


def test_prompt_static_board_reference_lists_all_40_spaces(tmp_path: Path) -> None:
    """Segment 2 rules text includes a static reference table covering all 40 board spaces."""
    from monopoly_agent_battle.decision.prompts import render_rules

    rules_text = render_rules()

    ref_block = block(rules_text, "棋盘布局参考", ("## ",))
    positions = sorted(int(p) for p in re.findall(r"^\|\s*(\d+)\s*\|", ref_block, flags=re.M))
    # All 40 spaces (0-39) are listed: 22 streets + 4 railroads + 2 utilities
    # + 12 non-property spaces (GO/Chance×3/Community Chest×3/Tax×2/Jail/
    # Free Parking/Go To Jail).
    assert positions == list(range(40))
