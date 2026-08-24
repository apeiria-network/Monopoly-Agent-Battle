"""Integration coverage for the provisional Shang two-role court."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.agents.shang import ShangCourtAgent
from monopoly_agent_battle.config.models import (
    GameConfig,
    ModelProfile,
    PlayerConfig,
    ShangCourtRoleProfiles,
)
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.prompts import options_from_prompt
from monopoly_agent_battle.decision.runner import DispatchController, run_decision_game
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.replay import verify_run
from monopoly_agent_battle.llm.mock_client import MockLLMClient, ResponsePolicy
from monopoly_agent_battle.llm.protocol import LLMConnectionError, LLMRequest
from monopoly_agent_battle.llm.recording_client import RecordingLLMClient
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts

_ORACLE = "神谕提示：审视眼前局势，谨慎权衡当下行动。"


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _result(run_directory: Path) -> dict[str, Any]:
    return json.loads((run_directory / "result.json").read_text(encoding="utf-8"))


def _config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="shang-integration",
        experiment_id="shang-integration",
        seed=0,
        players=(
            PlayerConfig(
                player_id="shang",
                seat=1,
                controller_type="shang_court",
                court_role_profiles=ShangCourtRoleProfiles(
                    great_priest="priest", emperor="emperor"
                ),
            ),
            PlayerConfig(player_id="random", seat=2, controller_type="random_baseline"),
        ),
        max_complete_rounds=1,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        model_profiles={
            "priest": ModelProfile(provider="mock", model="mock-priest-v1"),
            "emperor": ModelProfile(provider="mock", model="mock-emperor-v1"),
        },
        output_directory=output_directory,
    )


def _valid_choice(request: LLMRequest) -> str:
    option_id = options_from_prompt(request.messages[-1].content)[0]["option_id"]
    return json.dumps(
        {"selected_option": {"option": option_id}, "reason": "皇帝选择默认合法操作。"},
        ensure_ascii=False,
    )


def _dispatch(
    config: GameConfig,
    artifacts: RunArtifacts,
    policy: ResponsePolicy,
) -> tuple[DispatchController, dict[str, AgentConversation]]:
    priest_profile = config.model_profiles["priest"]
    emperor_profile = config.model_profiles["emperor"]
    conversation = AgentConversation(agent_id="shang", window_turns=config.window_turns)
    shang = ShangCourtAgent(
        player_id="shang",
        great_priest_client=RecordingLLMClient(MockLLMClient(policy), artifacts),
        great_priest_profile=priest_profile,
        emperor_client=RecordingLLMClient(MockLLMClient(policy), artifacts),
        emperor_profile=emperor_profile,
        emperor_conversation=conversation,
    )
    return (
        DispatchController(
            {
                "shang": shang,
                "random": RandomBaselineController(random.Random(1)),
            }
        ),
        {"shang": conversation},
    )


def _run(tmp_path: Path, policy: ResponsePolicy) -> tuple[RunArtifacts, list[LLMRequest]]:
    config = _config(tmp_path)
    artifacts = RunArtifacts.create(config)
    captured: list[LLMRequest] = []

    def capture_policy(request: LLMRequest) -> str:
        captured.append(request)
        return policy(request)

    controller, conversations = _dispatch(config, artifacts, capture_policy)
    run_decision_game(GameEngine(config), controller, artifacts, conversations=conversations)
    return artifacts, captured


def test_shang_court_keeps_oracle_private_and_replayable(tmp_path: Path) -> None:
    def policy(request: LLMRequest) -> str:
        if request.caller_role.endswith(".great_priest"):
            return _ORACLE
        return _valid_choice(request)

    artifacts, captured = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    decisions = _records(run_directory / "decisions.jsonl")
    llm_calls = _records(run_directory / "llm_calls.jsonl")
    result = _result(run_directory)
    shang_decisions = [record for record in decisions if record["request"]["player_id"] == "shang"]

    assert shang_decisions
    assert all("court_trace" in record for record in shang_decisions)
    assert all(
        [call["role"] for call in record["court_trace"]["calls"]] == ["great_priest", "emperor"]
        for record in shang_decisions
    )
    assert [record["call_id"] for record in llm_calls] == list(range(1, len(llm_calls) + 1))
    assert {record["caller_role"] for record in llm_calls} == {
        "shang.great_priest",
        "shang.emperor",
    }
    assert result["llm_calls"] == len(llm_calls)
    assert result["reconnect_events"] == 0
    assert result["validity_status"] == "valid"

    priest_requests = [
        request for request in captured if request.caller_role == "shang.great_priest"
    ]
    emperor_requests = [request for request in captured if request.caller_role == "shang.emperor"]
    assert priest_requests and emperor_requests
    assert all(
        [message.role for message in request.messages] == ["system", "user"]
        for request in priest_requests
    )
    assert all("## 合法候选操作" not in request.messages[-1].content for request in priest_requests)
    assert all(_ORACLE in request.messages[-1].content for request in emperor_requests)
    assert all(options_from_prompt(request.messages[-1].content) for request in emperor_requests)
    assert _ORACLE not in (run_directory / "events.jsonl").read_text(encoding="utf-8")
    assert all(
        "神谕" not in json.dumps(record, ensure_ascii=False)
        for record in decisions
        if record["request"]["player_id"] == "random"
    )
    verify_run(run_directory)


def test_emperor_connection_retries_only_emperor_and_counts_all_calls(tmp_path: Path) -> None:
    emperor_attempts = 0

    def policy(request: LLMRequest) -> str:
        nonlocal emperor_attempts
        if request.caller_role.endswith(".great_priest"):
            return _ORACLE
        emperor_attempts += 1
        if emperor_attempts == 1:
            raise LLMConnectionError("emperor unavailable")
        return _valid_choice(request)

    artifacts, _ = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    first_shang = next(
        record
        for record in _records(run_directory / "decisions.jsonl")
        if record["request"]["player_id"] == "shang"
    )
    llm_calls = _records(run_directory / "llm_calls.jsonl")
    result = _result(run_directory)

    assert [call["role"] for call in first_shang["court_trace"]["calls"]] == [
        "great_priest",
        "emperor",
        "emperor",
    ]
    assert first_shang["connection_retries"] == 1
    assert [record["caller_role"] for record in llm_calls[:3]] == [
        "shang.great_priest",
        "shang.emperor",
        "shang.emperor",
    ]
    assert llm_calls[1]["error"] == "emperor unavailable"
    assert result["llm_calls"] == len(llm_calls)
    assert result["reconnect_events"] == 1
    assert result["validity_status"] == "invalid"
    verify_run(run_directory)


def test_emperor_validation_retry_keeps_oracle_and_priest_stage(tmp_path: Path) -> None:
    emperor_attempts = 0

    def policy(request: LLMRequest) -> str:
        nonlocal emperor_attempts
        if request.caller_role.endswith(".great_priest"):
            return _ORACLE
        emperor_attempts += 1
        if emperor_attempts == 1:
            return '{"selected_option":{"option":"illegal"},"reason":"x"}'
        return _valid_choice(request)

    artifacts, _ = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    first_shang = next(
        record
        for record in _records(run_directory / "decisions.jsonl")
        if record["request"]["player_id"] == "shang"
    )
    llm_calls = _records(run_directory / "llm_calls.jsonl")

    assert [call["role"] for call in first_shang["court_trace"]["calls"]] == [
        "great_priest",
        "emperor",
        "emperor",
    ]
    assert first_shang["validation_retries"] == 1
    assert first_shang["validation_errors"] == ["selected_option is not a legal candidate"]
    assert [record["caller_role"] for record in llm_calls[:3]] == [
        "shang.great_priest",
        "shang.emperor",
        "shang.emperor",
    ]
    assert _result(run_directory)["llm_calls"] == len(llm_calls)
    verify_run(run_directory)


def test_priest_connection_exhaustion_falls_back_and_is_auditable(tmp_path: Path) -> None:
    def policy(request: LLMRequest) -> str:
        if request.caller_role.endswith(".great_priest"):
            raise LLMConnectionError("priest unavailable")
        return _valid_choice(request)

    artifacts, _ = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    first_shang = next(
        record
        for record in _records(run_directory / "decisions.jsonl")
        if record["request"]["player_id"] == "shang"
    )
    llm_calls = _records(run_directory / "llm_calls.jsonl")
    result = _result(run_directory)

    assert first_shang["fallback"] is True
    assert first_shang["connection_retries"] == 3
    assert [call["role"] for call in first_shang["court_trace"]["calls"]] == [
        "great_priest",
        "great_priest",
        "great_priest",
    ]
    assert [record["caller_role"] for record in llm_calls[:3]] == [
        "shang.great_priest",
        "shang.great_priest",
        "shang.great_priest",
    ]
    assert result["llm_calls"] == len(llm_calls)
    assert result["reconnect_events"] >= 3
    assert result["validity_status"] == "invalid"
    verify_run(run_directory)
