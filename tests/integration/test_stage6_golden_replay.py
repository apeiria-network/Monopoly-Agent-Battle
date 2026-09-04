"""Stage 6 golden-style full-game and replay regression tests."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

import yaml

from monopoly_agent_battle.cli.main import run_play
from monopoly_agent_battle.config.loader import load_game_config
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
        max_complete_rounds=30,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=output_directory,
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def canonical_jsonl(
    path: Path,
    *,
    volatile_fields: frozenset[str] = frozenset(),
    unordered_records: bool = False,
) -> list[dict[str, Any]]:
    """Remove declared wall-clock fields and optionally normalize record order."""
    ignored = {"occurred_at", *volatile_fields}
    records = [
        {key: value for key, value in record.items() if key not in ignored}
        for record in read_jsonl(path)
    ]
    if unordered_records:
        records.sort(key=lambda record: json.dumps(record, ensure_ascii=False, sort_keys=True))
    return records


def _canonical_decision(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize only unordered parallel calls inside one court trace."""
    normalized = dict(record)
    trace = normalized.get("court_trace")
    if isinstance(trace, dict):
        typed_trace = cast(dict[str, Any], trace)
        raw_calls = typed_trace.get("calls")
        if isinstance(raw_calls, list):
            calls = cast(list[dict[str, Any]], raw_calls)
            normalized["court_trace"] = dict(typed_trace)
            normalized["court_trace"]["calls"] = sorted(
                calls,
                key=lambda call: json.dumps(call, ensure_ascii=False, sort_keys=True),
            )
    return normalized


def write_four_courts_config(path: Path, output_directory: Path, game_id: str) -> None:
    """Write a short fixed four-court Fake-LLM configuration for golden runs."""
    source = Path("configs/games/four_courts_fake_demo.yaml")
    raw_config = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw_config, dict):
        raise AssertionError("four-court golden configuration must be a mapping")
    raw_config = cast(dict[str, Any], raw_config)
    raw_config.update(
        {
            "game_id": game_id,
            "experiment_id": "four-courts-fake-golden",
            "max_complete_rounds": 30,
            "output_directory": str(output_directory),
        }
    )
    path.write_text(
        yaml.safe_dump(raw_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def run_four_courts_fake_game(base_directory: Path, game_id: str) -> Path:
    base_directory.mkdir(parents=True, exist_ok=True)
    config_path = base_directory / f"{game_id}.yaml"
    output_directory = base_directory / "runs"
    write_four_courts_config(config_path, output_directory, game_id)
    load_game_config(config_path)
    return run_play(config_path)


def run_full_four_player_game(output_directory: Path) -> Path:
    config = make_config(output_directory)
    artifacts = RunArtifacts.create(config)
    result = run_decision_game(GameEngine(config), DeterministicPolicyController(), artifacts)
    assert result.status == "completed"
    return artifacts.run_directory


def _canonical_artifact(run_directory: Path, filename: str) -> object:
    """Normalize known wall-clock fields and unordered parallel records."""
    if filename.endswith(".jsonl"):
        volatile: frozenset[str] = (
            frozenset({"duration_ms", "call_id"}) if filename == "llm_calls.jsonl" else frozenset()
        )
        unordered = filename == "llm_calls.jsonl"
        records = canonical_jsonl(
            run_directory / filename,
            volatile_fields=volatile,
            unordered_records=unordered,
        )
        if filename == "decisions.jsonl":
            return [_canonical_decision(record) for record in records]
        return records
    if filename == "game_broadcast.txt":
        return (run_directory / filename).read_text(encoding="utf-8")
    return read_json(run_directory / filename)


def test_four_courts_fake_game_is_auditable_and_deterministic(tmp_path: Path) -> None:
    first = run_four_courts_fake_game(tmp_path / "first", "four-courts-golden")
    second = run_four_courts_fake_game(tmp_path / "second", "four-courts-golden")

    for filename in (
        "events.jsonl",
        "decisions.jsonl",
        "llm_calls.jsonl",
        "performance.jsonl",
        "game_broadcast.txt",
        "result.json",
    ):
        assert _canonical_artifact(first, filename) == _canonical_artifact(second, filename)

    decisions = canonical_jsonl(first / "decisions.jsonl")
    calls = canonical_jsonl(first / "llm_calls.jsonl", volatile_fields=frozenset({"duration_ms"}))
    result = read_json(first / "result.json")
    court_players = {"shang-court", "qin-court", "tang-court", "ming-court"}
    assert {record["request"]["player_id"] for record in decisions} == court_players
    assert calls
    assert [record["call_id"] for record in calls] == list(range(1, len(calls) + 1))
    assert {record["caller_role"].split(".", 1)[0] for record in calls} == court_players
    assert result["llm_calls"] == len(calls)
    assert result["llm_fallbacks"] == 0
    assert result["validity_status"] == "valid"
    verify_run(first)
    verify_run(second)


def test_four_player_complete_game_has_deterministic_golden_artifacts(tmp_path: Path) -> None:
    first = run_full_four_player_game(tmp_path / "first")
    second = run_full_four_player_game(tmp_path / "second")

    assert canonical_jsonl(first / "events.jsonl") == canonical_jsonl(second / "events.jsonl")
    assert canonical_jsonl(first / "decisions.jsonl") == canonical_jsonl(second / "decisions.jsonl")
    assert read_json(first / "result.json") == read_json(second / "result.json")

    result = read_json(first / "result.json")
    assert result["status"] == "completed"
    assert result["complete_rounds"] == 30
    assert set(result["players"]) == {"a", "b", "c", "d"}
    assert result["rankings"] == ["a", "d", "b", "c"]
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


def export_golden_runs(export_directory: Path) -> None:
    """Export both golden runs to a directory reserved for this script."""
    with TemporaryDirectory() as temporary_directory:
        temporary = Path(temporary_directory)
        random_run = run_full_four_player_game(temporary / "random").resolve()
        fake_run = run_four_courts_fake_game(
            temporary / "four-courts-fake", "four-courts-golden"
        ).resolve()
        for name, source in (
            ("random-baseline", random_run),
            ("four-courts-fake", fake_run),
        ):
            shutil.copytree(source, export_directory / name, dirs_exist_ok=True)


def main() -> None:
    """Run the golden checks and export their complete artifact directories."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=Path("artifacts/stage6-golden"),
        help="directory reserved for exported Stage 6 golden runs",
    )
    args = parser.parse_args()
    export_golden_runs(args.export_dir)
    print(f"Exported golden runs to {args.export_dir}")


if __name__ == "__main__":
    main()
