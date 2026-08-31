"""Stage 6 schema and accounting checks for persisted LLM call audits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from monopoly_agent_battle.agents.baseline import BaselineAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.runner import (
    DispatchController,
    RawDecisionController,
    run_decision_game,
)
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.mock_client import MockLLMClient
from monopoly_agent_battle.llm.protocol import LLMConnectionError, LLMRequest
from monopoly_agent_battle.llm.recording_client import RecordingLLMClient
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts

CALL_FIELDS = {
    "call_id",
    "caller_role",
    "model",
    "seed",
    "temperature",
    "max_tokens",
    "input_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "thinking_tokens",
    "duration_ms",
    "tool_calls",
    "tool_call_failures",
    "response_summary",
    "error",
}
COUNT_FIELDS = {
    "call_id",
    "input_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "thinking_tokens",
    "duration_ms",
    "tool_calls",
    "tool_call_failures",
}


def _config(output_directory: Path) -> GameConfig:
    players = tuple(
        PlayerConfig(player_id=player_id, seat=seat, model_profile="mock")
        for seat, player_id in enumerate(("a", "b"), start=1)
    )
    return GameConfig(
        game_id="stage6-llm-schema",
        experiment_id="stage6-llm-schema",
        seed=4,
        players=players,
        max_complete_rounds=1,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        model_profiles={"mock": ModelProfile(provider="mock", model="mock-baseline-v1")},
        output_directory=output_directory,
    )


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_llm_call_schema_and_result_accounting_are_consistent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    artifacts = RunArtifacts.create(config)
    attempts = 0
    controllers: dict[str, RawDecisionController] = {}
    conversations: dict[str, AgentConversation] = {}

    def mixed_policy(request: LLMRequest) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LLMConnectionError("temporary outage")
        return MockLLMClient(seed=0).complete(request).content

    for player in config.players:
        conversation = AgentConversation(agent_id=player.player_id, window_turns=1)
        conversations[player.player_id] = conversation
        controllers[player.player_id] = BaselineAgent(
            player_id=player.player_id,
            client=RecordingLLMClient(MockLLMClient(policy=mixed_policy), artifacts),
            profile=config.model_profiles["mock"],
            conversation=conversation,
        )

    run_decision_game(
        GameEngine(config),
        DispatchController(controllers),
        artifacts,
        conversations=conversations,
    )

    calls = _records(artifacts.run_directory / "llm_calls.jsonl")
    runtime = _records(artifacts.run_directory / "runtime.jsonl")
    result = json.loads((artifacts.run_directory / "result.json").read_text(encoding="utf-8"))

    assert [record["call_id"] for record in calls] == list(range(1, len(calls) + 1))
    assert all(set(record) == CALL_FIELDS for record in calls)
    assert all(
        isinstance(record[field], int) and record[field] >= 0
        for record in calls
        for field in COUNT_FIELDS
    )
    assert all(record["cached_input_tokens"] <= record["input_tokens"] for record in calls)
    assert all(
        record["uncached_input_tokens"] == record["input_tokens"] - record["cached_input_tokens"]
        for record in calls
    )
    assert sum(record["error"] is not None for record in calls) == result["reconnect_events"]
    assert result["llm_calls"] == len(calls)
    assert (
        sum(record["event_type"] == "controller_connection_error" for record in runtime)
        == result["reconnect_events"]
    )
