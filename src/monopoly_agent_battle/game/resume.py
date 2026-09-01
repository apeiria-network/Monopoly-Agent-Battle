"""Resume all-random games from the last complete audited command boundary."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, cast

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
    try:
        config = GameConfig.model_validate(config_doc.get("config"))
    except (TypeError, ValueError) as error:
        raise ResumeError("config.json contains an invalid frozen configuration") from error
    if config_hash(config) != config_doc.get("config_hash"):
        raise ResumeError("frozen configuration hash mismatch")
    if any(player.controller_type != "random_baseline" for player in config.players):
        raise ResumeError("resume currently supports all-random baseline games only")
    result_path = run_directory / "result.json"
    result = _json(result_path) if result_path.exists() else {}
    if result.get("status") == "completed":
        raise ResumeError("completed run cannot be resumed")
    checkpoint = _json(run_directory / "checkpoint.json")
    try:
        artifacts = RunArtifacts.open_existing(run_directory)
    except (OSError, ValueError) as error:
        raise ResumeError(f"invalid append-only artifact log: {error}") from error
    last_event_id = checkpoint.get("last_event_id")
    if not isinstance(last_event_id, int) or last_event_id != artifacts.next_event_id - 1:
        raise ResumeError("checkpoint boundary does not match contiguous event IDs")
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
    for line_number, line in enumerate(decisions_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ResumeError(f"invalid JSON in decisions.jsonl:{line_number}") from error
        if not isinstance(record, dict):
            raise ResumeError(f"decision record {line_number} must be an object")
        record = cast(dict[str, Any], record)
        request_value = record.get("request")
        if not isinstance(request_value, dict):
            raise ResumeError(f"decision record {line_number} has no request object")
        request = cast(dict[str, Any], request_value)
        if request.get("player_id") != player_id:
            continue
        options_value = request.get("options")
        if not isinstance(options_value, list) or not options_value:
            raise ResumeError(f"decision record {line_number} has no legal options")
        raw_options = cast(list[Any], options_value)
        if not all(isinstance(option, dict) for option in raw_options):
            raise ResumeError(f"decision record {line_number} has invalid legal options")
        options = cast(list[dict[str, Any]], raw_options)
        option_index = rng.randrange(len(options))
        executed_value = record.get("executed_command")
        if not isinstance(executed_value, dict):
            raise ResumeError(f"decision record {line_number} has no executed command")
        executed = cast(dict[str, Any], executed_value)
        command_value = executed.get("command")
        if not isinstance(command_value, dict):
            raise ResumeError(f"decision record {line_number} has no command payload")
        command = cast(dict[str, Any], command_value)
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
        if selected is None:
            raise ResumeError(f"decision record {line_number} command is absent from legal options")
        selected_index = next(index for index, option in enumerate(options) if option is selected)
        if selected_index != option_index:
            raise ResumeError(
                f"decision record {line_number} does not match random baseline sequence"
            )
        target_spec_value = selected.get("target")
        if target_spec_value is not None:
            if not isinstance(target_spec_value, dict):
                raise ResumeError(f"decision record {line_number} has invalid target schema")
            target_spec = cast(dict[str, Any], target_spec_value)
            target_values_value = target_spec.get("legal_values")
            if not isinstance(target_values_value, list) or not target_values_value:
                raise ResumeError(f"decision record {line_number} has no legal targets")
            target_values = cast(list[Any], target_values_value)
            target = command.get("position")
            if target is None:
                target = command.get("card_id")
            if target is None:
                target = command.get("target_player_id")
            try:
                target_index = next(
                    index
                    for index, value in enumerate(target_values)
                    if _normalized_target_value(value) == target
                )
            except StopIteration as error:
                raise ResumeError(
                    "decision target is not present in its audited legal values"
                ) from error
            chosen_target_index = rng.randrange(len(target_values))
            if chosen_target_index != target_index:
                raise ResumeError(
                    f"decision record {line_number} target does not match random sequence"
                )
    return rng


def _normalized_target_value(value: Any) -> Any:
    if isinstance(value, list):
        values = cast(list[Any], value)
        if len(values) == 1:
            return values[0]
        return values
    return value


def _json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ResumeError(f"missing required artifact: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ResumeError(f"cannot read {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise ResumeError(f"{path.name} must contain a JSON object")
    return cast(dict[str, Any], value)
