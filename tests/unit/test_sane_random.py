"""Unit tests for the sane random controller's voluntary-disposal filtering."""

from __future__ import annotations

import random
from pathlib import Path

from monopoly_agent_battle.agents.random_baseline import SaneRandomController
from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.models import (
    DecisionKind,
    DecisionOption,
    DecisionRequest,
)
from monopoly_agent_battle.decision.protocol import parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine

_BLOCKED = {"sell_building", "mortgage"}


def _engine(tmp_path: Path) -> GameEngine:
    config = GameConfig(
        game_id="sane-random-unit",
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
    return engine


def _option(command_type: str) -> DecisionOption:
    return DecisionOption(
        option_id=command_type,
        command_type=command_type,
        parameters={},
        title=command_type,
        preview="",
        response_format={},
        is_default=False,
        target=None,
    )


def _request(kind: DecisionKind, *command_types: str) -> DecisionRequest:
    return DecisionRequest(
        decision_id="d1",
        game_id="sane-random-unit",
        complete_rounds=1,
        player_id="a",
        phase="test",
        kind=kind,
        question="q",
        visible_state={},
        options=tuple(_option(command_type) for command_type in command_types),
        output_constraints={},
    )


def _drawn_command_types(controller: SaneRandomController, request: DecisionRequest) -> set[str]:
    drawn: set[str] = set()
    for _ in range(300):
        validation = parse_and_validate(controller(request), request)
        assert validation.valid
        assert validation.option is not None
        drawn.add(validation.option.command_type)
    return drawn


def test_sane_random_skips_voluntary_disposal_in_asset_management(tmp_path: Path) -> None:
    request = build_decision_request(_engine(tmp_path), sequence=1)
    assert any(option.command_type == "mortgage" for option in request.options)

    drawn = _drawn_command_types(SaneRandomController(random.Random(7)), request)

    assert drawn
    assert not drawn & _BLOCKED


def test_sane_random_keeps_redeem_but_blocks_mortgage_in_asset_management() -> None:
    request = _request(DecisionKind.ASSET_MANAGEMENT, "redeem_mortgage", "mortgage", "end_turn")

    drawn = _drawn_command_types(SaneRandomController(random.Random(11)), request)

    assert "redeem_mortgage" in drawn
    assert not drawn & _BLOCKED


def test_sane_random_stays_fully_random_in_payment_resolution() -> None:
    request = _request(DecisionKind.PAYMENT_RESOLUTION, "mortgage", "declare_bankruptcy")

    drawn = _drawn_command_types(SaneRandomController(random.Random(13)), request)

    assert "mortgage" in drawn


def test_sane_random_falls_back_when_all_asset_options_are_blocked() -> None:
    request = _request(DecisionKind.ASSET_MANAGEMENT, "mortgage", "sell_building")

    validation = parse_and_validate(SaneRandomController(random.Random(3))(request), request)

    assert validation.valid


def test_sane_random_is_reproducible_and_non_llm(tmp_path: Path) -> None:
    request = build_decision_request(_engine(tmp_path), sequence=1)
    first = SaneRandomController(random.Random(81))
    second = SaneRandomController(random.Random(81))

    assert [first(request) for _ in range(8)] == [second(request) for _ in range(8)]
    assert SaneRandomController(random.Random(1)).uses_llm is False
