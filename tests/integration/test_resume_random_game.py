from __future__ import annotations

import json
from pathlib import Path

import pytest

from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.cli.main import _random_baseline_rng
from monopoly_agent_battle.config.loader import config_hash
from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.runner import DispatchController, run_decision_game
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.resume import ResumeError, resume_random_game
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


class InterruptAfter:
    uses_llm = False

    def __init__(self, controller: RandomBaselineController, remaining: int) -> None:
        self.controller = controller
        self.remaining = remaining

    def __call__(self, request, feedback=None):
        if self.remaining == 0:
            raise RuntimeError("simulated process interruption")
        self.remaining -= 1
        return self.controller(request, feedback)


def config(output: Path, game_id: str) -> GameConfig:
    return GameConfig(
        game_id=game_id,
        experiment_id="resume-test",
        seed=17,
        players=(
            PlayerConfig(player_id="a", seat=1, controller_type="random_baseline"),
            PlayerConfig(player_id="b", seat=2, controller_type="random_baseline"),
        ),
        max_complete_rounds=3,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=output,
    )


def controllers(value: GameConfig, interrupt: int | None = None):
    result = {}
    for player in value.players:
        controller = RandomBaselineController(
            _random_baseline_rng(value.seed, player.seat, player.player_id)
        )
        result[player.player_id] = (
            InterruptAfter(controller, interrupt) if interrupt is not None else controller
        )
    return DispatchController(result)


def normalized_events(path: Path):
    return [
        (record["event_type"], record["payload"])
        for record in map(
            json.loads, (path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        )
    ]


def test_resume_random_run_matches_uninterrupted_execution(tmp_path: Path) -> None:
    expected_config = config(tmp_path, "expected")
    expected_artifacts = RunArtifacts.create(expected_config)
    run_decision_game(GameEngine(expected_config), controllers(expected_config), expected_artifacts)

    interrupted_config = config(tmp_path, "interrupted")
    interrupted_artifacts = RunArtifacts.create(interrupted_config)
    with pytest.raises(RuntimeError, match="simulated"):
        run_decision_game(
            GameEngine(interrupted_config),
            controllers(interrupted_config, interrupt=2),
            interrupted_artifacts,
        )
    assert (interrupted_artifacts.run_directory / "checkpoint.json").exists()
    assert not (interrupted_artifacts.run_directory / "result.json").exists()

    resume_random_game(interrupted_artifacts.run_directory)

    expected_result = json.loads(
        (expected_artifacts.run_directory / "result.json").read_text(encoding="utf-8")
    )
    resumed_result = json.loads(
        (interrupted_artifacts.run_directory / "result.json").read_text(encoding="utf-8")
    )
    assert resumed_result == expected_result
    assert normalized_events(interrupted_artifacts.run_directory) == normalized_events(
        expected_artifacts.run_directory
    )
    event_ids = [
        json.loads(line)["event_id"]
        for line in (interrupted_artifacts.run_directory / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert event_ids == list(range(1, len(event_ids) + 1))


def interrupt_run(tmp_path: Path, game_id: str = "interrupted") -> RunArtifacts:
    value = config(tmp_path, game_id)
    artifacts = RunArtifacts.create(value)
    with pytest.raises(RuntimeError, match="simulated"):
        run_decision_game(GameEngine(value), controllers(value, interrupt=2), artifacts)
    return artifacts


def test_resume_rejects_completed_run(tmp_path: Path) -> None:
    value = config(tmp_path, "completed")
    artifacts = RunArtifacts.create(value)
    run_decision_game(GameEngine(value), controllers(value), artifacts)

    with pytest.raises(ResumeError, match="completed run"):
        resume_random_game(artifacts.run_directory)


@pytest.mark.parametrize(
    "boundary", [None, 0, 10_000, "1"], ids=["missing", "stale", "future", "wrong-type"]
)
def test_resume_rejects_checkpoint_boundary_mismatch(tmp_path: Path, boundary: object) -> None:
    artifacts = interrupt_run(tmp_path, f"boundary-{str(boundary)}")
    path = artifacts.run_directory / "checkpoint.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    if boundary is None:
        document.pop("last_event_id")
    else:
        document["last_event_id"] = boundary
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ResumeError, match="checkpoint boundary"):
        resume_random_game(artifacts.run_directory)


def test_resume_rejects_modified_config_hash(tmp_path: Path) -> None:
    artifacts = interrupt_run(tmp_path, "modified-config")
    path = artifacts.run_directory / "config.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["config"]["seed"] = 18
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ResumeError, match="hash mismatch"):
        resume_random_game(artifacts.run_directory)


def test_resume_rejects_non_random_players(tmp_path: Path) -> None:
    artifacts = interrupt_run(tmp_path, "non-random")
    path = artifacts.run_directory / "config.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["config"]["players"][0]["controller_type"] = "llm_baseline"
    document["config"]["players"][0]["model_profile"] = "mock-profile"
    document["config"]["model_profiles"] = {
        "mock-profile": {"provider": "mock", "model": "test-model", "seed": 1}
    }

    modified = GameConfig.model_validate(document["config"])
    document["config_hash"] = config_hash(modified)
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ResumeError, match="all-random"):
        resume_random_game(artifacts.run_directory)


def test_resume_rejects_damaged_event_log(tmp_path: Path) -> None:
    artifacts = interrupt_run(tmp_path, "damaged-log")
    path = artifacts.run_directory / "events.jsonl"
    records = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(records[-1])
    record["event_id"] += 2
    records[-1] = json.dumps(record)
    path.write_text("\n".join(records) + "\n", encoding="utf-8")

    with pytest.raises(ResumeError, match="non-contiguous event_id"):
        resume_random_game(artifacts.run_directory)


def test_resume_rejects_tampered_random_decision(tmp_path: Path) -> None:
    artifacts = interrupt_run(tmp_path, "tampered-decision")
    path = artifacts.run_directory / "decisions.jsonl"
    records = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(records[0])
    options = record["request"]["options"]
    replacement = next(
        option
        for option in options
        if option["command_type"].replace("_", "").lower()
        != record["executed_command"]["command_type"].replace("_", "").lower()
    )
    record["executed_command"]["command_type"] = replacement["command_type"]
    record["executed_command"]["command"] = {"player_id": record["request"]["player_id"]}
    records[0] = json.dumps(record)
    path.write_text("\n".join(records) + "\n", encoding="utf-8")

    with pytest.raises(ResumeError, match="random baseline sequence"):
        resume_random_game(artifacts.run_directory)
