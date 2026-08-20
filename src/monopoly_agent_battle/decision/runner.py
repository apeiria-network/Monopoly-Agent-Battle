"""Run games through the validated decision protocol without an LLM dependency."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict

from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.context.token_guard import estimate_tokens
from monopoly_agent_battle.context.validation_feedback import build_feedback
from monopoly_agent_battle.decision.models import (
    DecisionRequest,
    decision_request_record,
    validation_record,
)
from monopoly_agent_battle.decision.prompts import (
    render_decision_question,
    render_system_prompt,
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
_FALLBACK_REASON = "多次重试仍未给出合法回复，自动选择系统默认选项。"
_CURRENT_SEGMENTS_TOKEN_RESERVE = 1500


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
    conversations: Mapping[str, AgentConversation] | None = None,
) -> ScriptedRunResult:
    """Drive a game by validating controller output and auditing deterministic fallbacks.

    When ``conversations`` is provided, each engine event is dispatched to every
    Agent's conversation for Stage 4C history tracking; ``turn_started`` events
    trigger ``start_turn`` on the matching Agent (rebuilding its segment-3 cache
    against the token budget). Validation-failure feedback is stashed on the
    conversation for the composer to render on retries.
    """
    events: list[GameEvent] = []
    sequence = 1
    llm_calls = 0
    reconnect_events = 0
    decision_fallbacks = 0
    conv_map: dict[str, AgentConversation] = dict(conversations or {})
    turn_counters: dict[str, int] = dict.fromkeys(conv_map, 0)
    segment3_budget = _segment3_budget(engine)
    # Bootstrap the first Agent's turn: the engine never emits ``turn_started``
    # for its initial player, so ``start_turn`` must be called explicitly here.
    initial_player_id = engine.state.current_player_id
    if initial_player_id in conv_map:
        turn_counters[initial_player_id] += 1
        conv_map[initial_player_id].start_turn(
            turn_counters[initial_player_id],
            segment3_budget_tokens=segment3_budget,
        )
        _log_start_turn_warning(conv_map[initial_player_id], initial_player_id, artifacts)
    while not engine.state.finished:
        automatic_command = _automatic_command(engine)
        if automatic_command is not None:
            command_events = _execute_and_audit(engine, automatic_command, artifacts)
            _dispatch_events(
                command_events, conv_map, turn_counters, segment3_budget, artifacts, engine
            )
            events.extend(command_events)
            continue
        request = build_decision_request(engine, sequence)
        current_conv = conv_map.get(request.player_id)
        (
            raw_response,
            connection_retries,
            validation_retries_used,
            attempts,
            validation_errors,
        ) = _request_response(
            controller,
            request,
            artifacts,
            current_conv,
            max_connection_retries=max_connection_retries,
            validation_retries=engine.config.validation_retries,
        )
        llm_calls += attempts
        reconnect_events += connection_retries
        attempted_validation = parse_and_validate(raw_response, request)
        validation = attempted_validation
        fallback = not attempted_validation.valid
        # ``persisted_reply`` is what gets written to segment 4's DecisionEntry.
        # For a normal success it's the AI's own reply; for a fallback we
        # substitute a synthesized default-option JSON whose ``reason``
        # explains the auto-selection so the AI can see it in later turns.
        persisted_reply = raw_response
        if fallback:
            decision_fallbacks += 1
            default = next(option for option in request.options if option.is_default)
            fallback_reply = json.dumps(
                {
                    "selected_option": default_option_json(default),
                    "reason": _FALLBACK_REASON,
                },
                ensure_ascii=False,
            )
            persisted_reply = fallback_reply
            validation = parse_and_validate(fallback_reply, request)
            if artifacts is not None:
                artifacts.append_runtime(
                    "decision_fallback",
                    {"decision_id": request.decision_id, "option_id": default.option_id},
                )
        if validation.option is None:
            raise AssertionError("validated decision has no selected option")
        if current_conv is not None:
            current_conv.append_decision(
                decision_id=request.decision_id,
                question_summary=render_decision_question(request),
                assistant_reply=persisted_reply,
            )
            _log_segment3_warning(current_conv, request, artifacts)
        command = command_from_option(request, validation.option, validation.target)
        command_events = _execute_and_audit(engine, command, artifacts)
        _dispatch_events(
            command_events, conv_map, turn_counters, segment3_budget, artifacts, engine
        )
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


def _segment3_budget(engine: GameEngine) -> int:
    """Return the token budget allocated to segment 3 for this game.

    The current segments (1+2 rules, 5-10 current decision) are given a fixed
    reserve; segment 3 gets whatever remains inside ``context_token_cap``.
    """
    cap = engine.config.context_token_cap or 6000
    # Rough estimate of segment 1+2 using a synthetic reference; we cannot build
    # a DecisionRequest without an active phase so we use the raw rules text.
    from monopoly_agent_battle.context.rules import load_game_rules

    rules_tokens = estimate_tokens(load_game_rules())
    return max(200, cap - rules_tokens - _CURRENT_SEGMENTS_TOKEN_RESERVE)


def _dispatch_events(
    engine_events: list[GameEvent],
    conversations: dict[str, AgentConversation],
    turn_counters: dict[str, int],
    segment3_budget: int,
    artifacts: RunArtifacts | None,
    engine: GameEngine,
) -> None:
    """Route engine events to every Agent conversation for history tracking."""
    if not conversations:
        return
    for event in engine_events:
        complete_round = engine.state.complete_rounds
        if event.event_type == "turn_started":
            player_id = str(event.payload["player_id"])
            for agent_id, conversation in conversations.items():
                if agent_id == player_id:
                    turn_counters[agent_id] += 1
                    conversation.start_turn(
                        turn_counters[agent_id],
                        segment3_budget_tokens=segment3_budget,
                    )
                    _log_start_turn_warning(conversation, agent_id, artifacts)
                else:
                    conversation.append_event(event, complete_round)
        else:
            for conversation in conversations.values():
                conversation.append_event(event, complete_round)


def _log_start_turn_warning(
    conversation: AgentConversation,
    agent_id: str,
    artifacts: RunArtifacts | None,
) -> None:
    warning = conversation.segment3_warning
    if warning is None or artifacts is None:
        return
    artifacts.append_runtime(
        "segment3_overflow",
        {
            "agent_id": agent_id,
            "kind": warning.kind,
            "detail": warning.detail,
        },
    )


def _log_segment3_warning(
    conversation: AgentConversation,
    request: DecisionRequest,
    artifacts: RunArtifacts | None,
) -> None:
    """Log segment-3 overflow when observed via a decision request (idempotent).

    ``AgentConversation.segment3_warning`` is set once per turn (in
    ``start_turn``) so the guard here catches the case where a decision is
    generated at game start before a ``turn_started`` event has fired.
    """
    warning = conversation.segment3_warning
    if warning is None or artifacts is None:
        return
    artifacts.append_runtime(
        "segment3_overflow_at_decision",
        {
            "decision_id": request.decision_id,
            "kind": warning.kind,
            "detail": warning.detail,
        },
    )


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
    conversation: AgentConversation | None,
    *,
    max_connection_retries: int,
    validation_retries: int,
) -> tuple[str, int, int, int, list[str]]:
    """Obtain a validated response, retrying connections and invalid output.

    Returns ``(raw_response, connection_errors, validation_retries_used, attempts,
    validation_errors)``. A connection error retries up to ``max_connection_retries``
    times; an invalid response records an ``ErrorEntry`` on the conversation
    (so segment 4 replays ``assistant(bad_reply) + user(feedback)`` for the
    rest of this turn) and re-sends the same request up to ``validation_retries``
    times. On the fresh conversation-less path (legacy tests using
    ``DeterministicPolicyController``) the feedback is passed to the
    controller via its ``feedback`` parameter instead.
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
        error_text = validation.error or ""
        validation_errors.append(error_text)
        feedback = build_feedback(validation, request)
        if conversation is not None:
            conversation.append_error(
                decision_id=request.decision_id,
                question_summary=render_decision_question(request),
                bad_reply=raw_response,
                feedback_text=feedback,
            )


def _validity_status(llm_calls: int, reconnect_events: int) -> str:
    """Mark a game invalid when reconnect events reach 10% of all LLM calls."""
    if llm_calls > 0 and reconnect_events * 10 >= llm_calls:
        return "invalid"
    return "valid"


# render_system_prompt import is retained for potential future runner-side pre-render use.
_ = render_system_prompt
