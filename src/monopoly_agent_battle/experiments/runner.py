"""Batch runner for a manifest-listed set of pre-experiment games.

The batch is driven by a small manifest file that lists the game YAML files to
run, in order:

    games:
      - game_a.yaml
      - game_b.yaml

Relative entries resolve against the manifest file's own directory. Each game
keeps the ``output_directory`` defined in its own YAML; the batch runner never
overrides it.

Execution has two strict phases:

1. **Pre-check (all-or-nothing).** Every listed configuration is loaded and
   validated and game ids are checked for duplicates. If anything is wrong the
   whole batch is aborted with an error and *no* game is run.
2. **Run.** Only after the pre-check passes are games executed once each, in
   manifest order. A game that raises during execution is isolated: its failure
   is recorded (message plus traceback tail) and the batch continues with the
   next game, so one broken game never aborts a stable pass.

``tasks.jsonl`` is rewritten next to the manifest after every task state
change, so an interrupted batch still leaves the outcomes of finished games
on disk. There is no resume: an interrupted batch is re-run wholesale.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast

import yaml

from monopoly_agent_battle.config.loader import load_game_config
from monopoly_agent_battle.config.models import GameConfig
from monopoly_agent_battle.experiments.state_machine import assert_transition
from monopoly_agent_battle.experiments.tasks import ExperimentTask

GameRunner = Callable[[Path], Path]

_TRACEBACK_TAIL: int = 5


class BatchManifestError(ValueError):
    """Raised when a batch manifest or one of its games fails the pre-check."""


def read_manifest_paths(manifest_path: Path) -> list[Path]:
    """Read the manifest and return the listed game config paths, in order.

    Relative paths resolve against the manifest file's directory. Raises
    ``BatchManifestError`` if the manifest is malformed or lists no games.
    """
    if not manifest_path.is_file():
        raise BatchManifestError(f"batch manifest does not exist: {manifest_path}")
    try:
        document: Any = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise BatchManifestError(f"batch manifest is not valid YAML: {error}") from error
    if not isinstance(document, dict) or "games" not in document:
        raise BatchManifestError("batch manifest must be a mapping with a 'games' list")
    games = cast(dict[str, Any], document).get("games")
    if not isinstance(games, list) or not games:
        raise BatchManifestError("batch manifest 'games' must be a non-empty list")
    base_dir = manifest_path.parent
    paths: list[Path] = []
    for entry in cast(list[object], games):
        if not isinstance(entry, str) or not entry.strip():
            raise BatchManifestError(f"batch manifest game entry must be a string: {entry!r}")
        candidate = Path(entry)
        paths.append(candidate if candidate.is_absolute() else base_dir / candidate)
    return paths


def precheck(config_paths: Iterable[Path]) -> list[tuple[Path, GameConfig]]:
    """Load and validate every listed configuration before any game runs.

    Aborts the whole batch (raising ``BatchManifestError``) on the first missing
    or invalid file, or on any duplicate game id. Returns the loaded
    configurations paired with their source paths on success.
    """
    loaded: list[tuple[Path, GameConfig]] = []
    seen_game_ids: dict[str, Path] = {}
    for path in config_paths:
        if not path.is_file():
            raise BatchManifestError(f"listed game config does not exist: {path}")
        try:
            config = load_game_config(path)
        except Exception as error:  # noqa: BLE001 - surface any load/validation failure
            raise BatchManifestError(f"game config failed to load ({path}): {error}") from error
        if config.game_id in seen_game_ids:
            raise BatchManifestError(
                f"duplicate game_id '{config.game_id}' in {path} "
                f"and {seen_game_ids[config.game_id]}"
            )
        seen_game_ids[config.game_id] = path
        loaded.append((path, config))
    return loaded


def _read_validity_status(run_directory: Path) -> str:
    """Read ``validity_status`` from a run's result.json; default to valid."""
    result_path = run_directory / "result.json"
    if not result_path.exists():
        return "valid"
    try:
        document = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "valid"
    value = document.get("validity_status")
    return value if isinstance(value, str) else "valid"


def _run_one(
    task: ExperimentTask,
    game_runner: GameRunner,
    *,
    persist: Callable[[list[ExperimentTask]], None],
    tasks: list[ExperimentTask],
) -> None:
    """Execute one task once, isolating any failure to this task."""
    assert_transition(task.status, "running")
    task.status = "running"
    persist(tasks)
    try:
        run_directory = game_runner(Path(task.config_path))
    except Exception as error:  # noqa: BLE001 - isolate a game failure from the batch
        assert_transition(task.status, "failed")
        task.status = "failed"
        task.reason = _failure_reason(error)
        return
    task.run_directory = str(run_directory)
    if _read_validity_status(run_directory) == "invalid":
        assert_transition(task.status, "invalid")
        task.status = "invalid"
        task.reason = "run produced validity_status=invalid"
    else:
        assert_transition(task.status, "completed")
        task.status = "completed"


def _failure_reason(error: Exception) -> str:
    """Render one failure line plus the traceback tail, newline-free for JSONL."""
    lines = [f"run failed: {error}"]
    for frame in traceback.extract_tb(error.__traceback__)[-_TRACEBACK_TAIL:]:
        lines.append(f"{frame.filename}:{frame.lineno} in {frame.name}")
    return " | ".join(lines)


def run_batch(manifest_path: Path, *, game_runner: GameRunner) -> list[ExperimentTask]:
    """Pre-check every listed game, then run each once, in manifest order.

    ``tasks.jsonl`` is persisted after every state change, so an interrupted
    batch leaves the outcomes of finished games on disk. Raises
    ``BatchManifestError`` (running nothing) if the pre-check fails.
    """
    manifest_path = Path(manifest_path)
    loaded = precheck(read_manifest_paths(manifest_path))
    tasks = [
        ExperimentTask(
            game_id=config.game_id,
            experiment_id=config.experiment_id,
            config_path=str(path),
        )
        for path, config in loaded
    ]
    tasks_path = manifest_path.parent / "tasks.jsonl"

    def persist(task_listing: list[ExperimentTask]) -> None:
        write_tasks(task_listing, tasks_path)

    for task in tasks:
        _run_one(task, game_runner, persist=persist, tasks=tasks)
        persist(tasks)
    return tasks


def write_tasks(tasks: Iterable[ExperimentTask], path: Path) -> None:
    """Persist the task listing as one JSON object per line."""
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for task in tasks:
            output.write(task.model_dump_json() + "\n")


def render_batch_summary(tasks: Iterable[ExperimentTask]) -> str:
    """Render a short human-readable batch outcome overview for the terminal."""
    task_list = list(tasks)
    counts: dict[str, int] = {}
    for task in task_list:
        counts[task.status] = counts.get(task.status, 0) + 1
    summary = " · ".join(f"{status}: {count}" for status, count in sorted(counts.items()))
    lines = [f"批次完成：共 {len(task_list)} 局 — {summary}"]
    for task in task_list:
        detail = f"  [{task.status}] {task.game_id}"
        if task.reason:
            detail += f" — {task.reason}"
        lines.append(detail)
    return "\n".join(lines)
