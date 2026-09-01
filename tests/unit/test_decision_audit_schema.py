"""Regression tests for decision audit schema fields."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.models import (
    decision_request_record,
    validation_record,
)
from monopoly_agent_battle.decision.protocol import parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine


def make_engine(tmp_path: Path) -> GameEngine:
    config = GameConfig(
        game_id="audit-schema-game",
        experiment_id="audit-schema-experiment",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )
    return GameEngine(config)


def test_validation_audit_preserves_target_and_error_category(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    request = build_decision_request(engine, 1)

    validation = parse_and_validate(
        json.dumps({"selected_option": {"option": "mortgage", "target": 99}, "reason": "x"}),
        request,
    )
    record = validation_record(validation)

    assert record["target"] is None
    assert record["error_category"] == "invalid_target"
    assert set(record) == {
        "raw_response",
        "parsed_response",
        "validation_error",
        "error_category",
        "selected_option",
        "target",
    }


def test_decision_request_audit_preserves_target_field_mapping(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.players["a"].properties.add(1)
    engine.state.properties[1].owner_id = "a"
    engine.state.players["b"].properties.add(3)
    engine.state.properties[3].owner_id = "b"
    engine.state.players["a"].chance_cards.append("chance-swap-property")
    request = build_decision_request(engine, 1)

    record = decision_request_record(request)
    options = cast(list[dict[str, object]], record["options"])
    swap = next(
        option
        for option in options
        if option["option_id"] == "use_chance_card-chance-swap-property"
    )
    target = swap["target"]
    assert isinstance(target, dict)
    assert target["fields"] == ["swap_in_position", "swap_out_position"]
    assert target["command_fields"] == ["target_position", "secondary_target_position"]
    assert target["legal_values"]
