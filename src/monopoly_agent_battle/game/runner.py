"""Auditable execution of deterministic, non-LLM game scripts."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from monopoly_agent_battle.config.models import GameConfig
from monopoly_agent_battle.domain.models import (
    GameEvent,
    GameState,
    SettlementOperation,
    TurnPhase,
)
from monopoly_agent_battle.game.controllers import ScriptedController
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


@dataclass(frozen=True, slots=True)
class ScriptedRunResult:
    events: tuple[GameEvent, ...]
    status: str


def run_scripted_game(
    config: GameConfig,
    controller: ScriptedController,
    artifacts: RunArtifacts | None = None,
    engine: GameEngine | None = None,
) -> ScriptedRunResult:
    """Run a fixed command sequence and optionally persist its audit trail."""
    engine = engine or GameEngine(config)
    events: list[GameEvent] = []
    status = "script_exhausted"
    while not engine.state.finished:
        try:
            command = controller.next_command()
        except StopIteration:
            if engine.state.turn_phase in {
                TurnPhase.FORCED_DISCARD,
                TurnPhase.THEFT_CARD_SELECTION,
            }:
                status = "awaiting_decision"
            break
        command_events = engine.execute(command)
        events.extend(command_events)
        if artifacts is not None:
            artifacts.append_event(
                "command_executed",
                {"command_type": type(command).__name__, "command": asdict(command)},
            )
            for event in command_events:
                artifacts.append_event(event.event_type, event.payload)
    if engine.state.finished:
        status = "completed"
    if artifacts is not None:
        artifacts.write_result(state_snapshot(engine.state, status))
    return ScriptedRunResult(tuple(events), status)


def state_snapshot(state: GameState, status: str) -> dict[str, object]:
    """Return the JSON-safe state projection used by results and replay checks."""
    return {
        "status": status,
        "end_reason": state.end_reason.value if state.end_reason is not None else None,
        "complete_rounds": state.complete_rounds,
        "rankings": list(state.rankings),
        "current_player_id": state.current_player_id,
        "turn_phase": state.turn_phase.value,
        "next_settlement_operation_id": state.next_settlement_operation_id,
        "settlement_operations": [
            _operation_snapshot(operation) for operation in state.settlement_operations
        ],
        "chance_draw_pile": list(state.chance_draw_pile),
        "chance_discard_pile": list(state.chance_discard_pile),
        "community_chest_draw_pile": list(state.community_chest_draw_pile),
        "community_chest_discard_pile": list(state.community_chest_discard_pile),
        "ongoing_effects": [
            {
                "kind": effect.kind.value,
                "source_player_id": effect.source_player_id,
                "remaining_turns": effect.remaining_turns,
                "activation_turn": effect.activation_turn,
                "target_player_id": effect.target_player_id,
                "color_group": effect.color_group,
            }
            for effect in state.ongoing_effects
        ],
        "pending_theft": {
            "thief_id": state.pending_theft_thief_id,
            "target_id": state.pending_theft_target_id,
            "source_card_id": state.pending_theft_source_card_id,
        },
        "players": {
            player_id: {
                "cash": player.cash,
                "position": player.position,
                "bankrupt": player.bankrupt,
                "jail_status": player.jail_status.value,
                "survived_turns": player.survived_turns,
                "properties": sorted(player.properties),
                "chance_cards": list(player.chance_cards),
                "community_get_out_of_jail_cards": list(player.community_get_out_of_jail_cards),
                "rent_waivers": player.rent_waivers,
            }
            for player_id, player in state.players.items()
        },
        "properties": {
            str(position): {
                "owner_id": property_state.owner_id,
                "building_level": property_state.building_level,
                "mortgaged": property_state.mortgaged,
            }
            for position, property_state in state.properties.items()
        },
    }


def _operation_snapshot(operation: SettlementOperation) -> dict[str, object]:
    return {
        "operation_id": operation.operation_id,
        "kind": operation.kind.value,
        "player_id": operation.player_id,
        "source": operation.source,
        "status": operation.status.value,
        "recipient_id": operation.recipient_id,
        "amount": operation.amount,
        "steps": operation.steps,
        "destination": operation.destination,
        "dice_total": operation.dice_total,
        "collect_go_salary": operation.collect_go_salary,
        "allow_build": operation.allow_build,
        "resume_phase": operation.resume_phase.value if operation.resume_phase else None,
        "resume_player_id": operation.resume_player_id,
        "deck": operation.deck.value if operation.deck else None,
    }
