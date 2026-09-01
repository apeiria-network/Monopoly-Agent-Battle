"""Versioned JSON codec for resumable deterministic game checkpoints."""

from __future__ import annotations

import random
from typing import Any, cast

from monopoly_agent_battle.domain.models import (
    CardDeck,
    EndReason,
    GameState,
    JailStatus,
    OngoingEffect,
    OngoingEffectKind,
    PlayerState,
    PropertyState,
    SettlementOperation,
    SettlementOperationKind,
    SettlementOperationStatus,
    TurnPhase,
)

CHECKPOINT_SCHEMA = "game-checkpoint-v1"


def encode_checkpoint(state: GameState, rng: random.Random) -> dict[str, Any]:
    """Encode all mutable engine state and its pseudo-random stream."""
    return {
        "schema": CHECKPOINT_SCHEMA,
        "state": {
            "players": {
                key: {
                    "player_id": value.player_id,
                    "seat": value.seat,
                    "cash": value.cash,
                    "position": value.position,
                    "properties": sorted(value.properties),
                    "jail_status": value.jail_status.value,
                    "jail_roll_attempts": value.jail_roll_attempts,
                    "bankrupt": value.bankrupt,
                    "survived_turns": value.survived_turns,
                    "chance_cards": list(value.chance_cards),
                    "community_get_out_of_jail_cards": list(value.community_get_out_of_jail_cards),
                    "rent_waivers": value.rent_waivers,
                }
                for key, value in state.players.items()
            },
            "properties": {
                str(key): {
                    "owner_id": value.owner_id,
                    "building_level": value.building_level,
                    "mortgaged": value.mortgaged,
                }
                for key, value in state.properties.items()
            },
            "current_player_id": state.current_player_id,
            "turn_phase": state.turn_phase.value,
            "settlement_operations": [_operation(value) for value in state.settlement_operations],
            "next_settlement_operation_id": state.next_settlement_operation_id,
            "complete_rounds": state.complete_rounds,
            "finished": state.finished,
            "end_reason": state.end_reason.value if state.end_reason else None,
            "rankings": list(state.rankings),
            "chance_draw_pile": list(state.chance_draw_pile),
            "chance_discard_pile": list(state.chance_discard_pile),
            "community_chest_draw_pile": list(state.community_chest_draw_pile),
            "community_chest_discard_pile": list(state.community_chest_discard_pile),
            "ongoing_effects": [
                {
                    "kind": value.kind.value,
                    "source_player_id": value.source_player_id,
                    "remaining_turns": value.remaining_turns,
                    "activation_turn": value.activation_turn,
                    "target_player_id": value.target_player_id,
                    "color_group": value.color_group,
                }
                for value in state.ongoing_effects
            ],
            "pending_theft_thief_id": state.pending_theft_thief_id,
            "pending_theft_target_id": state.pending_theft_target_id,
            "pending_theft_source_card_id": state.pending_theft_source_card_id,
            "consecutive_doubles": state.consecutive_doubles,
            "round_player_ids": list(state.round_player_ids),
            "completed_round_player_ids": sorted(state.completed_round_player_ids),
        },
        "rng_state": _json_value(rng.getstate()),
    }


def restore_checkpoint(document: dict[str, Any], state: GameState, rng: random.Random) -> None:
    """Restore a validated checkpoint into an existing engine."""
    if document.get("schema") != CHECKPOINT_SCHEMA:
        raise ValueError("unsupported checkpoint schema")
    raw = document.get("state")
    if not isinstance(raw, dict):
        raise ValueError("checkpoint state must be an object")
    restored = _state(cast(dict[str, Any], raw))
    if set(restored.players) != set(state.players):
        raise ValueError("checkpoint players do not match configuration")
    for field_name in (
        "players",
        "properties",
        "current_player_id",
        "turn_phase",
        "settlement_operations",
        "next_settlement_operation_id",
        "complete_rounds",
        "finished",
        "end_reason",
        "rankings",
        "chance_draw_pile",
        "chance_discard_pile",
        "community_chest_draw_pile",
        "community_chest_discard_pile",
        "ongoing_effects",
        "pending_theft_thief_id",
        "pending_theft_target_id",
        "pending_theft_source_card_id",
        "consecutive_doubles",
        "round_player_ids",
        "completed_round_player_ids",
    ):
        setattr(state, field_name, getattr(restored, field_name))
    encoded = document.get("rng_state")
    try:
        if not isinstance(encoded, list):
            raise ValueError("checkpoint rng_state must be an array")
        rng.setstate(_random_state(cast(list[Any], encoded)))
    except (TypeError, ValueError, IndexError) as error:
        raise ValueError("checkpoint contains invalid rng_state") from error


