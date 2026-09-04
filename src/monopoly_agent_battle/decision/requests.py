"""Generate player-visible state and engine-legal decision candidates."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields
from itertools import product

from monopoly_agent_battle.decision.models import (
    DecisionKind,
    DecisionOption,
    DecisionRequest,
    OptionTarget,
)
from monopoly_agent_battle.decision.wording import option_wording
from monopoly_agent_battle.domain.commands import (
    DiscardChanceCard,
    EndTurn,
    GameCommand,
    Mortgage,
    PayJailFine,
    RedeemMortgage,
    RollDice,
    SelectStolenChanceCard,
    SellBuilding,
    UseChanceCard,
    UseCommunityGetOutOfJailCard,
)
from monopoly_agent_battle.domain.models import JailStatus, PlayerState, TurnPhase
from monopoly_agent_battle.game.board_data.classic_us_40 import (
    BOARD,
    BOARD_BY_POSITION,
    COLOR_GROUPS,
)
from monopoly_agent_battle.game.cards.classic_cards import CARDS_BY_ID, CardEffect
from monopoly_agent_battle.game.engine import GameEngine, GameRuleError


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
            "rent": _unpaid_current_space_rent(engine, player_id),
        },
        "board": [_board_space_state(engine, space.position) for space in BOARD],
        "your_state": {
            **_public_player_state(player),
            "chance_cards": [
                {"card_id": card_id, "name": CARDS_BY_ID[card_id].name}
                for card_id in player.chance_cards
            ],
            "community_get_out_of_jail_cards": list(player.community_get_out_of_jail_cards),
            "rent_waivers": player.rent_waivers,
        },
        "players": [
            {
                **_public_player_state(item),
                "chance_card_count": len(item.chance_cards),
                "community_get_out_of_jail_card_count": len(item.community_get_out_of_jail_cards),
            }
            for item in sorted(state.players.values(), key=lambda item: item.seat)
            if item.player_id != player_id
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
    }


def _public_player_state(player: PlayerState) -> dict[str, object]:
    """Project public player data without private cards or internal turn counters."""
    return {
        "player_id": player.player_id,
        "seat": player.seat,
        "cash": player.cash,
        "position": player.position,
        "space_name": BOARD_BY_POSITION[player.position].name,
        "jail_status": player.jail_status.value,
        "jail_roll_attempts": player.jail_roll_attempts,
        "bankrupt": player.bankrupt,
        "property_positions": sorted(player.properties),
    }


def _unpaid_current_space_rent(engine: GameEngine, player_id: str) -> int | None:
    """Return only the current landing's outstanding rent, if settlement is blocked."""
    state = engine.state
    player = state.players[player_id]
    property_state = state.properties.get(player.position)
    if property_state is None or property_state.owner_id in {None, player_id}:
        return None
    if state.turn_phase is not TurnPhase.PAYMENT_RESOLUTION:
        return None

    rent_sources = {"rent"}
    pending_rent: list[int] = []
    for operation in state.settlement_operations:
        if operation.player_id != player_id or operation.source not in rent_sources:
            break
        if operation.amount is None:
            raise AssertionError("rent payment operation has no amount")
        pending_rent.append(operation.amount)
    return sum(pending_rent) if pending_rent else None


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


