"""Unit tests for the deterministic random non-LLM baseline controller."""

from __future__ import annotations

import json
import random
from pathlib import Path

from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.protocol import parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine


def _engine(tmp_path: Path) -> GameEngine:
    config = GameConfig(
        game_id="random-unit",
        experiment_id="unit",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.players["a"].properties.add(1)
    engine.state.properties[1].owner_id = "a"
    engine.state.players["b"].properties.add(3)
    engine.state.properties[3].owner_id = "b"
    engine.state.players["a"].chance_cards.append("chance-swap-property")
    return engine


def test_random_baseline_is_reproducible_and_protocol_valid(tmp_path: Path) -> None:
    request = build_decision_request(_engine(tmp_path), sequence=1)
    first = RandomBaselineController(random.Random(81))
    second = RandomBaselineController(random.Random(81))

    first_replies = [first(request) for _ in range(8)]
    second_replies = [second(request) for _ in range(8)]

    assert first_replies == second_replies
    for reply in first_replies:
        validation = parse_and_validate(reply, request)
        assert validation.valid
        assert validation.option in request.options


def test_random_baseline_selects_a_legal_multi_field_target(tmp_path: Path) -> None:
    request = build_decision_request(_engine(tmp_path), sequence=1)
    option = next(
        item for item in request.options if item.option_id == "use_chance_card-chance-swap-property"
    )
    assert option.target is not None

    class FixedSelectionController(RandomBaselineController):
        def select_option_index(self, option_count: int) -> int:
            assert option_count == len(request.options)
            return request.options.index(option)

        def select_target_index(self, target_count: int) -> int:
            assert option.target is not None
            assert target_count == len(option.target.legal_values)
            return target_count - 1

    reply = FixedSelectionController(random.Random(1))(request)
    selected = json.loads(reply)["selected_option"]

    assert selected == {
        "option": option.option_id,
        "target": dict(zip(option.target.fields, option.target.legal_values[-1], strict=True)),
    }
    assert parse_and_validate(reply, request).valid


def test_random_baseline_is_marked_non_llm() -> None:
    assert RandomBaselineController(random.Random(1)).uses_llm is False
