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


def test_config_accepts_model_profiles_and_player_binding() -> None:
    data = config_data()
    data["model_profiles"] = {"mock": {"provider": "mock", "model": "mock-baseline-v1"}}
    data["players"] = [
        {"player_id": "a", "seat": 1, "model_profile": "mock"},
        {"player_id": "b", "seat": 2},
    ]

    config = GameConfig.model_validate(data)
    assert config.players[0].model_profile == "mock"
    assert config.players[1].model_profile is None
    assert config.model_profiles["mock"].provider == "mock"
    assert config.model_profiles["mock"].model == "mock-baseline-v1"


def test_config_rejects_undefined_model_profile() -> None:
    data = config_data()
    data["model_profiles"] = {}
    data["players"] = [
        {"player_id": "a", "seat": 1, "model_profile": "missing"},
        {"player_id": "b", "seat": 2},
    ]

    with pytest.raises(ValidationError, match="model_profile not defined"):
        GameConfig.model_validate(data)


def test_config_accepts_explicit_random_baseline_without_model_profile() -> None:
    data = config_data()
    data["players"] = [
        {"player_id": "a", "seat": 1, "controller_type": "random_baseline"},
        {"player_id": "b", "seat": 2, "controller_type": "random_baseline"},
    ]

    config = GameConfig.model_validate(data)

    assert all(player.controller_type == "random_baseline" for player in config.players)
    assert all(player.model_profile is None for player in config.players)


def test_config_rejects_explicit_llm_baseline_without_model_profile() -> None:
    data = config_data()
    data["players"] = [
        {"player_id": "a", "seat": 1, "controller_type": "llm_baseline"},
        {"player_id": "b", "seat": 2},
    ]

    with pytest.raises(ValidationError, match="requires model_profile"):
        GameConfig.model_validate(data)


def test_config_rejects_random_baseline_with_model_profile() -> None:
    data = config_data()
    data["model_profiles"] = {"mock": {"provider": "mock", "model": "mock-baseline-v1"}}
    data["players"] = [
        {
            "player_id": "a",
            "seat": 1,
            "controller_type": "random_baseline",
            "model_profile": "mock",
        },
        {"player_id": "b", "seat": 2},
    ]

    with pytest.raises(ValidationError, match="must not set model_profile"):
        GameConfig.model_validate(data)


def test_explicit_controller_type_changes_config_hash() -> None:
    legacy = GameConfig.model_validate(config_data())
    random_data = config_data()
    random_data["players"] = [
        {"player_id": "a", "seat": 1, "controller_type": "random_baseline"},
        {"player_id": "b", "seat": 2, "controller_type": "random_baseline"},
    ]

    assert config_hash(legacy) != config_hash(GameConfig.model_validate(random_data))


def test_config_has_stage4_context_defaults() -> None:
    config = GameConfig.model_validate(config_data())
    assert config.validation_retries == 2
    assert config.window_turns == 1
    assert config.sentence_template_version is None
    assert config.context_token_cap is None
