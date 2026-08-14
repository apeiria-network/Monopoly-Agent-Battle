"""Run games through the validated decision protocol without an LLM dependency."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict

from monopoly_agent_battle.decision.models import (
    DecisionRequest,
    decision_request_record,
    validation_record,
)
from monopoly_agent_battle.decision.prompts import render_decision_prompt
from monopoly_agent_battle.decision.protocol import (
    command_from_option,
    option_command_payload,
    parse_and_validate,
)
from monopoly_agent_battle.decision.requests import GameCommand, build_decision_request
from monopoly_agent_battle.domain.commands import EndTurn, RollDice
from monopoly_agent_battle.domain.models import GameEvent, JailStatus, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.runner import ScriptedRunResult, state_snapshot
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts

RawDecisionController = Callable[[str], str]


class DeterministicPolicyController:
    """Choose the engine-defined default option for each request."""

    def __call__(self, request_text: str) -> str:
        """Return a valid response for the default candidate in the rendered prompt."""
        options_text = request_text.split("## 合法候选操作\n", maxsplit=1)[1]
        options = json.JSONDecoder().raw_decode(options_text)[0]
        default = next(option for option in options if option["is_default"])
        return json.dumps(
            {"selected_option": default["option_id"], "reasoning": "选择系统默认合法操作。"},
            ensure_ascii=False,
        )


def run_decision_game(
    engine: GameEngine,
    controller: RawDecisionController,
    artifacts: RunArtifacts | None = None,
    *,
    max_connection_retries: int = 2,
) -> ScriptedRunResult:
    """Drive a game by validating controller output and auditing deterministic fallbacks."""
    events: list[GameEvent] = []
    sequence = 1
    while not engine.state.finished:
        automatic_command = _automatic_command(engine)
        if automatic_command is not None:
            events.extend(_execute_and_audit(engine, automatic_command, artifacts))
            continue
        request = build_decision_request(engine, sequence)
        raw_response, retries = _request_response(
            controller, request, artifacts, max_connection_retries=max_connection_retries
        )
        attempted_validation = parse_and_validate(raw_response, request)
        validation = attempted_validation
        fallback = not attempted_validation.valid
        if fallback:
            default = next(option for option in request.options if option.is_default)
            validation = parse_and_validate(
                json.dumps(
                    {"selected_option": default.option_id, "reasoning": "系统回退至默认合法操作。"},
                    ensure_ascii=False,
                ),
                request,
            )
            if artifacts is not None:
                artifacts.append_runtime(
                    "decision_fallback",
                    {"decision_id": request.decision_id, "option_id": default.option_id},
                )
        if validation.option is None:
            raise AssertionError("validated decision has no selected option")
        command = command_from_option(request, validation.option)
        command_events = _execute_and_audit(engine, command, artifacts)
        events.extend(command_events)
        if artifacts is not None:
            artifacts.append_decision(
                {
                    "request": decision_request_record(request),
                    "attempted_response": raw_response,
                    "attempted_validation": validation_record(attempted_validation),
                    "validation": validation_record(validation),
                    "connection_retries": retries,
                    "fallback": fallback,
                    "executed_command": option_command_payload(request, validation.option),
                }
            )
        sequence += 1
    if artifacts is not None:
        artifacts.write_result(state_snapshot(engine.state, "completed"))
    return ScriptedRunResult(tuple(events), "completed")


def _automatic_command(engine: GameEngine) -> RollDice | EndTurn | None:
    """Return the forced progression command, if the current state has no player choice."""
    state = engine.state
    player = state.players[state.current_player_id]
    if state.turn_phase is TurnPhase.ROLLING and player.jail_status in {
        JailStatus.FREE,
        JailStatus.WAITING,
    }:
        return RollDice(player.player_id)
    if state.turn_phase is TurnPhase.TURN_COMPLETE:
        return EndTurn(player.player_id)
    return None


def _execute_and_audit(
    engine: GameEngine,
    command: GameCommand,
    artifacts: RunArtifacts | None,
) -> list[GameEvent]:
    """Execute one command and write its replay-compatible event records."""
    command_events = engine.execute(command)
    if artifacts is not None:
        artifacts.append_event(
            "command_executed",
            {"command_type": type(command).__name__, "command": asdict(command)},
        )
        for event in command_events:
            artifacts.append_event(event.event_type, event.payload)
    return command_events


def _request_response(
    controller: RawDecisionController,
    request: DecisionRequest,
    artifacts: RunArtifacts | None,
    *,
    max_connection_retries: int,
) -> tuple[str, int]:
    request_text = render_decision_prompt(request)
    for retry in range(max_connection_retries + 1):
        try:
            return controller(request_text), retry
        except ConnectionError as error:
            if artifacts is not None:
                artifacts.append_runtime(
                    "controller_connection_error",
                    {"decision_id": request.decision_id, "retry": retry, "error": str(error)},
                )
    return "", max_connection_retries
