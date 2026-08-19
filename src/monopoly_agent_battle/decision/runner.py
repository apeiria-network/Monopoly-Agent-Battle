"""Run games through the validated decision protocol without an LLM dependency."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict

from monopoly_agent_battle.decision.models import (
    DecisionRequest,
    decision_request_record,
    validation_record,
)
from monopoly_agent_battle.decision.protocol import (
    command_from_option,
    default_option_json,
    option_command_payload,
    parse_and_validate,
)
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.commands import EndTurn, GameCommand, RollDice
from monopoly_agent_battle.domain.models import GameEvent, JailStatus, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.runner import ScriptedRunResult, state_snapshot
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts

RawDecisionController = Callable[[DecisionRequest, str | None], str]

_DEFAULT_REASON = "选择系统默认合法操作。"
_FALLBACK_REASON = "系统回退至默认合法操作。"
_VALIDATION_FEEDBACK = "你的上一次输出无效：{error}。请重新输出一个合法 JSON。"


class DeterministicPolicyController:
    """Choose the engine-defined default option for each request."""

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        """Return a valid response for the default candidate of the request."""
        default = next(option for option in request.options if option.is_default)
        return json.dumps(
            {
                "selected_option": default_option_json(default),
                "reason": _DEFAULT_REASON,
            },
            ensure_ascii=False,
        )


class DispatchController:
    """Forward each request to the controller bound to the requesting player."""

    def __init__(self, controllers: Mapping[str, RawDecisionController]) -> None:
        self._controllers = dict(controllers)

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        return self._controllers[request.player_id](request, feedback)


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
    llm_calls = 0
    reconnect_events = 0
    decision_fallbacks = 0
    while not engine.state.finished:
        automatic_command = _automatic_command(engine)
        if automatic_command is not None:
            events.extend(_execute_and_audit(engine, automatic_command, artifacts))
            continue
        request = build_decision_request(engine, sequence)
        raw_response, connection_retries, validation_retries_used, attempts, validation_errors = (
            _request_response(
                controller,
                request,
                artifacts,
                max_connection_retries=max_connection_retries,
                validation_retries=engine.config.validation_retries,
            )
        )
        llm_calls += attempts
        reconnect_events += connection_retries
        attempted_validation = parse_and_validate(raw_response, request)
        validation = attempted_validation
        fallback = not attempted_validation.valid
        if fallback:
            decision_fallbacks += 1
            default = next(option for option in request.options if option.is_default)
            validation = parse_and_validate(
                json.dumps(
                    {
                        "selected_option": default_option_json(default),
                        "reason": _FALLBACK_REASON,
                    },
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
        command = command_from_option(request, validation.option, validation.target)
        command_events = _execute_and_audit(engine, command, artifacts)
        events.extend(command_events)
        if artifacts is not None:
            artifacts.append_decision(
                {
                    "request": decision_request_record(request),
                    "attempted_response": raw_response,
                    "attempted_validation": validation_record(attempted_validation),
                    "validation": validation_record(validation),
                    "connection_retries": connection_retries,
                    "validation_retries": validation_retries_used,
                    "validation_errors": validation_errors,
                    "fallback": fallback,
                    "executed_command": option_command_payload(
                        request, validation.option, validation.target
                    ),
                }
            )
        sequence += 1
    if artifacts is not None:
        result = state_snapshot(engine.state, "completed")
        result.update(
            {
                "llm_calls": llm_calls,
                "reconnect_events": reconnect_events,
                "decision_fallbacks": decision_fallbacks,
                "validity_status": _validity_status(llm_calls, reconnect_events),
            }
        )
        artifacts.write_result(result)
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
    validation_retries: int,
) -> tuple[str, int, int, int, list[str]]:
    """Obtain a validated response, retrying connections and invalid output.

    Returns ``(raw_response, connection_errors, validation_retries_used, attempts,
    validation_errors)``. A connection error retries up to ``max_connection_retries``
    times; an invalid response re-sends the same request with the validation error
    as temporary feedback up to ``validation_retries`` times. ``validation_errors``
    collects the errors that were sent as feedback so retries stay auditable.
    Budget exhaustion returns an empty/invalid raw response so the caller falls
    back to the default option.
    """
    feedback: str | None = None
    connection_errors = 0
    validation_retries_used = 0
    validation_errors: list[str] = []
    attempts = 0
    while True:
        attempts += 1
        try:
            raw_response = controller(request, feedback)
        except ConnectionError as error:
            connection_errors += 1
            if artifacts is not None:
                artifacts.append_runtime(
                    "controller_connection_error",
                    {
                        "decision_id": request.decision_id,
                        "retry": connection_errors - 1,
                        "error": str(error),
                    },
                )
            if connection_errors > max_connection_retries:
                return "", connection_errors, validation_retries_used, attempts, validation_errors
            continue
        validation = parse_and_validate(raw_response, request)
        if validation.valid:
            return (
                raw_response,
                connection_errors,
                validation_retries_used,
                attempts,
                validation_errors,
            )
        if validation_retries_used >= validation_retries:
            return (
                raw_response,
                connection_errors,
                validation_retries_used,
                attempts,
                validation_errors,
            )
        validation_retries_used += 1
        validation_errors.append(validation.error or "")
        feedback = _VALIDATION_FEEDBACK.format(error=validation.error)


def _validity_status(llm_calls: int, reconnect_events: int) -> str:
    """Mark a game invalid when reconnect events reach 10% of all LLM calls."""
    if llm_calls > 0 and reconnect_events * 10 >= llm_calls:
        return "invalid"
    return "valid"