def _operation(value: SettlementOperation) -> dict[str, Any]:
    return {
        "operation_id": value.operation_id,
        "kind": value.kind.value,
        "player_id": value.player_id,
        "source": value.source,
        "status": value.status.value,
        "recipient_id": value.recipient_id,
        "amount": value.amount,
        "steps": value.steps,
        "destination": value.destination,
        "dice_total": value.dice_total,
        "collect_go_salary": value.collect_go_salary,
        "allow_build": value.allow_build,
        "resume_phase": value.resume_phase.value if value.resume_phase else None,
        "resume_player_id": value.resume_player_id,
        "deck": value.deck.value if value.deck else None,
        "alliance_partner_id": value.alliance_partner_id,
    }


def _state(raw: dict[str, Any]) -> GameState:
    players = {
        key: PlayerState(
            player_id=str(v["player_id"]),
            seat=int(v["seat"]),
            cash=int(v["cash"]),
            position=int(v["position"]),
            properties=set(map(int, v["properties"])),
            jail_status=JailStatus(v["jail_status"]),
            jail_roll_attempts=int(v["jail_roll_attempts"]),
            bankrupt=bool(v["bankrupt"]),
            survived_turns=int(v["survived_turns"]),
            chance_cards=list(v["chance_cards"]),
            community_get_out_of_jail_cards=list(v["community_get_out_of_jail_cards"]),
            rent_waivers=int(v["rent_waivers"]),
        )
        for key, v in _object(raw, "players").items()
    }
    properties = {
        int(key): PropertyState(
            owner_id=v["owner_id"],
            building_level=int(v["building_level"]),
            mortgaged=bool(v["mortgaged"]),
        )
        for key, v in _object(raw, "properties").items()
    }
    return GameState(
        players=players,
        properties=properties,
        current_player_id=str(raw["current_player_id"]),
        turn_phase=TurnPhase(raw["turn_phase"]),
        settlement_operations=[_operation_from(v) for v in raw["settlement_operations"]],
        next_settlement_operation_id=int(raw["next_settlement_operation_id"]),
        complete_rounds=int(raw["complete_rounds"]),
        finished=bool(raw["finished"]),
        end_reason=EndReason(raw["end_reason"]) if raw["end_reason"] else None,
        rankings=tuple(raw["rankings"]),
        chance_draw_pile=list(raw["chance_draw_pile"]),
        chance_discard_pile=list(raw["chance_discard_pile"]),
        community_chest_draw_pile=list(raw["community_chest_draw_pile"]),
        community_chest_discard_pile=list(raw["community_chest_discard_pile"]),
        ongoing_effects=[
            OngoingEffect(
                kind=OngoingEffectKind(v["kind"]),
                source_player_id=str(v["source_player_id"]),
                remaining_turns=int(v["remaining_turns"]),
                activation_turn=int(v["activation_turn"]),
                target_player_id=v["target_player_id"],
                color_group=v["color_group"],
            )
            for v in raw["ongoing_effects"]
        ],
        pending_theft_thief_id=raw["pending_theft_thief_id"],
        pending_theft_target_id=raw["pending_theft_target_id"],
        pending_theft_source_card_id=raw["pending_theft_source_card_id"],
        consecutive_doubles=int(raw["consecutive_doubles"]),
        round_player_ids=tuple(raw["round_player_ids"]),
        completed_round_player_ids=set(raw["completed_round_player_ids"]),
    )


def _operation_from(v: dict[str, Any]) -> SettlementOperation:
    return SettlementOperation(
        operation_id=int(v["operation_id"]),
        kind=SettlementOperationKind(v["kind"]),
        player_id=str(v["player_id"]),
        source=str(v["source"]),
        status=SettlementOperationStatus(v["status"]),
        recipient_id=v["recipient_id"],
        amount=v["amount"],
        steps=v["steps"],
        destination=v["destination"],
        dice_total=v["dice_total"],
        collect_go_salary=bool(v["collect_go_salary"]),
        allow_build=bool(v["allow_build"]),
        resume_phase=TurnPhase(v["resume_phase"]) if v["resume_phase"] else None,
        resume_player_id=v["resume_player_id"],
        deck=CardDeck(v["deck"]) if v["deck"] else None,
        alliance_partner_id=v["alliance_partner_id"],
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        items = cast(tuple[Any, ...], value)
        return [_json_value(item) for item in items]
    if isinstance(value, list):
        items = cast(list[Any], value)
        return [_json_value(item) for item in items]
    if isinstance(value, (int, float, str)) or value is None:
        return value
    raise TypeError("random state contains a non-JSON value")


def _random_state(value: list[Any]) -> tuple[Any, ...]:
    if len(value) != 3 or not isinstance(value[0], int) or not isinstance(value[1], list):
        raise ValueError("random state has an invalid shape")
    inner_values = cast(list[Any], value[1])
    inner = tuple(
        item for item in inner_values if isinstance(item, int) and not isinstance(item, bool)
    )
    gaussian = value[2]
    if (
        len(inner) != len(inner_values)
        or isinstance(gaussian, bool)
        or (gaussian is not None and not isinstance(gaussian, (int, float)))
    ):
        raise ValueError("random state has invalid values")
    return (value[0], inner, gaussian)


def _object(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"checkpoint field {key} must be an object")
    return cast(dict[str, Any], value)
