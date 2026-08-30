from pathlib import Path

import pytest
from pydantic import ValidationError

from monopoly_agent_battle.config.loader import canonical_config_json, config_hash, load_game_config
from monopoly_agent_battle.config.models import (
    GameConfig,
    QinCourtRoleProfiles,
    ShangCourtRoleProfiles,
)


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


def test_config_accepts_independent_openai_compatible_profiles() -> None:
    data = config_data()
    data["model_profiles"] = {
        "first": {
            "provider": "openai_compatible",
            "base_url": "https://first.example/v1",
            "api_key_env": "FIRST_API_KEY",
            "model": "first-model",
            "seed": 42,
        },
        "second": {
            "provider": "openai_compatible",
            "base_url": "https://second.example/v1",
            "api_key_env": "SECOND_API_KEY",
            "model": "second-model",
            "seed": 42,
        },
    }
    data["players"] = [
        {
            "player_id": "a",
            "seat": 1,
            "controller_type": "llm_baseline",
            "model_profile": "first",
        },
        {
            "player_id": "b",
            "seat": 2,
            "controller_type": "llm_baseline",
            "model_profile": "second",
        },
    ]

    config = GameConfig.model_validate(data)

    assert config.model_profiles["first"].base_url == "https://first.example/v1"
    assert config.model_profiles["first"].api_key_env == "FIRST_API_KEY"
    assert config.model_profiles["first"].seed == 42
    assert config.model_profiles["second"].base_url == "https://second.example/v1"
    assert config.model_profiles["second"].api_key_env == "SECOND_API_KEY"
    assert config.model_profiles["second"].seed == 42


def test_config_rejects_incomplete_openai_compatible_profile() -> None:
    data = config_data()
    data["model_profiles"] = {"real": {"provider": "openai_compatible", "model": "model-only"}}

    with pytest.raises(ValidationError, match="requires: base_url, api_key_env"):
        GameConfig.model_validate(data)


def test_config_rejects_plaintext_api_key() -> None:
    data = config_data()
    data["model_profiles"] = {
        "real": {
            "provider": "openai_compatible",
            "base_url": "https://example.com/v1",
            "api_key_env": "REAL_API_KEY",
            "api_key": "must-not-be-accepted",
            "model": "model",
        }
    }

    with pytest.raises(ValidationError, match="api_key"):
        GameConfig.model_validate(data)


def test_config_hash_contains_credential_reference_not_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REAL_API_KEY", "actual-secret-value")
    data = config_data()
    data["model_profiles"] = {
        "real": {
            "provider": "openai_compatible",
            "base_url": "https://example.com/v1",
            "api_key_env": "REAL_API_KEY",
            "model": "model",
        }
    }

    config = GameConfig.model_validate(data)
    frozen = canonical_config_json(config)

    assert "REAL_API_KEY" in frozen
    assert "actual-secret-value" not in frozen


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


def test_config_accepts_shang_court_with_independent_role_profiles() -> None:
    data = config_data()
    data["model_profiles"] = {
        "priest": {"provider": "mock", "model": "mock-priest-v1"},
        "emperor": {"provider": "mock", "model": "mock-emperor-v1"},
    }
    data["players"] = [
        {
            "player_id": "a",
            "seat": 1,
            "controller_type": "shang_court",
            "court_role_profiles": {"great_priest": "priest", "emperor": "emperor"},
        },
        {"player_id": "b", "seat": 2},
    ]

    config = GameConfig.model_validate(data)

    assert config.players[0].model_profile is None
    profiles = config.players[0].court_role_profiles
    assert isinstance(profiles, ShangCourtRoleProfiles)
    assert profiles.great_priest == "priest"
    assert profiles.emperor == "emperor"


def test_config_rejects_shang_court_without_role_profiles() -> None:
    data = config_data()
    data["players"] = [
        {"player_id": "a", "seat": 1, "controller_type": "shang_court"},
        {"player_id": "b", "seat": 2},
    ]

    with pytest.raises(ValidationError, match="requires court_role_profiles"):
        GameConfig.model_validate(data)


