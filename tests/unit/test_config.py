from pathlib import Path

import pytest
from pydantic import ValidationError

from monopoly_agent_battle.config.loader import canonical_config_json, config_hash, load_game_config
from monopoly_agent_battle.config.models import GameConfig


def config_data() -> dict[str, object]:
    return {
        "game_id": "game-001",
        "experiment_id": "experiment-001",
        "seed": 42,
        "players": [
            {"player_id": "a", "seat": 1},
            {"player_id": "b", "seat": 2},
        ],
        "rules_version": "classic-level0-v1",
        "rules_level": 0,
        "board_data_version": "classic-us-40-v1",
        "card_data_version": "classic-cards-v1",
        "output_directory": "runs",
    }


def test_config_hash_is_stable_for_equivalent_models() -> None:
    first = GameConfig.model_validate(config_data())
    second = GameConfig.model_validate(config_data())

    assert canonical_config_json(first) == canonical_config_json(second)
    assert config_hash(first) == config_hash(second)


def test_config_rejects_duplicate_seats() -> None:
    data = config_data()
    data["players"] = [
        {"player_id": "a", "seat": 1},
        {"player_id": "b", "seat": 1},
    ]

    with pytest.raises(ValidationError, match="seats must be unique"):
        GameConfig.model_validate(data)


def test_config_rejects_future_rule_levels() -> None:
    data = config_data()
    data["rules_level"] = 1

    with pytest.raises(ValidationError, match="Level 0"):
        GameConfig.model_validate(data)


def test_load_game_config_reads_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "game.yaml"
    config_path.write_text(
        """game_id: game-001
experiment_id: experiment-001
seed: 42
players:
  - player_id: a
    seat: 1
  - player_id: b
    seat: 2
rules_version: classic-level0-v1
rules_level: 0
board_data_version: classic-us-40-v1
card_data_version: classic-cards-v1
""",
        encoding="utf-8",
    )

    assert load_game_config(config_path).game_id == "game-001"
