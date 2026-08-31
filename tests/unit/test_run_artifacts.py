import json
from pathlib import Path

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.models import GameEvent
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


def make_config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="game-001",
        experiment_id="experiment-001",
        seed=42,
        players=(
            PlayerConfig(player_id="a", seat=1),
            PlayerConfig(player_id="b", seat=2),
        ),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=output_directory,
    )


def test_run_artifacts_persist_config_events_and_result(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(make_config(tmp_path))
    artifacts.append_event("game_initialized", {"seed": 42})
    artifacts.write_result({"status": "initialized"})

    config = json.loads((artifacts.run_directory / "config.json").read_text(encoding="utf-8"))
    event = json.loads((artifacts.run_directory / "events.jsonl").read_text(encoding="utf-8"))
    result = json.loads((artifacts.run_directory / "result.json").read_text(encoding="utf-8"))

    assert config["config_hash"]
    assert event["event_id"] == 1
    assert event["event_type"] == "game_initialized"
    assert event["occurred_at"].endswith("Z")
    assert result == {"status": "initialized"}


def test_run_artifacts_append_public_game_broadcast(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(make_config(tmp_path))
    artifacts.append_game_broadcast(GameEvent("turn_started", {"player_id": "a"}), 0)
    artifacts.append_game_broadcast(GameEvent("space_landed", {"player_id": "a"}), 0)
    artifacts.append_game_broadcast(GameEvent("turn_ended", {"player_id": "a"}), 1)

    broadcast = (artifacts.run_directory / "game_broadcast.txt").read_text(encoding="utf-8")
    assert broadcast.splitlines() == [
        "[第0轮] 玩家a开始行动回合。",
        "[第1轮] 玩家a结束行动回合。",
    ]
