"""Stage 6 golden-style full-game and replay regression tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.runner import DeterministicPolicyController, run_decision_game
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.replay import verify_run
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


def make_config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="stage6-golden-game",
        experiment_id="stage6-golden-experiment",
        seed=2026,
        players=tuple(
            PlayerConfig(player_id=player_id, seat=seat)
            for seat, player_id in enumerate(("a", "b", "c", "d"), start=1)
        ),
        max_complete_rounds=2,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=output_directory,
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def canonical_jsonl(path: Path) -> list[dict[str, Any]]:
    """Remove wall-clock fields while retaining the complete audit payload."""
    return [
        {key: value for key, value in record.items() if key != "occurred_at"}
        for record in read_jsonl(path)
    ]


def run_full_four_player_game(output_directory: Path) -> Path:
    config = make_config(output_directory)
    artifacts = RunArtifacts.create(config)
    result = run_decision_game(GameEngine(config), DeterministicPolicyController(), artifacts)
    assert result.status == "completed"
    return artifacts.run_directory


def test_four_player_complete_game_has_deterministic_golden_artifacts(tmp_path: Path) -> None:
    first = run_full_four_player_game(tmp_path / "first")
    second = run_full_four_player_game(tmp_path / "second")

    assert canonical_jsonl(first / "events.jsonl") == canonical_jsonl(second / "events.jsonl")
    assert canonical_jsonl(first / "decisions.jsonl") == canonical_jsonl(second / "decisions.jsonl")
    assert read_json(first / "result.json") == read_json(second / "result.json")

    result = read_json(first / "result.json")
    assert result["status"] == "completed"
    assert result["complete_rounds"] == 2
    assert set(result["players"]) == {"a", "b", "c", "d"}
    assert result["rankings"] == ["d", "a", "c", "b"]
    verify_run(first)
    verify_run(second)


def test_golden_artifacts_keep_sequencing_and_decision_coverage(tmp_path: Path) -> None:
    run_directory = run_full_four_player_game(tmp_path)
    events = canonical_jsonl(run_directory / "events.jsonl")
    decisions = canonical_jsonl(run_directory / "decisions.jsonl")

    assert [record["event_id"] for record in events] == list(range(1, len(events) + 1))
    assert decisions
    assert {record["request"]["player_id"] for record in decisions} == {"a", "b", "c", "d"}
    assert all(record["validation"]["validation_error"] is None for record in decisions)
    assert all(record["executed_command"]["command_type"] for record in decisions)
