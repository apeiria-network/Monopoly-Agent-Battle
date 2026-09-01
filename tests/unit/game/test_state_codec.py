from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.state_codec import encode_checkpoint, restore_checkpoint


def config(tmp_path: Path) -> GameConfig:
    return GameConfig(
        game_id="codec",
        experiment_id="resume",
        seed=41,
        players=(
            PlayerConfig(player_id="a", seat=1, controller_type="random_baseline"),
            PlayerConfig(player_id="b", seat=2, controller_type="random_baseline"),
        ),
        max_complete_rounds=2,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )


def test_checkpoint_round_trip_is_json_and_restores_rng(tmp_path: Path) -> None:
    value = config(tmp_path)
    source = GameEngine(value)
    source.state.players["a"].jail_roll_attempts = 2
    source.state.consecutive_doubles = 1
    source.state.completed_round_player_ids.add("a")
    source.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    source.random.gauss(0, 1)
    document = encode_checkpoint(source.state, source.random)
    serialized = json.loads(json.dumps(document))

    restored = GameEngine(value)
    restore_checkpoint(serialized, restored.state, restored.random)

    assert encode_checkpoint(restored.state, restored.random) == serialized
    assert [restored.random.randint(1, 6) for _ in range(8)] == [
        source.random.randint(1, 6) for _ in range(8)
    ]


def test_checkpoint_rejects_invalid_rng_state(tmp_path: Path) -> None:
    value = config(tmp_path)
    source = GameEngine(value)
    document = encode_checkpoint(source.state, source.random)
    document["rng_state"] = [3, [1, "invalid"], None]

    with pytest.raises(ValueError, match="invalid rng_state"):
        restore_checkpoint(document, GameEngine(value).state, random.Random())
