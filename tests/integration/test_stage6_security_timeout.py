"""Stage 6 harmless-marker security and timeout audit integration coverage."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from monopoly_agent_battle.agents.baseline import BaselineAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.runner import (
    DispatchController,
    RawDecisionController,
    run_decision_game,
)
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.openai_compatible_client import OpenAICompatibleClient
from monopoly_agent_battle.llm.recording_client import RecordingLLMClient
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts

_PRIVATE_MARKER = "PRIVATE_MARKER_123"
_ENV_NAME = "STAGE6_PLACEHOLDER_API_KEY"


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _config(output_directory: Path) -> GameConfig:
    profile = ModelProfile(
        provider="openai_compatible",
        base_url="https://placeholder.invalid/v1",
        api_key_env=_ENV_NAME,
        model="placeholder-model",
        timeout_seconds=0.25,
    )
    return GameConfig(
        game_id="stage6-security-timeout",
        experiment_id="stage6-security-timeout",
        seed=7,
        players=tuple(
            PlayerConfig(player_id=player_id, seat=seat, model_profile="remote")
            for seat, player_id in enumerate(("a", "b"), start=1)
        ),
        max_complete_rounds=1,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        model_profiles={"remote": profile},
        output_directory=output_directory,
    )


def test_timeout_retries_are_auditable_and_private_marker_never_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_ENV_NAME, _PRIVATE_MARKER)
    observed_timeouts: list[float] = []

    def timeout_urlopen(_request: urllib.request.Request, timeout: float) -> None:
        observed_timeouts.append(timeout)
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(urllib.request, "urlopen", timeout_urlopen)
    config = _config(tmp_path)
    artifacts = RunArtifacts.create(config)
    controllers: dict[str, RawDecisionController] = {}
    conversations: dict[str, AgentConversation] = {}
    for player in config.players:
        conversation = AgentConversation(agent_id=player.player_id, window_turns=1)
        conversations[player.player_id] = conversation
        profile = config.model_profiles["remote"]
        controllers[player.player_id] = BaselineAgent(
            player_id=player.player_id,
            client=RecordingLLMClient(OpenAICompatibleClient(profile), artifacts),
            profile=profile,
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
    decisions = _records(artifacts.run_directory / "decisions.jsonl")
    result = json.loads((artifacts.run_directory / "result.json").read_text(encoding="utf-8"))
    connection_events = [
        record for record in runtime if record["event_type"] == "controller_connection_error"
    ]

    assert observed_timeouts and set(observed_timeouts) == {0.25}
    assert len(calls) == result["llm_calls"] == result["reconnect_events"]
    assert len(connection_events) == result["reconnect_events"]
    assert all(record["error"].endswith("TimeoutError") for record in calls)
    assert all(record["response_summary"] is None for record in calls)
    assert all(record["fallback"] for record in decisions)
    assert all(record["connection_retries"] == 3 for record in decisions)
    for decision in decisions:
        decision_id = decision["request"]["decision_id"]
        assert [
            event["payload"]["retry"]
            for event in connection_events
            if event["payload"]["decision_id"] == decision_id
        ] == [0, 1, 2]

    artifact_files = [path for path in artifacts.run_directory.iterdir() if path.is_file()]
    assert artifact_files
    for path in artifact_files:
        assert _PRIVATE_MARKER not in path.read_text(encoding="utf-8")
