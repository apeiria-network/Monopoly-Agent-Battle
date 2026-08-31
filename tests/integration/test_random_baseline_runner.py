"""Integration tests for random non-LLM baseline game runs."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, cast

from monopoly_agent_battle.agents.baseline import BaselineAgent
from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.cli.main import run_play
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.runner import DispatchController, run_decision_game
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.replay import verify_run
from monopoly_agent_battle.llm.mock_client import MockLLMClient
from monopoly_agent_battle.llm.protocol import LLMConnectionError, LLMRequest
from monopoly_agent_battle.llm.recording_client import RecordingLLMClient
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


def _write_random_config(path: Path, output_directory: Path, game_id: str) -> None:
    path.write_text(
        f"""game_id: {game_id}
experiment_id: random-integration
seed: 17
players:
  - player_id: a
    seat: 1
    controller_type: random_baseline
  - player_id: b
    seat: 2
    controller_type: random_baseline
rules_version: classic-level0-v1
rules_level: 0
board_data_version: classic-us-40-v1
card_data_version: classic-cards-v1
max_complete_rounds: 3
output_directory: {output_directory.as_posix()}
""",
        encoding="utf-8",
    )


def _records(path: Path) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _random_controller(seed: int, seat: int, player_id: str) -> RandomBaselineController:
    material = f"random-baseline-v1:{seed}:{seat}:{player_id}".encode()
    derived_seed = int.from_bytes(hashlib.sha256(material).digest(), "big")
    return RandomBaselineController(random.Random(derived_seed))


def _commands(run_directory: Path) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], record["executed_command"])
        for record in _records(run_directory / "decisions.jsonl")
    ]


def _mixed_config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="mixed-random-llm",
        experiment_id="random-integration",
        seed=17,
        players=(
            PlayerConfig(player_id="a", seat=1, controller_type="random_baseline"),
            PlayerConfig(player_id="b", seat=2, model_profile="mock"),
        ),
        max_complete_rounds=3,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        model_profiles={"mock": ModelProfile(provider="mock", model="mock-baseline-v1")},
        output_directory=output_directory,
    )


def test_mixed_game_records_and_counts_only_llm_player_calls(tmp_path: Path) -> None:
    config = _mixed_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    conversations: dict[str, AgentConversation] = {}
    client = RecordingLLMClient(MockLLMClient(seed=0), artifacts)
    conversation = AgentConversation(agent_id="b", window_turns=config.window_turns)
    conversations["b"] = conversation
    controller = DispatchController(
        {
            "a": _random_controller(config.seed, 1, "a"),
            "b": BaselineAgent(
                player_id="b",
                client=client,
                profile=config.model_profiles["mock"],
                conversation=conversation,
            ),
        }
    )

    run_decision_game(GameEngine(config), controller, artifacts, conversations=conversations)

    decisions = _records(artifacts.run_directory / "decisions.jsonl")
    llm_calls = _records(artifacts.run_directory / "llm_calls.jsonl")
    result = json.loads((artifacts.run_directory / "result.json").read_text(encoding="utf-8"))
    random_decisions = [record for record in decisions if record["controller_type"] == "non_llm"]
    llm_decisions = [record for record in decisions if record["controller_type"] == "llm"]
    assert random_decisions
    assert llm_decisions
    assert {record["request"]["player_id"] for record in random_decisions} == {"a"}
    assert {record["request"]["player_id"] for record in llm_decisions} == {"b"}
    assert len(llm_calls) == result["llm_calls"]
    assert len(llm_calls) >= len(llm_decisions)
    assert set(conversations) == {"b"}
    verify_run(artifacts.run_directory)


def test_random_requests_do_not_dilute_llm_connection_failure_rate(tmp_path: Path) -> None:
    config = _mixed_config(tmp_path)
    artifacts = RunArtifacts.create(config)

    def disconnected_policy(_request: LLMRequest) -> str:
        raise LLMConnectionError("mock service unavailable")

    controller = DispatchController(
        {
            "a": _random_controller(config.seed, 1, "a"),
            "b": BaselineAgent(
                player_id="b",
                client=RecordingLLMClient(MockLLMClient(disconnected_policy), artifacts),
                profile=config.model_profiles["mock"],
                conversation=AgentConversation(agent_id="b", window_turns=config.window_turns),
            ),
        }
    )

    run_decision_game(GameEngine(config), controller, artifacts)

    result = json.loads((artifacts.run_directory / "result.json").read_text(encoding="utf-8"))
    decisions = _records(artifacts.run_directory / "decisions.jsonl")
    llm_decisions = [record for record in decisions if record["controller_type"] == "llm"]
    random_decisions = [record for record in decisions if record["controller_type"] == "non_llm"]
    assert llm_decisions
    assert random_decisions
    assert result["llm_calls"] > 0
    assert result["llm_fallbacks"] > 0
    assert result["validity_status"] == "invalid"


def test_all_random_baseline_game_is_auditable_without_llm_artifacts(tmp_path: Path) -> None:
    config_path = tmp_path / "random.yaml"
    output_directory = tmp_path / "runs"
    _write_random_config(config_path, output_directory, "random-game")

    run_directory = run_play(config_path)

    decisions = _records(run_directory / "decisions.jsonl")
    result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
    assert decisions
    assert all(record["controller_type"] == "non_llm" for record in decisions)
    assert all(record["validation"]["validation_error"] is None for record in decisions)
    assert result["llm_calls"] == 0
    assert result["reconnect_events"] == 0
    assert result["validity_status"] == "valid"
    assert not (run_directory / "llm_calls.jsonl").exists()
    verify_run(run_directory)


def test_random_baseline_run_is_reproducible_without_perturbing_engine_rng(tmp_path: Path) -> None:
    first_config = tmp_path / "first.yaml"
    second_config = tmp_path / "second.yaml"
    output_directory = tmp_path / "runs"
    _write_random_config(first_config, output_directory, "first")
    _write_random_config(second_config, output_directory, "second")

    first = run_play(first_config)
    second = run_play(second_config)

    assert _commands(first) == _commands(second)
    first_events = _records(first / "events.jsonl")
    second_events = _records(second / "events.jsonl")
    assert [record["event_type"] for record in first_events] == [
        record["event_type"] for record in second_events
    ]
    first_payloads = [record["payload"] for record in first_events]
    second_payloads = [record["payload"] for record in second_events]
    assert first_payloads == second_payloads
