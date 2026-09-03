"""Run games through the validated decision protocol without an LLM dependency."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from typing import cast

from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.context.validation_feedback import build_feedback
from monopoly_agent_battle.decision.models import (
    DecisionRequest,
    decision_request_record,
    validation_record,
)
from monopoly_agent_battle.decision.prompts import render_decision_question
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
from monopoly_agent_battle.performance.scoring import PerformanceWindowResult
from monopoly_agent_battle.performance.tracker import PerformanceTracker, evidence_from_trace

RawDecisionController = Callable[[DecisionRequest, str | None], str]

_DEFAULT_REASON = "选择系统默认合法操作。"
_FALLBACK_REASON = "多次重试仍未给出合法回复，自动选择系统默认选项。"


ConversationBinding = AgentConversation | Mapping[str, AgentConversation]


class DeterministicPolicyController:
    """Choose the engine-defined default option for each request."""

    uses_llm = False

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

    def record_final_decision_for(self, request: DecisionRequest, reply: str) -> None:
        """Forward final decision persistence to a court-aware controller."""
        recorder = getattr(self._controllers[request.player_id], "record_final_decision", None)
        if callable(recorder):
            recorder(request, reply)

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        return self._controllers[request.player_id](request, feedback)

    def uses_llm_for(self, request: DecisionRequest) -> bool:
        """Return whether the controller selected for a request makes LLM calls."""
        return bool(getattr(self._controllers[request.player_id], "uses_llm", True))

    def last_llm_call_count_for(self, request: DecisionRequest) -> int:
        """Return actual LLM calls made by the selected controller invocation."""
        selected = self._controllers[request.player_id]
        count = getattr(selected, "last_llm_call_count", 1)
        return int(cast(int, count))

    def court_trace_for(self, request: DecisionRequest) -> dict[str, object] | None:
        """Return private court evidence when the selected controller provides it."""
        trace = getattr(self._controllers[request.player_id], "court_trace", None)
        if not callable(trace):
            return None
        return cast(dict[str, object], trace())


def run_decision_game(
    engine: GameEngine,
    controller: RawDecisionController,
    artifacts: RunArtifacts | None = None,
    *,
    max_connection_retries: int = 2,
    conversations: Mapping[str, ConversationBinding] | None = None,
    performance_tracker: PerformanceTracker | None = None,
) -> ScriptedRunResult:
    """Drive a game by validating controller output and auditing deterministic fallbacks.

    When ``conversations`` is provided, each engine event is dispatched to every
    Agent's conversation for Stage 4C history tracking; ``turn_started`` events
    trigger ``start_turn`` on the matching Agent (rebuilding its segment-3 cache
    with its independent fixed 500-token cap). Validation-failure feedback is
    stashed on the conversation for the composer to render on retries.
    """
    events: list[GameEvent] = []
    sequence = 1
    llm_calls = 0
    reconnect_events = 0
    decision_fallbacks = 0
    llm_fallbacks = 0
    conv_map, decision_conversations = _normalize_conversations(conversations or {})
    turn_counters: dict[str, int] = dict.fromkeys(conv_map, 0)
    logged_segment3_warning_turns: set[tuple[str, int]] = set()
    # Bootstrap the first Agent's turn: the engine never emits ``turn_started``
    # for its initial player, so ``start_turn`` must be called explicitly here.
    initial_player_id = engine.state.current_player_id
    if performance_tracker is not None:
        _record_performance_windows(
            performance_tracker.start_turn(initial_player_id), artifacts, controller
        )
    if initial_player_id in conv_map or any(
        key.startswith(f"{initial_player_id}.") for key in conv_map
    ):
        for agent_id, conversation in conv_map.items():
            if agent_id != initial_player_id and not agent_id.startswith(f"{initial_player_id}."):
                continue
            turn_counters[agent_id] += 1
            conversation.start_turn(turn_counters[agent_id])
            _record_segment3_warning(
                conversation,
                agent_id,
                turn_counters[agent_id],
                logged_segment3_warning_turns,
                artifacts,
            )
    while not engine.state.finished:
        automatic_command = _automatic_command(engine)
        if automatic_command is not None:
            command_events = _execute_and_audit(engine, automatic_command, artifacts)
            _dispatch_events(
                command_events,
                conv_map,
                turn_counters,
                logged_segment3_warning_turns,
                artifacts,
                engine,
                performance_tracker,
                controller,
            )
            events.extend(command_events)
            continue
        request = build_decision_request(engine, sequence)
        current_conv = decision_conversations.get(request.player_id)
        uses_llm = _uses_llm(controller, request)
        (
            raw_response,
            connection_retries,
            validation_retries_used,
            llm_attempts,
            validation_errors,
        ) = _request_response(
            controller,
            request,
            artifacts,
            current_conv,
            max_connection_retries=max_connection_retries,
            validation_retries=engine.config.validation_retries,
        )
        if uses_llm:
            llm_calls += llm_attempts
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
            if uses_llm:
                llm_fallbacks += 1
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
        final_recorder = getattr(controller, "record_final_decision_for", None)
        if callable(final_recorder):
            final_recorder(request, persisted_reply)
        command = command_from_option(request, validation.option, validation.target)
        court_trace = _court_trace(controller, request)
        if performance_tracker is not None and court_trace is not None:
            evidence = evidence_from_trace(
                request, court_trace, validation.option.option_id, validation.target
            )
            if evidence is not None:
                performance_tracker.record_decision(request.player_id, evidence)
        command_events = _execute_and_audit(engine, command, artifacts)
        _dispatch_events(
            command_events,
            conv_map,
            turn_counters,
            logged_segment3_warning_turns,
            artifacts,
            engine,
            performance_tracker,
            controller,
        )
        events.extend(command_events)
        if artifacts is not None:
            decision_record: dict[str, object] = {
                "request": decision_request_record(request),
                "controller_type": "llm" if uses_llm else "non_llm",
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
            court_trace = (
                court_trace if court_trace is not None else _court_trace(controller, request)
            )
            if court_trace is not None:
                decision_record["court_trace"] = court_trace
            artifacts.append_decision(decision_record)
        sequence += 1
    if performance_tracker is not None:
        _record_performance_windows(performance_tracker.finalize(), artifacts, controller)
    if artifacts is not None:
        result = state_snapshot(engine.state, "completed")
        result.update(
            {
                "llm_calls": llm_calls,
                "reconnect_events": reconnect_events,
                "decision_fallbacks": decision_fallbacks,
                "llm_fallbacks": llm_fallbacks,
                "validity_status": _validity_status(llm_calls, llm_fallbacks),
                "llm_token_stats": _llm_token_stats(artifacts, llm_calls, llm_fallbacks),
            }
        )
        artifacts.write_result(result)
    return ScriptedRunResult(tuple(events), "completed")


def _normalize_conversations(
    conversations: Mapping[str, ConversationBinding],
) -> tuple[dict[str, AgentConversation], dict[str, AgentConversation]]:
    """Flatten role conversations while selecting one external decision conversation."""
    event_conversations: dict[str, AgentConversation] = {}
    decision_conversations: dict[str, AgentConversation] = {}
    for player_id, binding in conversations.items():
        if isinstance(binding, AgentConversation):
            event_conversations[player_id] = binding
            decision_conversations[player_id] = binding
            continue
        if not binding:
            continue
        for role, conversation in binding.items():
            event_conversations[f"{player_id}.{role}"] = conversation
        decision_conversations[player_id] = binding.get("emperor", next(iter(binding.values())))
    return event_conversations, decision_conversations


def _uses_llm(controller: RawDecisionController, request: DecisionRequest) -> bool:
    """Return the controller's LLM accounting classification for one request.

    Legacy bare callables retain the pre-random-baseline accounting behavior and
    count as LLM-backed unless they explicitly expose ``uses_llm = False``.
    """
    dispatch_method = getattr(controller, "uses_llm_for", None)
    if callable(dispatch_method):
        return bool(dispatch_method(request))
    return bool(getattr(controller, "uses_llm", True))


def _last_llm_call_count(controller: RawDecisionController, request: DecisionRequest) -> int:
    """Read optional per-invocation call metrics with legacy-safe defaults."""
    dispatch_method = getattr(controller, "last_llm_call_count_for", None)
    if callable(dispatch_method):
        return int(cast(int, dispatch_method(request)))
    return int(cast(int, getattr(controller, "last_llm_call_count", 1)))


def _court_trace(
    controller: RawDecisionController, request: DecisionRequest
) -> dict[str, object] | None:
    """Read optional court-only evidence without exposing it to the engine."""
    dispatch_method = getattr(controller, "court_trace_for", None)
    if callable(dispatch_method):
        return cast(dict[str, object], dispatch_method(request))
    trace = getattr(controller, "court_trace", None)
    if not callable(trace):
        return None
    return cast(dict[str, object], trace())


def _dispatch_events(
    engine_events: list[GameEvent],
    conversations: dict[str, AgentConversation],
    turn_counters: dict[str, int],
    logged_segment3_warning_turns: set[tuple[str, int]],
    artifacts: RunArtifacts | None,
    engine: GameEngine,
    performance_tracker: PerformanceTracker | None = None,
    controller: RawDecisionController | None = None,
) -> None:
    """Route engine events to every Agent conversation for history tracking."""
    for event in engine_events:
        complete_round = engine.state.complete_rounds
        if event.event_type == "turn_started":
            player_id = str(event.payload["player_id"])
            if performance_tracker is not None:
                results = performance_tracker.start_turn(player_id)
                _record_performance_windows(results, artifacts, controller)
            for agent_id, conversation in conversations.items():
                if agent_id == player_id or agent_id.startswith(f"{player_id}."):
                    turn_counters[agent_id] += 1
                    conversation.start_turn(turn_counters[agent_id])
                    _record_segment3_warning(
                        conversation,
                        agent_id,
                        turn_counters[agent_id],
                        logged_segment3_warning_turns,
                        artifacts,
                    )
                else:
                    conversation.append_event(event, complete_round)
        else:
            for conversation in conversations.values():
                conversation.append_event(event, complete_round)


def _record_performance_windows(
    results: Sequence[PerformanceWindowResult],
    artifacts: RunArtifacts | None,
    controller: RawDecisionController | None,
) -> None:
    for result in results:
        payload = result.as_dict()
        if artifacts is not None:
            artifacts.append_performance(payload)
        if controller is not None and payload["court"] == "qin_court":
            publisher = getattr(controller, "publish_performance", None)
            if callable(publisher):
                publisher(str(payload["player_id"]), payload)


def _record_segment3_warning(
    conversation: AgentConversation,
    agent_id: str,
    turn_num: int,
    logged_turns: set[tuple[str, int]],
    artifacts: RunArtifacts | None,
) -> None:
    warning = conversation.segment3_warning
    key = (agent_id, turn_num)
    if warning is None or artifacts is None or key in logged_turns:
        return
    logged_turns.add(key)
    artifacts.append_runtime(
        "segment3_overflow",
        {
            "agent_id": agent_id,
            "turn_num": turn_num,
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
    round_before = engine.state.complete_rounds
    command_events = engine.execute(command)
    round_after = engine.state.complete_rounds
    if artifacts is not None:
        artifacts.append_event(
            "command_executed",
            {"command_type": type(command).__name__, "command": asdict(command)},
        )
        for event in command_events:
            artifacts.append_event(event.event_type, event.payload)
            event_round = round_after if event.event_type == "turn_started" else round_before
            if event.event_type == "game_finished":
                event_round = round_after
            artifacts.append_game_broadcast(event, event_round)
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

    Returns ``(raw_response, connection_errors, validation_retries_used, llm_calls,
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
    llm_attempts = 0
    while True:
        try:
            raw_response = controller(request, feedback)
            llm_attempts += _last_llm_call_count(controller, request)
        except ConnectionError as error:
            llm_attempts += _last_llm_call_count(controller, request)
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
                return (
                    "",
                    connection_errors,
                    validation_retries_used,
                    llm_attempts,
                    validation_errors,
                )
            continue
        validation = parse_and_validate(raw_response, request)
        if validation.valid:
            return (
                raw_response,
                connection_errors,
                validation_retries_used,
                llm_attempts,
                validation_errors,
            )
        if validation_retries_used >= validation_retries:
            return (
                raw_response,
                connection_errors,
                validation_retries_used,
                llm_attempts,
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


def _validity_status(llm_calls: int, decision_fallbacks: int) -> str:
    """Mark a game invalid when LLM-triggered fallbacks reach 10% of calls."""
    if llm_calls > 0 and decision_fallbacks * 10 >= llm_calls:
        return "invalid"
    return "valid"


def _round4(value: float) -> float:
    return round(value, 4)


def _llm_token_stats(
    artifacts: RunArtifacts, llm_calls: int, llm_fallbacks: int
) -> dict[str, object]:
    """Aggregate per-player token usage from the persisted call & decision logs.

    Reads the already-flushed ``llm_calls.jsonl`` and ``decisions.jsonl`` (never
    touches the live LLM data path), grouped by ``caller_role`` player id.

    Two families of averages are reported per player:

    - Per successful *call* (``avg_*_tokens``): mean over calls with no
      ``error`` (failed calls carry 0 tokens and are excluded so they cannot
      drag these means down); ``successful_calls`` is that denominator.
    - Per *decision* (``per_decision.*``): the player's total token spend across
      **every** physical request for its decisions — including retries and, for
      a court player, all officers' calls — divided by the player's decision
      count from ``decisions.jsonl``. This captures "what one decision costs".
      Failed requests contribute 0 tokens but are counted in
      ``avg_requests_per_decision``.

    ``fallback_rate`` uses the same denominator as ``validity_status``:
    ``llm_fallbacks / llm_calls`` over every physical request.
    """
    log_path = artifacts.run_directory / "llm_calls.jsonl"
    groups: dict[str, dict[str, int]] = {}

    def _bucket(player_id: str) -> dict[str, int]:
        return groups.setdefault(
            player_id,
            {
                "successful_calls": 0,
                "cached": 0,
                "uncached": 0,
                "output": 0,
                "total_calls": 0,
                "total_cached": 0,
                "total_uncached": 0,
                "total_output": 0,
                "decisions": 0,
            },
        )

    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = cast(dict[str, object], json.loads(line))
            raw_role = record.get("caller_role")
            role = raw_role if isinstance(raw_role, str) else ""
            # ``player.role`` for court officers → group under the player id.
            bucket = _bucket(role.split(".", 1)[0])
            cached = int(cast(int, record.get("cached_input_tokens", 0) or 0))
            uncached = int(cast(int, record.get("uncached_input_tokens", 0) or 0))
            output = int(cast(int, record.get("output_tokens", 0) or 0))
            # Per-decision totals include every physical request (retries + all
            # officers); failed requests contribute 0 tokens but still count.
            bucket["total_calls"] += 1
            bucket["total_cached"] += cached
            bucket["total_uncached"] += uncached
            bucket["total_output"] += output
            if record.get("error") is not None:
                continue
            bucket["successful_calls"] += 1
            bucket["cached"] += cached
            bucket["uncached"] += uncached
            bucket["output"] += output

    decisions_path = artifacts.run_directory / "decisions.jsonl"
    if decisions_path.exists():
        for line in decisions_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = cast(dict[str, object], json.loads(line))
            request = record.get("request")
            player_id = (
                cast(dict[str, object], request).get("player_id")
                if isinstance(request, dict)
                else None
            )
            if isinstance(player_id, str):
                _bucket(player_id)["decisions"] += 1

    per_player: dict[str, object] = {}
    for player_id, bucket in sorted(groups.items()):
        count = bucket["successful_calls"]
        decisions = bucket["decisions"]
        per_player[player_id] = {
            "successful_calls": count,
            "avg_cached_input_tokens": _round4(bucket["cached"] / count) if count else 0.0,
            "avg_uncached_input_tokens": _round4(bucket["uncached"] / count) if count else 0.0,
            "avg_output_tokens": _round4(bucket["output"] / count) if count else 0.0,
            "per_decision": {
                "decisions": decisions,
                "avg_requests_per_decision": (
                    _round4(bucket["total_calls"] / decisions) if decisions else 0.0
                ),
                "avg_cached_input_tokens": (
                    _round4(bucket["total_cached"] / decisions) if decisions else 0.0
                ),
                "avg_uncached_input_tokens": (
                    _round4(bucket["total_uncached"] / decisions) if decisions else 0.0
                ),
                "avg_output_tokens": (
                    _round4(bucket["total_output"] / decisions) if decisions else 0.0
                ),
            },
        }
    return {
        "fallback_rate": _round4(llm_fallbacks / llm_calls) if llm_calls > 0 else 0.0,
        "per_player": per_player,
    }
