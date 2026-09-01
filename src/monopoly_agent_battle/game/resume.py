"""Resume all-random games from the last complete audited command boundary."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.config.loader import config_hash
from monopoly_agent_battle.config.models import GameConfig
from monopoly_agent_battle.decision.runner import DispatchController, run_decision_game
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.replay import verify_run
from monopoly_agent_battle.game.state_codec import restore_checkpoint
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


class ResumeError(ValueError):
    """Raised when an existing run cannot be safely resumed."""


def resume_random_game(run_directory: Path) -> Path:
    """Resume an unfinished all-random run in its original directory."""
    run_directory = Path(run_directory)
    config_doc = _json(run_directory / "config.json")
    config = GameConfig.model_validate(config_doc.get("config"))
    if config_hash(config) != config_doc.get("config_hash"):
        raise ResumeError("frozen configuration hash mismatch")
    if any(player.controller_type != "random_baseline" for player in config.players):
        raise ResumeError("resume currently supports all-random baseline games only")
    result_path = run_directory / "result.json"
    result = _json(result_path) if result_path.exists() else {}
    if result.get("status") == "completed":
        raise ResumeError("completed run cannot be resumed")
    checkpoint = _json(run_directory / "checkpoint.json")
    artifacts = RunArtifacts.open_existing(run_directory)
    engine = GameEngine(config)
    restore_checkpoint(checkpoint, engine.state, engine.random)
    if engine.state.finished:
        raise ResumeError("checkpoint already contains a finished game")
    controllers = {
        player.player_id: RandomBaselineController(
            _advanced_controller_rng(config, player.player_id, player.seat, run_directory)
        )
        for player in config.players
    }
    artifacts.append_runtime(
        "run_resumed",
        {"event_id_start": artifacts.next_event_id, "boundary": "complete_command"},
    )
    run_decision_game(engine, DispatchController(controllers), artifacts)
    verify_run(run_directory)
    return run_directory


def _advanced_controller_rng(
    config: GameConfig, player_id: str, seat: int, run_directory: Path
) -> random.Random:
    material = f"random-baseline-v1:{config.seed}:{seat}:{player_id}".encode()
    rng = random.Random(int.from_bytes(hashlib.sha256(material).digest(), "big"))
    decisions_path = run_directory / "decisions.jsonl"
    if not decisions_path.exists():
        return rng
    for line in decisions_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        request = record.get("request", {})
        if request.get("player_id") != player_id:
            continue
        options = request.get("options", [])
        rng.randrange(len(options))
        executed = record.get("executed_command", {})
        command = executed.get("command", {})
        command_type = str(executed.get("command_type", ""))
        selected = next(
            (
                option
                for option in options
                if str(option.get("command_type", "")).replace("_", "").lower()
                == command_type.replace("_", "").lower()
            ),
            None,
        )
        if selected and selected.get("target") is not None:
            target_values = selected["target"]["legal_values"]
            target = command.get("position")
            if target is None:
                target = command.get("card_id")
            if target is None:
                target = command.get("target_player_id")
            try:
                target_index = next(
                    index
                    for index, value in enumerate(target_values)
                    if (value[0] if isinstance(value, list) and len(value) == 1 else value)
                    == target
                )
            except StopIteration as error:
                raise ResumeError(
                    "decision target is not present in its audited legal values"
                ) from error
            rng.randrange(len(target_values))
            # The RNG draw above is intentionally consumed; the audit validates its selected index.
            del target_index
    return rng


def _json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ResumeError(f"missing required artifact: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ResumeError(f"cannot read {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise ResumeError(f"{path.name} must contain a JSON object")
    return value
