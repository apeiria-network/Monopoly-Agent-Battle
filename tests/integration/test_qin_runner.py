"""Integration coverage for the Qin four-role court."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from monopoly_agent_battle.agents.qin import QinCourtAgent
from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.config.models import (
    GameConfig,
    ModelProfile,
    PlayerConfig,
    QinCourtRoleProfiles,
)
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.prompts import options_from_prompt
from monopoly_agent_battle.decision.runner import DispatchController, run_decision_game
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.replay import verify_run
from monopoly_agent_battle.llm.mock_client import MockLLMClient, ResponsePolicy
from monopoly_agent_battle.llm.protocol import LLMRequest
from monopoly_agent_battle.llm.recording_client import RecordingLLMClient
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts
from monopoly_agent_battle.performance.tracker import PerformanceTracker


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _result(run_directory: Path) -> dict[str, Any]:
    return json.loads((run_directory / "result.json").read_text(encoding="utf-8"))


def _config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="qin-integration",
        experiment_id="qin-integration",
        seed=0,
        players=(
            PlayerConfig(
                player_id="qin",
                seat=1,
                controller_type="qin_court",
                court_role_profiles=QinCourtRoleProfiles(
                    chancellor="chancellor",
                    grand_marshal="grand_marshal",
                    imperial_counsellor="imperial_counsellor",
                    emperor="emperor",
                ),
            ),
            PlayerConfig(player_id="random", seat=2, controller_type="random_baseline"),
        ),
        max_complete_rounds=1,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        model_profiles={
            role: ModelProfile(provider="mock", model=f"mock-{role}-v1")
            for role in ("chancellor", "grand_marshal", "imperial_counsellor", "emperor")
        },
        output_directory=output_directory,
    )


def _valid_choice(request: LLMRequest) -> str:
    options = options_from_prompt(request.messages[-1].content)
    option_id = options[0]["option_id"]
    return json.dumps(
        {"selected_option": {"option": option_id}, "reason": "选择当前合法默认操作。"},
        ensure_ascii=False,
    )


def _comment() -> str:
    return json.dumps(
        {
            "reason": "综合比较两位官员的本次建议。",
            "assessments": [
                {"officer_id": "chancellor", "judgement": "agree", "reason": "意见可行。"},
                {"officer_id": "grand_marshal", "judgement": "neutral", "reason": "仍需观察。"},
            ],
        },
        ensure_ascii=False,
    )


def _dispatch(
    config: GameConfig,
    artifacts: RunArtifacts,
    policy: ResponsePolicy,
) -> tuple[DispatchController, dict[str, AgentConversation]]:
    profiles = config.model_profiles
    conversations = {
        role: AgentConversation(agent_id=f"qin.{role}", window_turns=config.window_turns)
        for role in ("chancellor", "grand_marshal", "imperial_counsellor", "emperor")
    }
    qin = QinCourtAgent(
        player_id="qin",
        chancellor_client=RecordingLLMClient(MockLLMClient(policy), artifacts),
        chancellor_profile=profiles["chancellor"],
        grand_marshal_client=RecordingLLMClient(MockLLMClient(policy), artifacts),
        grand_marshal_profile=profiles["grand_marshal"],
        imperial_counsellor_client=RecordingLLMClient(MockLLMClient(policy), artifacts),
        imperial_counsellor_profile=profiles["imperial_counsellor"],
        emperor_client=RecordingLLMClient(MockLLMClient(policy), artifacts),
        emperor_profile=profiles["emperor"],
        conversations=conversations,
    )
    return (
        DispatchController({"qin": qin, "random": RandomBaselineController(random.Random(1))}),
        {
            "qin": conversations["emperor"],
            **{f"qin.{role}": conv for role, conv in conversations.items()},
        },
    )


def _run(tmp_path: Path, policy: ResponsePolicy) -> tuple[RunArtifacts, list[LLMRequest]]:
    config = _config(tmp_path)
    artifacts = RunArtifacts.create(config)
    captured: list[LLMRequest] = []

    def capture_policy(request: LLMRequest) -> str:
        captured.append(request)
        return policy(request)

    controller, conversations = _dispatch(config, artifacts, capture_policy)
    engine = GameEngine(config)
    tracker = PerformanceTracker(engine, {"qin": "qin_court"})
    run_decision_game(
        engine,
        controller,
        artifacts,
        conversations=conversations,
        performance_tracker=tracker,
    )
    return artifacts, captured


def test_qin_terminal_performance_is_persisted_once(tmp_path: Path) -> None:
    artifacts, _ = _run(tmp_path, lambda request: _valid_choice(request))
    performance = _records(artifacts.run_directory / "performance.jsonl")
    keys = [
        (
            record["player_id"],
            record["window"],
            record["start_action_turn"],
            record["end_action_turn"],
        )
        for record in performance
    ]
    assert performance
    assert len(keys) == len(set(keys))
    assert any(record["end_action_turn"] > 1 for record in performance)


def test_qin_four_roles_run_and_performance_context_are_auditable(tmp_path: Path) -> None:
    def policy(request: LLMRequest) -> str:
        if request.caller_role.endswith(".imperial_counsellor"):
            return _comment()
        return _valid_choice(request)

    artifacts, captured = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    decisions = _records(run_directory / "decisions.jsonl")
    llm_calls = _records(run_directory / "llm_calls.jsonl")
    qin_decisions = [record for record in decisions if record["request"]["player_id"] == "qin"]

    assert qin_decisions
    assert all(
        [call["role"] for call in record["court_trace"]["calls"]]
        == ["chancellor", "grand_marshal", "imperial_counsellor", "emperor"]
        for record in qin_decisions
    )
    assert {record["caller_role"] for record in llm_calls} == {
        "qin.chancellor",
        "qin.grand_marshal",
        "qin.imperial_counsellor",
        "qin.emperor",
    }
    counsellor_requests = [
        request for request in captured if request.caller_role == "qin.imperial_counsellor"
    ]
    assert counsellor_requests
    assert all("## 官员绩效" not in request.messages[-1].content for request in counsellor_requests)
    assert _result(run_directory)["llm_calls"] == len(llm_calls)
    verify_run(run_directory)


def test_qin_emperor_validation_retry_is_auditable(tmp_path: Path) -> None:
    emperor_attempts = 0

    def policy(request: LLMRequest) -> str:
        nonlocal emperor_attempts
        if request.caller_role.endswith(".imperial_counsellor"):
            return _comment()
        if request.caller_role.endswith(".emperor"):
            emperor_attempts += 1
            if emperor_attempts == 1:
                return '{"selected_option":{"option":"illegal"},"reason":"错误"}'
        return _valid_choice(request)

    artifacts, captured = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    first_qin = next(
        record
        for record in _records(run_directory / "decisions.jsonl")
        if record["request"]["player_id"] == "qin"
    )
    llm_calls = _records(run_directory / "llm_calls.jsonl")
    emperor_requests = [request for request in captured if request.caller_role.endswith(".emperor")]

    assert first_qin["validation_retries"] >= 1
    assert len(emperor_requests) >= 2
    assert "Error:" in emperor_requests[1].messages[-1].content
    assert "illegal" in emperor_requests[1].messages[-2].content
    assert [call["caller_role"] for call in llm_calls[:5]].count("qin.emperor") >= 2
    verify_run(run_directory)


def test_qin_adviser_and_counsellor_retries_replay_feedback(tmp_path: Path) -> None:
    attempts: dict[str, int] = {}

    def policy(request: LLMRequest) -> str:
        role = request.caller_role.rsplit(".", 1)[-1]
        attempts[role] = attempts.get(role, 0) + 1
        if role == "imperial_counsellor" and attempts[role] == 1:
            return "{}"
        if role == "chancellor" and attempts[role] == 1:
            return "{}"
        if role == "imperial_counsellor":
            return _comment()
        return _valid_choice(request)

    artifacts, captured = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    chancellor_retry = next(
        request
        for request in captured
        if request.caller_role == "qin.chancellor" and "Error:" in request.messages[-1].content
    )
    counsellor_retry = next(
        request
        for request in captured
        if request.caller_role == "qin.imperial_counsellor"
        and "评价结构非法" in request.messages[-1].content
    )
    assert "{}" in chancellor_retry.messages[-2].content
    assert "{}" in counsellor_retry.messages[-2].content
    verify_run(run_directory)
