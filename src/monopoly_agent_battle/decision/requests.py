"""Generate player-visible state and engine-legal decision candidates."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from itertools import product

from monopoly_agent_battle.decision.models import DecisionKind, DecisionOption, DecisionRequest
from monopoly_agent_battle.domain.commands import (
    Build,
    DeclareBankruptcy,
    DiscardChanceCard,
    EndTurn,
    Mortgage,
    PayJailFine,
    RedeemMortgage,
    ResolveRent,
    RollDice,
    SellBuilding,
    UseChanceCard,
    UseCommunityGetOutOfJailCard,
)
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.board_data.classic_us_40 import BOARD, COLOR_GROUPS
from monopoly_agent_battle.game.cards.classic_cards import CARDS_BY_ID, CardEffect
from monopoly_agent_battle.game.engine import GameEngine, GameRuleError

GameCommand = (
    Build
    | DeclareBankruptcy
    | DiscardChanceCard
    | EndTurn
    | Mortgage
    | PayJailFine
    | RedeemMortgage
    | ResolveRent
    | RollDice
    | SellBuilding
    | UseChanceCard
    | UseCommunityGetOutOfJailCard
)


def player_visible_state(engine: GameEngine, player_id: str) -> dict[str, object]:
    """Project game state without card-deck order or other players' cards."""
    state = engine.state
    player = state.players[player_id]
    return {
        "board_version": engine.config.board_data_version,
        "turn": {
            "complete_rounds": state.complete_rounds,
            "current_player_id": state.current_player_id,
            "phase": state.turn_phase.value,
            "consecutive_doubles": state.consecutive_doubles,
        },
        "players": [
            {
                "player_id": item.player_id,
                "seat": item.seat,
                "cash": item.cash,
                "position": item.position,
                "jail_status": item.jail_status.value,
                "bankrupt": item.bankrupt,
                "survived_turns": item.survived_turns,
                "property_positions": sorted(item.properties),
                "chance_card_count": len(item.chance_cards),
                "community_get_out_of_jail_card_count": len(item.community_get_out_of_jail_cards),
            }
            for item in sorted(state.players.values(), key=lambda item: item.seat)
        ],
        "properties": [
            {
                "position": space.position,
                "name": space.name,
                "owner_id": state.properties[space.position].owner_id,
                "building_level": state.properties[space.position].building_level,
                "mortgaged": state.properties[space.position].mortgaged,
            }
            for space in BOARD
            if space.is_property
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


def build_decision_request(engine: GameEngine, sequence: int) -> DecisionRequest:
    """Build the next decision request exclusively from current engine state."""
    player_id = _decision_player_id(engine)
    kind, question = _question(engine, player_id)
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
        visible_state=player_visible_state(engine, player_id),
        options=options,
        output_constraints={
            "response_format": "single_json_object",
            "required_fields": ["selected_option", "reasoning"],
            "reasoning_max_characters": 400,
        },
    )


def _decision_player_id(engine: GameEngine) -> str:
    if engine.state.turn_phase is TurnPhase.PAYMENT_RESOLUTION:
        operation = engine.state.settlement_operations[0]
        return operation.player_id
    return engine.state.current_player_id


def _question(engine: GameEngine, player_id: str) -> tuple[DecisionKind, str]:
    phase = engine.state.turn_phase
    if phase is TurnPhase.PAYMENT_RESOLUTION:
        operation = engine.state.settlement_operations[0]
        return (
            DecisionKind.PAYMENT_RESOLUTION,
            f"你需要支付 {operation.amount}；请处置资产或宣告破产。",
        )
    if phase is TurnPhase.CARD_RESOLUTION:
        return DecisionKind.RENT_WAIVER, "请选择使用免租机会，或放弃免租并正常支付租金。"
    if phase is TurnPhase.ASSET_MANAGEMENT:
        return DecisionKind.ASSET_MANAGEMENT, "请选择一项资产管理操作，或结束本回合。"
    player = engine.state.players[player_id]
    if player.jail_status.value != "free":
        return DecisionKind.JAIL, "你正在监狱中；请选择支付罚款或掷骰尝试出狱。"
    raise RuntimeError("current game state requires automatic flow, not a decision request")


def _legal_options(engine: GameEngine, player_id: str) -> list[DecisionOption]:
    candidates = _candidate_commands(engine, player_id)
    options: list[DecisionOption] = []
    for command in candidates:
        if _is_legal(engine, command):
            option = _option_for(command)
            options.append(option)
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
    player = engine.state.players[player_id]
    phase = engine.state.turn_phase
    commands: list[GameCommand] = []
    if phase is TurnPhase.ROLLING:
        if player.jail_status.value == "free":
            return commands
        commands.append(RollDice(player_id))
        commands.append(PayJailFine(player_id))
        commands.extend(
            UseCommunityGetOutOfJailCard(player_id, card_id)
            for card_id in player.community_get_out_of_jail_cards
        )
    elif phase is TurnPhase.TURN_COMPLETE:
        return commands
    elif phase is TurnPhase.PAYMENT_RESOLUTION:
        commands.append(DeclareBankruptcy(player_id))
        commands.extend(SellBuilding(player_id, position) for position in sorted(player.properties))
        commands.extend(Mortgage(player_id, position) for position in sorted(player.properties))
    elif phase is TurnPhase.CARD_RESOLUTION:
        commands.extend((ResolveRent(player_id, True), ResolveRent(player_id, False)))
    elif phase is TurnPhase.ASSET_MANAGEMENT:
        commands.append(EndTurn(player_id))
        commands.extend(Build(player_id, position) for position in sorted(player.properties))
        commands.extend(SellBuilding(player_id, position) for position in sorted(player.properties))
        commands.extend(Mortgage(player_id, position) for position in sorted(player.properties))
        commands.extend(
            RedeemMortgage(player_id, position) for position in sorted(player.properties)
        )
        commands.extend(DiscardChanceCard(player_id, card_id) for card_id in player.chance_cards)
        commands.extend(_chance_candidates(engine, player_id))
    return commands


def _chance_candidates(engine: GameEngine, player_id: str) -> list[UseChanceCard]:
    player = engine.state.players[player_id]
    player_ids = tuple(engine.state.players)
    positions = tuple(engine.state.properties)
    color_groups = tuple(COLOR_GROUPS)
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


def _option_for(command: GameCommand) -> DecisionOption:
    command_type = _command_type(command)
    parameters = {
        field.name: getattr(command, field.name)
        for field in fields(command)
        if field.name != "player_id" and getattr(command, field.name) is not None
    }
    parameter_suffix = "-".join(str(value) for value in parameters.values())
    option_id = command_type if not parameter_suffix else f"{command_type}-{parameter_suffix}"
    return DecisionOption(
        option_id=option_id,
        command_type=command_type,
        parameters=parameters,
        summary=_summary(command),
        effect_preview={},
    )


def _command_type(command: GameCommand) -> str:
    name = type(command).__name__
    return "".join(f"_{char.lower()}" if char.isupper() else char for char in name).lstrip("_")


def _summary(command: GameCommand) -> str:
    if isinstance(command, ResolveRent):
        return "使用免租机会。" if command.use_waiver else "放弃免租并正常支付租金。"
    labels = {
        "RollDice": "掷骰继续本回合。",
        "PayJailFine": "支付 50 元罚款并出狱。",
        "EndTurn": "结束本回合。",
        "DeclareBankruptcy": "宣告破产。",
    }
    return labels.get(type(command).__name__, f"执行 {type(command).__name__} 操作。")
