"""Stage 6 decision failure and audit boundary tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.protocol import default_option_json
from monopoly_agent_battle.decision.runner import (
    DispatchController,
    _validity_status,  # pyright: ignore[reportPrivateUsage]
    run_decision_game,
)
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


def make_config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="fault-audit-game",
        experiment_id="fault-audit-experiment",
        seed=3,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        max_complete_rounds=1,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=output_directory,
    )


def records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def valid_default(request: DecisionRequest) -> str:
    default = next(option for option in request.options if option.is_default)
    return json.dumps(
        {"selected_option": default_option_json(default), "reason": "选择默认合法操作。"},
        ensure_ascii=False,
    )


@pytest.mark.parametrize(
    ("invalid_reply", "expected_error"),
    [
        ("not-json", "response is not valid JSON"),
        (
            '{"selected_option":{"option":"not-a-candidate"},"reason":"x"}',
            "selected_option is not a legal candidate",
        ),
        (
            '{"selected_option":{"option":"mortgage","target":99},"reason":"x"}',
            "target value is not legal for this option",
        ),
    ],
)
def test_invalid_controller_output_cannot_execute_and_uses_default_fallback(
    tmp_path: Path,
    invalid_reply: str,
    expected_error: str,
) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)

    def controller(_request: DecisionRequest, _feedback: str | None = None) -> str:
        return invalid_reply

    run_decision_game(engine, controller, artifacts)

    first = records(artifacts.run_directory / "decisions.jsonl")[0]
    runtime_path = artifacts.run_directory / "runtime.jsonl"
    assert runtime_path.exists()
    runtime = records(runtime_path)
    assert first["attempted_validation"]["validation_error"] == expected_error
    assert first["fallback"] is True
    assert first["executed_command"]["command_type"] == "EndTurn"
    assert first["executed_command"]["command"] == {"player_id": "a"}
    assert engine.state.properties[1].owner_id == "a"
    assert any(
        item["event_type"] == "decision_fallback"
        and item["payload"]["decision_id"] == first["request"]["decision_id"]
        and item["payload"]["option_id"] == "end_turn"
        for item in runtime
    )


def test_connection_failure_uses_exactly_two_retries_then_audited_fallback(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    attempts = 0

    def controller(_request: DecisionRequest, _feedback: str | None = None) -> str:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("service unavailable")

    run_decision_game(GameEngine(config), controller, artifacts)

    runtime = records(artifacts.run_directory / "runtime.jsonl")
    connection_errors = [
        item for item in runtime if item["event_type"] == "controller_connection_error"
    ]
    first_decision = records(artifacts.run_directory / "decisions.jsonl")[0]
    assert attempts == 3 * len(records(artifacts.run_directory / "decisions.jsonl"))
    assert first_decision["connection_retries"] == 3
    assert first_decision["fallback"] is True
    assert [item["payload"]["retry"] for item in connection_errors[:3]] == [0, 1, 2]
    assert (
        connection_errors[0]["payload"]["decision_id"] == first_decision["request"]["decision_id"]
    )


@pytest.mark.parametrize(
    ("llm_calls", "llm_fallbacks", "expected"),
    [
        (0, 0, "valid"),
        (10, 0, "valid"),
        (10, 1, "invalid"),
        (11, 1, "valid"),
        (9, 1, "invalid"),
        (20, 2, "invalid"),
        (21, 2, "valid"),
    ],
)
def test_validity_status_uses_exact_ten_percent_boundary(
    llm_calls: int, llm_fallbacks: int, expected: str
) -> None:
    assert _validity_status(llm_calls, llm_fallbacks) == expected


def test_audit_decision_ids_and_fallback_validation_remain_consistent(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)

    def controller(_request: DecisionRequest, _feedback: str | None = None) -> str:
        return "not-json"

    run_decision_game(GameEngine(config), controller, artifacts)

    decisions = records(artifacts.run_directory / "decisions.jsonl")
    runtime = records(artifacts.run_directory / "runtime.jsonl")
    decision_ids = [record["request"]["decision_id"] for record in decisions]
    assert len(decision_ids) == len(set(decision_ids))
    assert all(record["fallback"] is True for record in decisions)
    assert all(record["validation"]["validation_error"] is None for record in decisions)
    assert all(
        record["attempted_validation"]["validation_error"] is not None for record in decisions
    )
    assert all(
        item["payload"]["decision_id"] in decision_ids
        for item in runtime
        if "decision_id" in item["payload"]
    )


def valid_default_controller(request: DecisionRequest, _feedback: str | None = None) -> str:
    return valid_default(request)


class NonLLMInvalidController:
    """Intentionally invalid non-LLM controller for accounting isolation tests."""

    uses_llm = False

    def __call__(self, _request: DecisionRequest, _feedback: str | None = None) -> str:
        return "not-json"


def test_non_llm_fallback_does_not_pollute_llm_validity_status(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    controller = cast(
        dict[str, Any],
        {
            "a": NonLLMInvalidController(),
            "b": valid_default_controller,
        },
    )

    run_decision_game(GameEngine(config), DispatchController(controller), artifacts)

    result = json.loads((artifacts.run_directory / "result.json").read_text(encoding="utf-8"))
    decisions = records(artifacts.run_directory / "decisions.jsonl")
    assert any(
        record["controller_type"] == "non_llm" and record["fallback"] for record in decisions
    )
    assert result["llm_calls"] > 0
    assert result["llm_fallbacks"] == 0
    assert result["validity_status"] == "valid"
