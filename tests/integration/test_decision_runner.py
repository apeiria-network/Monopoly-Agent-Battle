import json
from pathlib import Path

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.runner import DeterministicPolicyController, run_decision_game
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.replay import verify_run
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


def make_config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="protocol-game",
        experiment_id="protocol-experiment",
        seed=3,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        max_complete_rounds=1,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=output_directory,
    )


def test_deterministic_decision_runner_writes_auditable_commands_and_replays(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)

    result = run_decision_game(GameEngine(config), DeterministicPolicyController(), artifacts)

    decisions = [
        json.loads(line)
        for line in (artifacts.run_directory / "decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert result.status == "completed"
    assert decisions
    assert all(record["validation"]["validation_error"] is None for record in decisions)
    assert all(record["executed_command"]["command_type"] for record in decisions)
    verify_run(artifacts.run_directory)


def test_connection_failures_are_retried_then_recorded_as_fallback(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    attempts = 0

    def disconnected_controller(_request: str) -> str:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("service unavailable")

    run_decision_game(GameEngine(config), disconnected_controller, artifacts)

    runtime = [
        json.loads(line)
        for line in (artifacts.run_directory / "runtime.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert attempts >= 3
    assert runtime[0]["event_type"] == "controller_connection_error"
    assert runtime[2]["event_type"] == "controller_connection_error"
    assert runtime[3]["event_type"] == "decision_fallback"
    decisions = [
        json.loads(line)
        for line in (artifacts.run_directory / "decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert decisions[0]["fallback"] is True
    assert decisions[0]["attempted_validation"]["validation_error"] == "response is not valid JSON"
