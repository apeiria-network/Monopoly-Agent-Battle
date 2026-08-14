"""Generate player-visible state and engine-legal decision candidates."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from itertools import product

from monopoly_agent_battle.decision.models import DecisionKind, DecisionOption, DecisionRequest
from monopoly_agent_battle.domain.commands import (
    DiscardChanceCard,
    EndTurn,
    Mortgage,
    PayJailFine,
    RedeemMortgage,
    RollDice,
    SelectStolenChanceCard,
    SellBuilding,
    UseChanceCard,
    UseCommunityGetOutOfJailCard,
)
from monopoly_agent_battle.domain.models import JailStatus, TurnPhase
from monopoly_agent_battle.game.board_data.classic_us_40 import (
    BOARD,
    BOARD_BY_POSITION,
    COLOR_GROUPS,
)
from monopoly_agent_battle.game.cards.classic_cards import CARDS_BY_ID, CardEffect
from monopoly_agent_battle.game.engine import GameEngine, GameRuleError

GameCommand = (
    DiscardChanceCard
    | EndTurn
    | Mortgage
    | PayJailFine
    | RedeemMortgage
    | RollDice
    | SelectStolenChanceCard
    | SellBuilding
    | UseChanceCard
    | UseCommunityGetOutOfJailCard
)


def player_visible_state(engine: GameEngine, player_id: str) -> dict[str, object]:
    """Project complete permitted game context without decks or opponents' cards."""
    state = engine.state
    player = state.players[player_id]
    return {
        "board_version": engine.config.board_data_version,
        "turn": {
            "complete_rounds": state.complete_rounds,
            "current_player_id": state.current_player_id,
            "phase": state.turn_phase.value,
        },
        "current_space": {
            **_space_state(player.position),
            "owner_id": engine.state.properties[player.position].owner_id
            if player.position in engine.state.properties
            else None,
            "building_level": engine.state.properties[player.position].building_level
            if player.position in engine.state.properties
            else None,
            "mortgaged": engine.state.properties[player.position].mortgaged
            if player.position in engine.state.properties
            else None,
        },
        "board": [_space_state(space.position) for space in BOARD],
        "players": [
            {
                "player_id": item.player_id,
                "seat": item.seat,
                "cash": item.cash,
                "position": item.position,
                "space_name": BOARD_BY_POSITION[item.position].name,
                "jail_status": item.jail_status.value,
                "bankrupt": item.bankrupt,
                "survived_turns": item.survived_turns,
                "assets": [_asset_state(engine, position) for position in sorted(item.properties)],
                "chance_card_count": len(item.chance_cards),
                "community_get_out_of_jail_card_count": len(item.community_get_out_of_jail_cards),
            }
            for item in sorted(state.players.values(), key=lambda item: item.seat)
        ],
        "ongoing_effects": [
            {
                "kind": effect.kind.value,
                "source_player_id": effect.source_player_id,
                "target_player_id": effect.target_player_id,
                "color_group": effect.color_group,
                "remaining_turns": effect.remaining_turns,
            }
            for effect in state.ongoing_effects
        ],
        "your_private_state": {
            "chance_cards": [
                {"card_id": card_id, "name": CARDS_BY_ID[card_id].name}
                for card_id in player.chance_cards
            ],
            "community_get_out_of_jail_cards": list(player.community_get_out_of_jail_cards),
            "rent_waivers": player.rent_waivers,
        },
    }


def _space_state(position: int) -> dict[str, object]:
    space = BOARD_BY_POSITION[position]
    return {
        "position": space.position,
        "name": space.name,
        "kind": space.kind.value,
        "price": space.price,
        "building_cost": space.building_cost,
        "rents": list(space.rents),
        "tax": space.tax,
        "color_group": space.color_group,
    }


def _asset_state(engine: GameEngine, position: int) -> dict[str, object]:
    property_state = engine.state.properties[position]
    return {
        **_space_state(position),
        "owner_id": property_state.owner_id,
        "building_level": property_state.building_level,
        "mortgaged": property_state.mortgaged,
    }


