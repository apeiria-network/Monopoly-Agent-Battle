import json
from pathlib import Path

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.commands import EndTurn, RollDice, UseChanceCard
from monopoly_agent_battle.game.controllers import ScriptedController
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.replay import ReplayVerificationError, verify_run
from monopoly_agent_battle.game.runner import run_scripted_game
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


def make_config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="runner-game",
        experiment_id="runner-experiment",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        max_complete_rounds=1,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=output_directory,
    )


def make_engine(config: GameConfig, dice_values: list[int]) -> GameEngine:
    engine = GameEngine(config)
    iterator = iter(dice_values)
    engine.random.randint = lambda _low, _high: next(iterator)  # type: ignore[method-assign]
    return engine


def test_runner_persists_reproducible_completed_game(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    commands = [RollDice("a"), EndTurn("a"), RollDice("b"), EndTurn("b")]
    first_artifacts = RunArtifacts.create(config)
    first = run_scripted_game(
        config,
        ScriptedController(commands),
        first_artifacts,
        make_engine(config, [1, 2, 2, 3]),
    )

    duplicate_config = config.model_copy(update={"game_id": "runner-game-copy"})
    second = run_scripted_game(
        duplicate_config,
        ScriptedController(commands),
        engine=make_engine(duplicate_config, [1, 2, 2, 3]),
    )
    result = json.loads((first_artifacts.run_directory / "result.json").read_text(encoding="utf-8"))
    event_lines = (
        (first_artifacts.run_directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
    )
    broadcast_lines = (
        (first_artifacts.run_directory / "game_broadcast.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert broadcast_lines
    assert all(line.startswith("[第") for line in broadcast_lines)
    assert all("command_executed" not in line for line in broadcast_lines)
    assert any("游戏结束" in line for line in broadcast_lines)

    assert first.status == "completed"
    assert first.events == second.events
    assert result["status"] == "completed"
    assert result["rankings"] == ["a", "b"]
    assert len(event_lines) == len(first.events) + len(commands)
    verify_run(first_artifacts.run_directory)


def test_runner_resolves_card_draw_without_false_success(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    engine = make_engine(config, [1, 1])

    result = run_scripted_game(config, ScriptedController([RollDice("a")]), engine=engine)

    assert result.status == "script_exhausted"


def test_runner_replays_chance_card_with_secondary_property_target(tmp_path: Path) -> None:
    config = make_config(tmp_path).model_copy(update={"max_complete_rounds": 2})
    engine = GameEngine(config)
    dice = iter((1, 2, 3, 3, 1, 2, 2, 2, 1, 1, 1, 2))
    engine.random.randint = lambda _low, _high: next(dice)  # type: ignore[method-assign]
    artifacts = RunArtifacts.create(config)

    result = run_scripted_game(
        config,
        ScriptedController(
            [
                RollDice("a"),
                EndTurn("a"),
                RollDice("b"),
                RollDice("b"),
                EndTurn("b"),
                RollDice("a"),
                RollDice("a"),
                RollDice("a"),
                UseChanceCard(
                    "a",
                    "chance-swap-property",
                    target_position=9,
                    secondary_target_position=3,
                ),
            ]
        ),
        artifacts,
        engine,
    )

    assert result.status == "script_exhausted"
    assert engine.state.properties[3].owner_id == "b"
    assert engine.state.properties[9].owner_id == "a"
    verify_run(artifacts.run_directory)


def test_runner_replays_drawn_chance_card_use(tmp_path: Path) -> None:
    config = make_config(tmp_path).model_copy(update={"seed": 7, "max_complete_rounds": 2})
    artifacts = RunArtifacts.create(config)
    engine = make_engine(config, [1, 6])

    result = run_scripted_game(
        config,
        ScriptedController(
            [
                RollDice("a"),
                UseChanceCard("a", "chance-waiver"),
            ]
        ),
        artifacts,
        engine,
    )

    assert result.status == "script_exhausted"
    assert engine.state.players["a"].position == 7
    assert engine.state.players["a"].rent_waivers == 2
    assert engine.state.players["a"].chance_cards == []
    assert engine.state.chance_discard_pile == ["chance-waiver"]
    verify_run(artifacts.run_directory)


def test_replay_rejects_nonsequential_event_ids(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    run_scripted_game(
        config,
        ScriptedController([RollDice("a")]),
        artifacts,
        make_engine(config, [1, 2]),
    )
    events_path = artifacts.run_directory / "events.jsonl"
    records = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    records[1]["event_id"] = 99
    events_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    with pytest.raises(ReplayVerificationError, match="expected event_id 2"):
        verify_run(artifacts.run_directory)


def test_replay_tolerates_card_discarded_without_reason_field(tmp_path: Path) -> None:
    config = make_config(tmp_path).model_copy(update={"seed": 7, "max_complete_rounds": 2})
    artifacts = RunArtifacts.create(config)
    engine = make_engine(config, [1, 6])

    run_scripted_game(
        config,
        ScriptedController([RollDice("a"), UseChanceCard("a", "chance-waiver")]),
        artifacts,
        engine,
    )
    events_path = artifacts.run_directory / "events.jsonl"
    records = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    stripped = False
    for record in records:
        if record["event_type"] == "card_discarded":
            record["payload"].pop("reason", None)
            stripped = True
    assert stripped, "scenario must produce at least one card_discarded event"
    events_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    verify_run(artifacts.run_directory)
