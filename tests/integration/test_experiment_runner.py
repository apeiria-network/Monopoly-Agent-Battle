"""Integration tests for the manifest-driven pre-experiment batch runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from monopoly_agent_battle.experiments.runner import (
    BatchManifestError,
    read_manifest_paths,
    run_batch,
)

_GAME_TEMPLATE = """\
game_id: {game_id}
experiment_id: batch-test
seed: {seed}
players:
  - {{player_id: p1, seat: 1, controller_type: random_baseline}}
  - {{player_id: p2, seat: 2, controller_type: random_baseline}}
initial_cash: 1500
max_complete_rounds: 5
rules_version: classic-level0-v1
rules_level: 0
board_data_version: classic-us-40-v1
card_data_version: classic-cards-v1
output_directory: runs
"""


def _write_game(directory: Path, name: str, game_id: str, seed: int = 1) -> Path:
    path = directory / name
    path.write_text(_GAME_TEMPLATE.format(game_id=game_id, seed=seed), encoding="utf-8")
    return path


def _write_manifest(directory: Path, entries: list[str]) -> Path:
    lines = "\n".join(f"  - {entry}" for entry in entries)
    path = directory / "batch.yaml"
    path.write_text(f"games:\n{lines}\n", encoding="utf-8")
    return path


def test_manifest_paths_resolve_relative_to_manifest_dir(tmp_path: Path) -> None:
    _write_game(tmp_path, "game_a.yaml", "g-a")
    manifest = _write_manifest(tmp_path, ["game_a.yaml"])
    paths = read_manifest_paths(manifest)
    assert paths == [tmp_path / "game_a.yaml"]


def test_run_batch_runs_each_game_in_order(tmp_path: Path) -> None:
    _write_game(tmp_path, "game_a.yaml", "g-a")
    _write_game(tmp_path, "game_b.yaml", "g-b")
    manifest = _write_manifest(tmp_path, ["game_a.yaml", "game_b.yaml"])
    seen: list[str] = []

    def fake_runner(config_path: Path) -> Path:
        seen.append(config_path.name)
        run_dir = tmp_path / f"run-{config_path.stem}"
        run_dir.mkdir()
        (run_dir / "result.json").write_text(
            json.dumps({"validity_status": "valid"}), encoding="utf-8"
        )
        return run_dir

    tasks = run_batch(manifest, game_runner=fake_runner)
    assert seen == ["game_a.yaml", "game_b.yaml"]
    assert [task.status for task in tasks] == ["completed", "completed"]
    # tasks.jsonl is written next to the manifest.
    lines = (tmp_path / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["game_id"] == "g-a"


def test_run_batch_marks_invalid_from_result(tmp_path: Path) -> None:
    _write_game(tmp_path, "game_a.yaml", "g-a")
    manifest = _write_manifest(tmp_path, ["game_a.yaml"])

    def fake_runner(config_path: Path) -> Path:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "result.json").write_text(
            json.dumps({"validity_status": "invalid"}), encoding="utf-8"
        )
        return run_dir

    tasks = run_batch(manifest, game_runner=fake_runner)
    assert tasks[0].status == "invalid"
    assert tasks[0].reason is not None


def test_single_game_failure_is_isolated(tmp_path: Path) -> None:
    _write_game(tmp_path, "game_a.yaml", "g-a")
    _write_game(tmp_path, "game_b.yaml", "g-b")
    manifest = _write_manifest(tmp_path, ["game_a.yaml", "game_b.yaml"])
    ran: list[str] = []

    def fake_runner(config_path: Path) -> Path:
        ran.append(config_path.name)
        if config_path.name == "game_a.yaml":
            raise RuntimeError("boom")
        run_dir = tmp_path / "run-b"
        run_dir.mkdir()
        (run_dir / "result.json").write_text(
            json.dumps({"validity_status": "valid"}), encoding="utf-8"
        )
        return run_dir

    tasks = run_batch(manifest, game_runner=fake_runner)
    # The failing game does not abort the batch: game_b still runs.
    assert ran == ["game_a.yaml", "game_b.yaml"]
    assert tasks[0].status == "failed"
    assert "boom" in (tasks[0].reason or "")
    assert tasks[1].status == "completed"


def test_precheck_aborts_on_missing_file(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, ["missing.yaml"])
    called = False

    def fake_runner(config_path: Path) -> Path:  # pragma: no cover - must not run
        nonlocal called
        called = True
        return tmp_path

    with pytest.raises(BatchManifestError, match="does not exist"):
        run_batch(manifest, game_runner=fake_runner)
    assert called is False
    assert not (tmp_path / "tasks.jsonl").exists()


def test_precheck_aborts_on_duplicate_game_id(tmp_path: Path) -> None:
    _write_game(tmp_path, "game_a.yaml", "dup")
    _write_game(tmp_path, "game_b.yaml", "dup")
    manifest = _write_manifest(tmp_path, ["game_a.yaml", "game_b.yaml"])

    def fake_runner(config_path: Path) -> Path:  # pragma: no cover - must not run
        return tmp_path

    with pytest.raises(BatchManifestError, match="duplicate game_id"):
        run_batch(manifest, game_runner=fake_runner)


def test_precheck_aborts_on_invalid_config(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("game_id: x\nnot_a_field: 1\n", encoding="utf-8")
    manifest = _write_manifest(tmp_path, ["bad.yaml"])

    def fake_runner(config_path: Path) -> Path:  # pragma: no cover - must not run
        return tmp_path

    with pytest.raises(BatchManifestError, match="failed to load"):
        run_batch(manifest, game_runner=fake_runner)


def test_empty_manifest_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "batch.yaml"
    manifest.write_text("games: []\n", encoding="utf-8")
    with pytest.raises(BatchManifestError, match="non-empty list"):
        read_manifest_paths(manifest)