def build_decision_request(engine: GameEngine, sequence: int) -> DecisionRequest:
    """Build the next decision request exclusively from current engine state."""
    player_id = _decision_player_id(engine)
    kind, question = _question(engine, player_id)
    visible_state = player_visible_state(engine, player_id)
    _add_decision_context(engine, visible_state)
    if kind is DecisionKind.THEFT_CARD_SELECTION:
        target_id = engine.state.pending_theft_target_id
        if target_id is None:
            raise AssertionError("theft selection lacks target")
        visible_state["theft_selection"] = {
            "target_player_id": target_id,
            "target_chance_cards": [
                {"card_id": card_id, "name": CARDS_BY_ID[card_id].name}
                for card_id in engine.state.players[target_id].chance_cards
            ],
        }
    options = tuple(_legal_options(engine, player_id))
    if not options:
        raise RuntimeError("current game state has no legal decision option")
    return DecisionRequest(
        decision_id=f"decision-{engine.config.game_id}-{sequence:06d}",
        game_id=engine.config.game_id,
        complete_rounds=engine.state.complete_rounds,
        player_id=player_id,
        phase=engine.state.turn_phase.value,
        kind=kind,
        question=question,
        visible_state=visible_state,
        options=options,
        output_constraints={
            "response_format": "single_json_object",
            "required_fields": ["selected_option", "reasoning"],
            "reasoning_max_characters": 400,
        },
    )


def _add_decision_context(engine: GameEngine, visible_state: dict[str, object]) -> None:
    """Attach state that is relevant only to the current decision boundary."""
    state = engine.state
    if state.turn_phase is TurnPhase.PAYMENT_RESOLUTION:
        operation = state.settlement_operations[0]
        visible_state["payment_due"] = {
            "amount": operation.amount,
            "reason": operation.source,
            "recipient_id": operation.recipient_id,
        }
    elif state.turn_phase is TurnPhase.ROLLING:
        player = state.players[state.current_player_id]
        if player.jail_status is JailStatus.ROLLING:
            visible_state["jail"] = {
                "roll_attempts": player.jail_roll_attempts,
                "fine": 50,
                "can_pay_fine": player.cash >= 50,
                "get_out_of_jail_card_count": len(player.community_get_out_of_jail_cards),
            }


def _decision_player_id(engine: GameEngine) -> str:
    if engine.state.turn_phase is TurnPhase.PAYMENT_RESOLUTION:
        return engine.state.settlement_operations[0].player_id
    return engine.state.current_player_id


def _question(engine: GameEngine, player_id: str) -> tuple[DecisionKind, str]:
    phase = engine.state.turn_phase
    if phase is TurnPhase.PAYMENT_RESOLUTION:
        return (
            DecisionKind.PAYMENT_RESOLUTION,
            f"你需要支付 {engine.state.settlement_operations[0].amount}；请出售建筑或抵押资产。",
        )
    if phase is TurnPhase.FORCED_DISCARD:
        return DecisionKind.FORCED_DISCARD, "尝试结束回合后，你的机会卡超过四张；必须弃置一张。"
    if phase is TurnPhase.THEFT_CARD_SELECTION:
        return (
            DecisionKind.THEFT_CARD_SELECTION,
            "抢夺掷骰成功；请选择从目标玩家手中拿走的一张机会卡。",
        )
    if phase is TurnPhase.ASSET_MANAGEMENT:
        return DecisionKind.ASSET_MANAGEMENT, "请选择一项资产管理操作，或尝试结束本回合。"
    if engine.state.players[player_id].jail_status is JailStatus.ROLLING:
        return DecisionKind.JAIL, "你正在监狱中；请选择缴纳罚款、使用出狱卡或掷骰尝试出狱。"
    raise RuntimeError("current game state requires automatic flow, not a decision request")


def _legal_options(engine: GameEngine, player_id: str) -> list[DecisionOption]:
    options = [
        _option_for(command, engine)
        for command in _candidate_commands(engine, player_id)
        if _is_legal(engine, command)
    ]
    if not options:
        return []
    default = next((option for option in options if option.command_type == "end_turn"), options[0])
    return [
        DecisionOption(
            option.option_id,
            option.command_type,
            option.parameters,
            option.summary,
            option.effect_preview,
            option.option_id == default.option_id,
        )
        for option in options
    ]


