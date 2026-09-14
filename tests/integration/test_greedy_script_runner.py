"""Integration tests for greedy scripted game runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from monopoly_agent_battle.cli.main import run_play
from monopoly_agent_battle.game.replay import verify_run

_DISPOSAL_COMMAND_TYPES = {"Mortgage", "SellBuilding"}


def _write_greedy_config(
    path: Path,
    output_directory: Path,
    game_id: str,
    *,
    seed: int = 17,
    max_rounds: int = 3,
) -> None:
    path.write_text(
        f"""game_id: {game_id}
experiment_id: greedy-script-integration
seed: {seed}
players:
  - player_id: a
    seat: 1
    controller_type: greedy_script
  - player_id: b
    seat: 2
    controller_type: greedy_script
  - player_id: c
    seat: 3
    controller_type: greedy_script
  - player_id: d
    seat: 4
    controller_type: greedy_script
rules_version: classic-level0-v1
rules_level: 0
board_data_version: classic-us-40-v1
card_data_version: classic-cards-v1
initial_cash: 1500
initial_chance_cards: 2
max_complete_rounds: {max_rounds}
output_directory: {output_directory.as_posix()}
""",
        encoding="utf-8",
    )


def _records(path: Path) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_all_greedy_script_game_is_auditable_and_never_voluntarily_disposes(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "greedy.yaml"
    output_directory = tmp_path / "runs"
    _write_greedy_config(config_path, output_directory, "greedy-game")

    run_directory = run_play(config_path)

    decisions = _records(run_directory / "decisions.jsonl")
    result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
    assert decisions
    assert all(record["controller_type"] == "non_llm" for record in decisions)
    assert all(record["validation"]["validation_error"] is None for record in decisions)
    assert result["llm_calls"] == 0
    assert result["validity_status"] == "valid"
    assert not (run_directory / "llm_calls.jsonl").exists()
    for record in decisions:
        if record["executed_command"]["command_type"] in _DISPOSAL_COMMAND_TYPES:
            assert record["request"]["kind"] == "payment_resolution"
    verify_run(run_directory)


def test_greedy_script_run_is_reproducible(tmp_path: Path) -> None:
    first_config = tmp_path / "first.yaml"
    second_config = tmp_path / "second.yaml"
    output_directory = tmp_path / "runs"
    _write_greedy_config(first_config, output_directory, "first")
    _write_greedy_config(second_config, output_directory, "second")

    first = run_play(first_config)
    second = run_play(second_config)

    first_commands = [
        cast(dict[str, object], record["executed_command"])
        for record in _records(first / "decisions.jsonl")
    ]
    second_commands = [
        cast(dict[str, object], record["executed_command"])
        for record in _records(second / "decisions.jsonl")
    ]
    assert first_commands == second_commands


def test_greedy_script_handles_targeted_decisions_end_to_end(tmp_path: Path) -> None:
    """A full 50-round game on seed 2201 exercises every previously-crashing path.

    Seed 2201 deterministically triggers (in production option structure):
      * theft card selection at decision ~10 (the original crash),
      * redeem-mortgage decisions around round 45,
      * forced mortgage/sell disposal in payment resolution around round 47.
    The engine folds each command's legal targets into a single option whose
    ``option.target.legal_values`` lists the candidate tuples; this asserts the
    controller supplies a valid target for each instead of raising.
    """
    config_path = tmp_path / "greedy_targets.yaml"
    output_directory = tmp_path / "runs"
    _write_greedy_config(config_path, output_directory, "greedy-targets", seed=2201, max_rounds=50)

    run_directory = run_play(config_path)

    decisions = _records(run_directory / "decisions.jsonl")
    result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
    assert result["validity_status"] == "valid"
    assert result["llm_calls"] == 0

    command_types = {record["executed_command"]["command_type"] for record in decisions}
    assert "SelectStolenChanceCard" in command_types
    assert "RedeemMortgage" in command_types
    assert command_types & _DISPOSAL_COMMAND_TYPES

    for record in decisions:
        if record["executed_command"]["command_type"] in _DISPOSAL_COMMAND_TYPES:
            assert record["request"]["kind"] == "payment_resolution"
    verify_run(run_directory)
