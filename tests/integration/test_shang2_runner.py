"""Integration coverage for the redesigned Shang2 four-role court."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.agents.shang2 import Shang2CourtAgent
from monopoly_agent_battle.config.models import (
    GameConfig,
    ModelProfile,
    PlayerConfig,
    Shang2CourtRoleProfiles,
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

_OMENS = ("大吉", "中吉", "小吉", "小凶", "中凶", "大凶")
_ROLES = ("minister_1", "minister_2", "minister_3", "emperor")


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _result(run_directory: Path) -> dict[str, Any]:
    return json.loads((run_directory / "result.json").read_text(encoding="utf-8"))


def _config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="shang2-integration",
        experiment_id="shang2-integration",
        seed=0,
        players=(
            PlayerConfig(
                player_id="shang2",
                seat=1,
                controller_type="shang2_court",
                court_role_profiles=Shang2CourtRoleProfiles(
                    minister_1="minister",
                    minister_2="minister",
                    minister_3="minister",
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
            "minister": ModelProfile(provider="mock", model="mock-shang2-minister-v1"),
            "emperor": ModelProfile(provider="mock", model="mock-shang2-emperor-v1"),
        },
        output_directory=output_directory,
    )


def _valid_choice(request: LLMRequest) -> str:
    option_id = options_from_prompt(request.messages[-1].content)[0]["option_id"]
    return json.dumps(
        {"selected_option": {"option": option_id}, "reason": "选择合法操作。"},
        ensure_ascii=False,
    )


def _dispatch(
    config: GameConfig,
    artifacts: RunArtifacts,
    policy: ResponsePolicy,
) -> tuple[DispatchController, dict[str, Any]]:
    conversations = {
        role: AgentConversation(agent_id=f"shang2.{role}", window_turns=config.window_turns)
        for role in _ROLES
    }
    clients = {role: RecordingLLMClient(MockLLMClient(policy), artifacts) for role in _ROLES}
    minister_profile = config.model_profiles["minister"]
    emperor_profile = config.model_profiles["emperor"]
    agent = Shang2CourtAgent(
        player_id="shang2",
        seed=config.seed,
        minister_1_client=clients["minister_1"],
        minister_1_profile=minister_profile,
        minister_2_client=clients["minister_2"],
        minister_2_profile=minister_profile,
        minister_3_client=clients["minister_3"],
        minister_3_profile=minister_profile,
        emperor_client=clients["emperor"],
        emperor_profile=emperor_profile,
        conversations=conversations,
    )
    return (
        DispatchController(
            {
                "shang2": agent,
                "random": RandomBaselineController(random.Random(1)),
            }
        ),
        {"shang2": conversations},
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


def test_shang2_court_delivers_oracles_to_emperor_only(tmp_path: Path) -> None:
    artifacts, captured = _run(tmp_path, _valid_choice)
    run_directory = artifacts.run_directory
    decisions = _records(run_directory / "decisions.jsonl")
    llm_calls = _records(run_directory / "llm_calls.jsonl")
    result = _result(run_directory)
    shang_decisions = [record for record in decisions if record["request"]["player_id"] == "shang2"]

    assert shang_decisions
    assert all("court_trace" in record for record in shang_decisions)
    assert all(
        [call["role"] for call in record["court_trace"]["calls"]]
        == ["minister_1", "minister_2", "minister_3", "emperor"]
        for record in shang_decisions
    )
    for record in shang_decisions:
        oracles = record["court_trace"]["oracles"]
        assert oracles
        assert all(entry["oracle"] in _OMENS for entry in oracles)
        assert all(isinstance(entry["derivation"], str) for entry in oracles)

    assert [record["call_id"] for record in llm_calls] == list(range(1, len(llm_calls) + 1))
    assert {record["caller_role"] for record in llm_calls} == {
        "shang2.minister_1",
        "shang2.minister_2",
        "shang2.minister_3",
        "shang2.emperor",
    }
    assert result["llm_calls"] == len(llm_calls)
    assert result["reconnect_events"] == 0
    assert result["llm_fallbacks"] == 0
    assert result["validity_status"] == "valid"

    minister_requests = [
        request
        for request in captured
        if request.caller_role in {"shang2.minister_1", "shang2.minister_2", "shang2.minister_3"}
    ]
    emperor_requests = [request for request in captured if request.caller_role == "shang2.emperor"]
    assert minister_requests and emperor_requests
    for request in minister_requests:
        text = "\n".join(message.content for message in request.messages)
        assert '"oracle":' not in text
        assert options_from_prompt(request.messages[-1].content)
    for request in emperor_requests:
        text = "\n".join(message.content for message in request.messages)
        assert '"oracle":' in text
        assert options_from_prompt(request.messages[-1].content)
    assert '"oracle"' not in (run_directory / "events.jsonl").read_text(encoding="utf-8")
    assert all(
        "兆相" not in json.dumps(record, ensure_ascii=False)
        for record in decisions
        if record["request"]["player_id"] == "random"
    )
    verify_run(run_directory)


def test_minister_connection_exhaustion_falls_back_but_emperor_decides(tmp_path: Path) -> None:
    minister_attempts = 0

    def policy(request: LLMRequest) -> str:
        nonlocal minister_attempts
        if request.caller_role == "shang2.minister_1":
            minister_attempts += 1
            raise LLMConnectionError("minister unavailable")
        return _valid_choice(request)

    artifacts, _ = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    first_shang = next(
        record
        for record in _records(run_directory / "decisions.jsonl")
        if record["request"]["player_id"] == "shang2"
    )
    llm_calls = _records(run_directory / "llm_calls.jsonl")
    result = _result(run_directory)

    calls = first_shang["court_trace"]["calls"]
    assert [call["role"] for call in calls] == [
        "minister_1",
        "minister_1",
        "minister_1",
        "minister_1",
        "minister_2",
        "minister_3",
        "emperor",
    ]
    assert [call["outcome"] for call in calls] == [
        "connection_error",
        "connection_error",
        "connection_error",
        "connection_fallback",
        "success",
        "success",
        "success",
    ]
    assert "大臣一重连次数耗尽，无法做出有效回复。" in calls[3]["content"]
    assert first_shang["connection_retries"] == 2
    assert {record["caller_role"] for record in llm_calls} == {
        "shang2.minister_1",
        "shang2.minister_2",
        "shang2.minister_3",
        "shang2.emperor",
    }
    assert result["llm_calls"] == len(llm_calls)
    assert result["reconnect_events"] >= 2
    assert result["llm_fallbacks"] == 0
    assert result["fallback_events"] >= 1 
    assert result["validity_status"] == "invalid"
    verify_run(run_directory)