def _candidate_commands(engine: GameEngine, player_id: str) -> list[GameCommand]:
    player, phase = engine.state.players[player_id], engine.state.turn_phase
    if phase is TurnPhase.ROLLING:
        if player.jail_status is not JailStatus.ROLLING:
            return []
        return [
            RollDice(player_id),
            PayJailFine(player_id),
            *(
                UseCommunityGetOutOfJailCard(player_id, card_id)
                for card_id in player.community_get_out_of_jail_cards
            ),
        ]
    if phase is TurnPhase.PAYMENT_RESOLUTION:
        return [
            *(SellBuilding(player_id, position) for position in sorted(player.properties)),
            *(Mortgage(player_id, position) for position in sorted(player.properties)),
        ]
    if phase is TurnPhase.FORCED_DISCARD:
        return [DiscardChanceCard(player_id, card_id) for card_id in player.chance_cards]
    if phase is TurnPhase.THEFT_CARD_SELECTION:
        target_id = engine.state.pending_theft_target_id
        return (
            []
            if target_id is None
            else [
                SelectStolenChanceCard(player_id, card_id)
                for card_id in engine.state.players[target_id].chance_cards
            ]
        )
    if phase is TurnPhase.ASSET_MANAGEMENT:
        return [
            EndTurn(player_id),
            *(SellBuilding(player_id, position) for position in sorted(player.properties)),
            *(Mortgage(player_id, position) for position in sorted(player.properties)),
            *(RedeemMortgage(player_id, position) for position in sorted(player.properties)),
            *_chance_candidates(engine, player_id),
        ]
    return []


def _chance_candidates(engine: GameEngine, player_id: str) -> list[UseChanceCard]:
    player = engine.state.players[player_id]
    player_ids, positions, color_groups = (
        tuple(engine.state.players),
        tuple(engine.state.properties),
        tuple(COLOR_GROUPS),
    )
    candidates: list[UseChanceCard] = []
    for card_id in player.chance_cards:
        effect = CARDS_BY_ID[card_id].effect
        if effect in {CardEffect.NUCLEAR_RESET, CardEffect.RENT_WAIVER}:
            candidates.append(UseChanceCard(player_id, card_id))
        elif effect is CardEffect.STEAL_CARD:
            candidates.extend(
                UseChanceCard(player_id, card_id, target_player_id=target)
                for target in player_ids
                if engine.state.players[target].chance_cards
            )
        elif effect in {
            CardEffect.ALLIANCE,
            CardEffect.JAIL_PLAYER,
            CardEffect.EQUALIZE_CASH,
            CardEffect.TAX_PLAYER,
        }:
            candidates.extend(
                UseChanceCard(player_id, card_id, target_player_id=target) for target in player_ids
            )
        elif effect in {
            CardEffect.RENT_SURGE,
            CardEffect.RENT_FREEZE,
            CardEffect.MONSTER,
            CardEffect.ANGEL,
        }:
            candidates.extend(
                UseChanceCard(player_id, card_id, target_color_group=color)
                for color in color_groups
            )
        elif effect in {CardEffect.VACATE_PROPERTY, CardEffect.BUY_PROPERTY, CardEffect.BUILD}:
            candidates.extend(
                UseChanceCard(player_id, card_id, target_position=position)
                for position in positions
            )
        elif effect in {CardEffect.SWAP_PROPERTY, CardEffect.SWAP_BUILDINGS}:
            candidates.extend(
                UseChanceCard(
                    player_id, card_id, target_position=first, secondary_target_position=second
                )
                for first, second in product(positions, repeat=2)
            )
        else:
            raise AssertionError(f"unsupported chance card effect: {effect}")
    return candidates


def _is_legal(engine: GameEngine, command: GameCommand) -> bool:
    cloned = deepcopy(engine)
    try:
        cloned.execute(command)
    except GameRuleError:
        return False
    return True


def _option_for(command: GameCommand, engine: GameEngine) -> DecisionOption:
    command_type = _command_type(command)
    parameters = {
        field.name: getattr(command, field.name)
        for field in fields(command)
        if field.name != "player_id" and getattr(command, field.name) is not None
    }
    suffix = "-".join(str(value) for value in parameters.values())
    return DecisionOption(
        f"{command_type}-{suffix}" if suffix else command_type,
        command_type,
        parameters,
        _summary(command, engine),
        _effect_preview(command, engine),
    )


def _command_type(command: GameCommand) -> str:
    return "".join(
        f"_{char.lower()}" if char.isupper() else char for char in type(command).__name__
    ).lstrip("_")


