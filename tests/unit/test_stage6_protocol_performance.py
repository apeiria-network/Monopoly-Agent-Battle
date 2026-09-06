"""Stage 6 protocol boundary and performance edge tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.protocol import parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.performance.scoring import (
    DecisionEvidence,
    DecisionSignature,
    PerformanceWindow,
    score_window,
)


def make_request(tmp_path: Path):
    config = GameConfig(
        game_id="stage6-boundary-game",
        experiment_id="stage6-boundary",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    return build_decision_request(engine, 1)


@pytest.mark.parametrize("document", [[], 1, None, "text"])
def test_protocol_rejects_non_object_top_level(tmp_path: Path, document: object) -> None:
    validation = parse_and_validate(json.dumps(document), make_request(tmp_path))
    assert validation.error_category == "not_json"
    assert validation.error == "response must be a JSON object"


@pytest.mark.parametrize("reason", [None, 1, [], {}])
def test_protocol_rejects_missing_or_non_string_reason(tmp_path: Path, reason: object) -> None:
    request = make_request(tmp_path)
    document: dict[str, object] = {"selected_option": {"option": "end_turn"}}
    if reason is not None:
        document["reason"] = reason
    validation = parse_and_validate(json.dumps(document), request)
    assert validation.error_category == "missing_reason"
    assert validation.error == "reason field is missing or not a string"


def test_extra_fields_do_not_change_materialized_choice(tmp_path: Path) -> None:
    request = make_request(tmp_path)
    plain = parse_and_validate('{"selected_option":{"option":"end_turn"},"reason":"x"}', request)
    extra = parse_and_validate(
        '{"selected_option":{"option":"end_turn","target":999,"extra":true},'
        '"reason":"x","runtime":{"retry":99}}',
        request,
    )
    assert plain.valid and extra.valid
    assert plain.option == extra.option
    assert plain.target == extra.target == {}


def test_performance_zero_decisions_with_officer_is_neutral() -> None:
    result = score_window(
        game_id="g",
        player_id="p",
        court="qin_court",
        window=PerformanceWindow.BASIC,
        start_turn=4,
        end_turn=5,
        start_net_worth=100,
        end_net_worth=90,
        decisions=[],
        officers=("officer",),
    )

    assessment = result.assessments["officer"]
    assert assessment["decision_count"] == 0
    assert assessment["consistency_ratio"] is None
    assert assessment["ratio_relation"] == "empty"
    assert assessment["bad_review"] is False
    assert assessment["reason"] == "本窗口无决策记录，不记差评。"


@pytest.mark.parametrize("selected_option", [None, [], 1, "end_turn"])
def test_protocol_rejects_non_object_selected_option(
    tmp_path: Path, selected_option: object
) -> None:
    request = make_request(tmp_path)
    validation = parse_and_validate(
        json.dumps({"selected_option": selected_option, "reason": "x"}), request
    )
    assert validation.error_category == "missing_option"
    assert validation.error == "selected_option field is missing or not a JSON object"


@pytest.mark.parametrize("reason", [None, 1, [], {}])
def test_protocol_rejects_non_string_reason(tmp_path: Path, reason: object) -> None:
    request = make_request(tmp_path)
    validation = parse_and_validate(
        json.dumps({"selected_option": {"option": "end_turn"}, "reason": reason}), request
    )
    assert validation.error_category == "missing_reason"
    assert validation.error == "reason field is missing or not a string"


def test_protocol_truncates_reason_to_four_hundred_characters(tmp_path: Path) -> None:
    request = make_request(tmp_path)
    reason = "x" * 401
    validation = parse_and_validate(
        json.dumps({"selected_option": {"option": "end_turn"}, "reason": reason}), request
    )
    assert validation.valid
    assert validation.response is not None
    assert len(validation.response.reason) == 400


def evidence(matches: tuple[bool, ...]) -> list[DecisionEvidence]:
    emperor = DecisionSignature.from_parts("end_turn", {})
    other = DecisionSignature.from_parts("mortgage", {"position": 1})
    return [
        DecisionEvidence(str(index), emperor, {"officer": emperor if match else other})
        for index, match in enumerate(matches, start=1)
    ]


@pytest.mark.parametrize(
    ("matches", "expected_relation", "expected_bad"),
    [
        ((False, False), "below", True),
        ((True, False), "equal", False),
        ((True, True), "above", False),
    ],
)
def test_performance_exact_ratio_boundaries(
    matches: tuple[bool, ...], expected_relation: str, expected_bad: bool
) -> None:
    result = score_window(
        game_id="g",
        player_id="p",
        court="qin_court",
        window=PerformanceWindow.BASIC,
        start_turn=4,
        end_turn=5,
        start_net_worth=100,
        end_net_worth=100,
        decisions=evidence(matches),
        officers=("officer",),
    )
    assessment = result.assessments["officer"]
    assert assessment["ratio_relation"] == expected_relation
    assert assessment["consistency_ratio"] == sum(matches) / len(matches)
    assert assessment["bad_review"] is expected_bad
    assert result.as_dict()["start_action_turn"] == 4
    assert result.as_dict()["end_action_turn"] == 5