def _board_space_state(engine: GameEngine, position: int) -> dict[str, object]:
    property_state = engine.state.properties.get(position)
    return {
        **_space_state(position),
        "owner_id": property_state.owner_id if property_state else None,
        "building_level": property_state.building_level if property_state else None,
        "mortgaged": property_state.mortgaged if property_state else None,
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
            "required_fields": ["selected_option", "reason"],
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
        return DecisionKind.FORCED_DISCARD, "尝试结束回合后，你的机会卡超过三张；必须弃置一张。"
    if phase is TurnPhase.THEFT_CARD_SELECTION:
        return (
            DecisionKind.THEFT_CARD_SELECTION,
            "抢夺成功；请选择从目标玩家手中拿走的一张机会卡。",
        )
    if phase is TurnPhase.ASSET_MANAGEMENT:
        return DecisionKind.ASSET_MANAGEMENT, "请选择一项资产管理操作，或尝试结束本回合。"
    if engine.state.players[player_id].jail_status is JailStatus.ROLLING:
        return DecisionKind.JAIL, "你正在监狱中；请选择缴纳罚款、使用出狱卡或掷骰尝试出狱。"
    raise RuntimeError("current game state requires automatic flow, not a decision request")


def _legal_options(engine: GameEngine, player_id: str) -> list[DecisionOption]:
    drafts: dict[tuple[object, ...], _OptionDraft] = {}
    order: list[tuple[object, ...]] = []
    for command in _candidate_commands(engine, player_id):
        if not _is_legal(engine, command):
            continue
        command_type = _command_type(command)
        fixed_params, target_fields, target_values = _split_command(command)
        key = (command_type, tuple(sorted(fixed_params.items())))
        if key not in drafts:
            drafts[key] = _OptionDraft(command_type, fixed_params, target_fields, command)
            order.append(key)
        drafts[key].target_values.append(target_values)
    options = [_build_option(drafts[key]) for key in order]
    if not options:
        return []
    default_id = next(
        (option.option_id for option in options if option.command_type == "end_turn"),
        options[0].option_id,
    )
    return [
        DecisionOption(
            option.option_id,
            option.command_type,
            option.parameters,
            option.title,
            option.preview,
            option.response_format,
            option.option_id == default_id,
            option.target,
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


@dataclass
class _OptionDraft:
    command_type: str
    fixed_params: dict[str, object]
    target_fields: tuple[tuple[str, str], ...]
    representative: GameCommand
    target_values: list[tuple[object, ...]] = field(default_factory=list[tuple[object, ...]])


def _split_command(
    command: GameCommand,
) -> tuple[dict[str, object], tuple[tuple[str, str], ...], tuple[object, ...]]:
    """Split a concrete command into fixed parameters and its target value tuple."""
    target_fields = _target_fields(command)
    target_command_fields = {command_field for _, command_field in target_fields}
    fixed_params = {
        item.name: getattr(command, item.name)
        for item in fields(command)
        if item.name != "player_id"
        and item.name not in target_command_fields
        and getattr(command, item.name) is not None
    }
    target_values = tuple(getattr(command, command_field) for _, command_field in target_fields)
    return fixed_params, target_fields, target_values


def _target_fields(command: GameCommand) -> tuple[tuple[str, str], ...]:
    """Return (json_field, command_field) pairs that the controller must specify."""
    if isinstance(command, UseChanceCard):
        return _chance_target_fields(CARDS_BY_ID[command.card_id].effect)
    if isinstance(command, (SellBuilding, Mortgage, RedeemMortgage)):
        return (("position", "position"),)
    if isinstance(command, SelectStolenChanceCard):
        return (("card_id", "card_id"),)
    return ()


def _chance_target_fields(effect: CardEffect) -> tuple[tuple[str, str], ...]:
    if effect in {CardEffect.NUCLEAR_RESET, CardEffect.RENT_WAIVER}:
        return ()
    if effect in {
        CardEffect.ALLIANCE,
        CardEffect.JAIL_PLAYER,
        CardEffect.EQUALIZE_CASH,
        CardEffect.TAX_PLAYER,
        CardEffect.STEAL_CARD,
    }:
        return (("target_player_id", "target_player_id"),)
    if effect in {
        CardEffect.RENT_SURGE,
        CardEffect.RENT_FREEZE,
        CardEffect.MONSTER,
        CardEffect.ANGEL,
    }:
        return (("target_color_group", "target_color_group"),)
    if effect in {CardEffect.VACATE_PROPERTY, CardEffect.BUY_PROPERTY, CardEffect.BUILD}:
        return (("target_position", "target_position"),)
    if effect in {CardEffect.SWAP_PROPERTY, CardEffect.SWAP_BUILDINGS}:
        return (
            ("swap_in_position", "target_position"),
            ("swap_out_position", "secondary_target_position"),
        )
    raise AssertionError(f"unsupported chance card effect: {effect}")


def _target_kind(target_fields: tuple[tuple[str, str], ...]) -> str:
    if len(target_fields) == 2:
        return "position_pair"
    command_field = target_fields[0][1]
    if command_field == "target_player_id":
        return "player"
    if command_field == "target_color_group":
        return "color_group"
    if command_field in {"target_position", "position"}:
        return "position"
    if command_field == "card_id":
        return "card"
    raise AssertionError(f"unsupported target field: {command_field}")


def _option_id(command_type: str, fixed_params: dict[str, object]) -> str:
    suffix = "-".join(str(value) for value in fixed_params.values())
    return f"{command_type}-{suffix}" if suffix else command_type


def _build_option(draft: _OptionDraft) -> DecisionOption:
    target = None
    if draft.target_fields:
        target = OptionTarget(
            kind=_target_kind(draft.target_fields),
            fields=tuple(json_field for json_field, _ in draft.target_fields),
            command_fields=tuple(command_field for _, command_field in draft.target_fields),
            legal_values=tuple(draft.target_values),
        )
    wording = option_wording(draft.representative)
    return DecisionOption(
        _option_id(draft.command_type, draft.fixed_params),
        draft.command_type,
        draft.fixed_params,
        wording.title,
        wording.preview,
        wording.response_format,
        target=target,
    )


def _command_type(command: GameCommand) -> str:
    return "".join(
        f"_{char.lower()}" if char.isupper() else char for char in type(command).__name__
    ).lstrip("_")