def test_config_rejects_shang_court_with_legacy_profile() -> None:
    data = config_data()
    data["model_profiles"] = {"mock": {"provider": "mock", "model": "mock-v1"}}
    data["players"] = [
        {
            "player_id": "a",
            "seat": 1,
            "controller_type": "shang_court",
            "model_profile": "mock",
            "court_role_profiles": {"great_priest": "mock", "emperor": "mock"},
        },
        {"player_id": "b", "seat": 2},
    ]

    with pytest.raises(ValidationError, match="must not set model_profile"):
        GameConfig.model_validate(data)


def test_config_rejects_undefined_shang_role_profile() -> None:
    data = config_data()
    data["model_profiles"] = {"priest": {"provider": "mock", "model": "mock-priest-v1"}}
    data["players"] = [
        {
            "player_id": "a",
            "seat": 1,
            "controller_type": "shang_court",
            "court_role_profiles": {"great_priest": "priest", "emperor": "missing"},
        },
        {"player_id": "b", "seat": 2},
    ]

    with pytest.raises(ValidationError, match="model_profile not defined"):
        GameConfig.model_validate(data)


def _qin_model_profiles() -> dict[str, object]:
    return {
        role: {"provider": "mock", "model": f"mock-{role}-v1"}
        for role in ("chancellor", "grand_marshal", "imperial_counsellor", "emperor")
    }


def _qin_role_profiles() -> dict[str, str]:
    return {
        "chancellor": "chancellor",
        "grand_marshal": "grand_marshal",
        "imperial_counsellor": "imperial_counsellor",
        "emperor": "emperor",
    }


def test_config_accepts_qin_court_with_four_role_profiles() -> None:
    data = config_data()
    data["model_profiles"] = _qin_model_profiles()
    data["players"] = [
        {
            "player_id": "a",
            "seat": 1,
            "controller_type": "qin_court",
            "court_role_profiles": _qin_role_profiles(),
        },
        {"player_id": "b", "seat": 2},
    ]

    config = GameConfig.model_validate(data)

    assert config.players[0].model_profile is None
    profiles = config.players[0].court_role_profiles
    assert isinstance(profiles, QinCourtRoleProfiles)
    assert profiles.chancellor == "chancellor"
    assert profiles.grand_marshal == "grand_marshal"
    assert profiles.imperial_counsellor == "imperial_counsellor"
    assert profiles.emperor == "emperor"


def test_config_rejects_qin_court_without_role_profiles() -> None:
    data = config_data()
    data["players"] = [
        {"player_id": "a", "seat": 1, "controller_type": "qin_court"},
        {"player_id": "b", "seat": 2},
    ]

    with pytest.raises(ValidationError, match="requires court_role_profiles"):
        GameConfig.model_validate(data)


def test_config_rejects_qin_court_with_shang_role_profiles() -> None:
    data = config_data()
    data["model_profiles"] = {
        "priest": {"provider": "mock", "model": "mock-priest-v1"},
        "emperor": {"provider": "mock", "model": "mock-emperor-v1"},
    }
    data["players"] = [
        {
            "player_id": "a",
            "seat": 1,
            "controller_type": "qin_court",
            "court_role_profiles": {"great_priest": "priest", "emperor": "emperor"},
        },
        {"player_id": "b", "seat": 2},
    ]

    with pytest.raises(ValidationError):
        GameConfig.model_validate(data)


def test_shang_role_profiles_change_config_hash() -> None:
    first = config_data()
    first["model_profiles"] = {
        "priest-a": {"provider": "mock", "model": "mock-priest-a"},
        "priest-b": {"provider": "mock", "model": "mock-priest-b"},
        "emperor": {"provider": "mock", "model": "mock-emperor"},
    }
    first["players"] = [
        {
            "player_id": "a",
            "seat": 1,
            "controller_type": "shang_court",
            "court_role_profiles": {"great_priest": "priest-a", "emperor": "emperor"},
        },
        {"player_id": "b", "seat": 2},
    ]
    second = dict(first)
    second["players"] = [
        {
            "player_id": "a",
            "seat": 1,
            "controller_type": "shang_court",
            "court_role_profiles": {"great_priest": "priest-b", "emperor": "emperor"},
        },
        {"player_id": "b", "seat": 2},
    ]

    assert config_hash(GameConfig.model_validate(first)) != config_hash(
        GameConfig.model_validate(second)
    )


def test_config_has_stage4_context_defaults() -> None:
    config = GameConfig.model_validate(config_data())
    assert config.validation_retries == 2
    assert config.window_turns == 1
    assert config.sentence_template_version is None
    assert config.context_token_cap is None