def _summary(command: GameCommand, engine: GameEngine) -> str:
    if isinstance(command, SellBuilding):
        return f"出售 {_space_state(command.position)['name']} 的一层建筑。"
    if isinstance(command, Mortgage):
        return f"抵押 {_space_state(command.position)['name']}。"
    if isinstance(command, RedeemMortgage):
        return f"赎回 {_space_state(command.position)['name']}。"
    if isinstance(command, DiscardChanceCard):
        return f"弃置机会卡「{CARDS_BY_ID[command.card_id].name}」。"
    if isinstance(command, SelectStolenChanceCard):
        return f"从目标玩家手中拿走机会卡「{CARDS_BY_ID[command.card_id].name}」。"
    if isinstance(command, UseChanceCard):
        card = CARDS_BY_ID[command.card_id]
        preview = _card_target_preview(command, engine)
        target = preview.get("target_name")
        return f"使用机会卡「{card.name}」{f'，目标为 {target}' if target else ''}。"
    return {
        "RollDice": "掷骰继续本回合。",
        "PayJailFine": "支付 50 元罚款并出狱。",
        "EndTurn": "尝试结束本回合。",
        "UseCommunityGetOutOfJailCard": "使用一张出狱卡。",
    }.get(type(command).__name__, f"执行 {type(command).__name__}。")


def _effect_preview(command: GameCommand, engine: GameEngine) -> dict[str, object]:
    if isinstance(command, SellBuilding):
        return {
            "cash_change": (BOARD_BY_POSITION[command.position].building_cost or 0) // 2,
            "position": command.position,
            "space_name": BOARD_BY_POSITION[command.position].name,
            "effect": "出售一层建筑",
        }
    if isinstance(command, Mortgage):
        return {
            "cash_change": BOARD_BY_POSITION[command.position].price or 0,
            "position": command.position,
            "space_name": BOARD_BY_POSITION[command.position].name,
            "effect": "资产将被抵押，不能收取租金",
        }
    if isinstance(command, RedeemMortgage):
        return {
            "cash_change": -((BOARD_BY_POSITION[command.position].price or 0) * 110 // 100),
            "position": command.position,
            "space_name": BOARD_BY_POSITION[command.position].name,
            "effect": "资产恢复未抵押状态",
        }
    if isinstance(command, DiscardChanceCard):
        return {"card_name": CARDS_BY_ID[command.card_id].name, "effect": "卡牌将被弃置"}
    if isinstance(command, SelectStolenChanceCard):
        return {
            "card_name": CARDS_BY_ID[command.card_id].name,
            "effect": "转入你的手牌；抢夺卡将被弃置",
        }
    if isinstance(command, UseChanceCard):
        card = CARDS_BY_ID[command.card_id]
        return {
            "card_name": card.name,
            "effect": card.effect.value,
            **_card_target_preview(command, engine),
        }
    if isinstance(command, PayJailFine):
        return {"cash_change": -50, "effect": "出狱后仍需掷骰行动"}
    if isinstance(command, UseCommunityGetOutOfJailCard):
        return {"card_name": CARDS_BY_ID[command.card_id].name, "effect": "出狱后仍需掷骰行动"}
    if isinstance(command, RollDice):
        return {"effect": "骰子结果由游戏引擎决定"}
    if isinstance(command, EndTurn):
        player = engine.state.players[command.player_id]
        return {
            "effect": "若机会卡超过四张，将先要求弃置一张",
            "chance_card_count": len(player.chance_cards),
        }
    return {}


def _card_target_preview(command: UseChanceCard, engine: GameEngine) -> dict[str, object]:
    preview: dict[str, object] = {}
    if command.target_player_id is not None:
        target = engine.state.players[command.target_player_id]
        preview.update(
            target_player_id=target.player_id,
            target_name=target.player_id,
            target_position=target.position,
        )
    if command.target_position is not None:
        preview.update(
            target_position=command.target_position,
            target_space_name=BOARD_BY_POSITION[command.target_position].name,
        )
    if command.secondary_target_position is not None:
        preview.update(
            secondary_target_position=command.secondary_target_position,
            secondary_target_space_name=BOARD_BY_POSITION[command.secondary_target_position].name,
        )
    if command.target_color_group is not None:
        preview["target_color_group"] = command.target_color_group
    return preview
