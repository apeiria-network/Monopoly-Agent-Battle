"""Integration coverage for the Tang serial three-role court."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.agents.tang import TangCourtAgent
from monopoly_agent_battle.config.models import (
    GameConfig,
    ModelProfile,
    PlayerConfig,
    TangCourtRoleProfiles,
)
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.prompts import options_from_prompt
from monopoly_agent_battle.decision.runner import DispatchController, run_decision_game
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.replay import verify_run
from monopoly_agent_battle.llm.mock_client import MockLLMClient
from monopoly_agent_battle.llm.protocol import LLMConnectionError, LLMRequest
from monopoly_agent_battle.llm.recording_client import RecordingLLMClient
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="tang-integration",
        experiment_id="tang-integration",
        seed=0,
        players=(
            PlayerConfig(
                player_id="tang",
                seat=1,
                controller_type="tang_court",
                court_role_profiles=TangCourtRoleProfiles(
                    shangshu="shangshu",
                    zhongshu="zhongshu",
                    menxia="menxia",
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
            for role in ("shangshu", "zhongshu", "menxia", "emperor")
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


def _review(verdict: str) -> str:
    return json.dumps(
        {"reason": "审核当前草案。", "selected_option": {"option": verdict}},
        ensure_ascii=False,
    )


def _run(
    tmp_path: Path,
    policy: Any,
) -> tuple[RunArtifacts, list[LLMRequest]]:
    config = _config(tmp_path)
    artifacts = RunArtifacts.create(config)
    captured: list[LLMRequest] = []

    def capture(request: LLMRequest) -> str:
        captured.append(request)
        return policy(request)

    profiles = config.model_profiles
    conversations = {
        role: AgentConversation(agent_id=f"tang.{role}", window_turns=1)
        for role in ("shangshu", "zhongshu", "menxia", "emperor")
    }
    clients = {
        role: RecordingLLMClient(MockLLMClient(capture), artifacts) for role in conversations
    }
    agent = TangCourtAgent(
        player_id="tang",
        shangshu_client=clients["shangshu"],
        shangshu_profile=profiles["shangshu"],
        zhongshu_client=clients["zhongshu"],
        zhongshu_profile=profiles["zhongshu"],
        menxia_client=clients["menxia"],
        menxia_profile=profiles["menxia"],
        emperor_client=clients["emperor"],
        emperor_profile=profiles["emperor"],
        conversations=conversations,
    )
    controller = DispatchController(
        {"tang": agent, "random": RandomBaselineController(random.Random(1))}
    )
    run_decision_game(
        GameEngine(config),
        controller,
        artifacts,
        conversations={"tang": conversations, **{f"tang.{r}": c for r, c in conversations.items()}},
    )
    return artifacts, captured


def test_tang_run_audits_roles_and_replays(tmp_path: Path) -> None:
    def policy(request: LLMRequest) -> str:
        if request.caller_role.endswith(".shangshu"):
            return "尚书省摘要：双方现金与地产暂无重大变化。"
        if request.caller_role.endswith(".menxia"):
            return _review("agree")
        return _valid_choice(request)

    artifacts, captured = _run(tmp_path, policy)
    decisions = _records(artifacts.run_directory / "decisions.jsonl")
    calls = _records(artifacts.run_directory / "llm_calls.jsonl")
    tang_decisions = [record for record in decisions if record["request"]["player_id"] == "tang"]
    assert tang_decisions
    assert all(
        [call["role"] for call in record["court_trace"]["calls"]]
        == ["shangshu", "zhongshu", "menxia", "emperor"]
        for record in tang_decisions
    )
    assert {call["caller_role"] for call in calls} == {
        "tang.shangshu",
        "tang.zhongshu",
        "tang.menxia",
        "tang.emperor",
    }
    assert any(
        request.caller_role == "tang.zhongshu"
        and '"decision_maker":"shangshu"' in request.messages[-1].content
        for request in captured
    )
    assert any(request.caller_role == "tang.emperor" for request in captured)
    verify_run(artifacts.run_directory)


def test_tang_internal_retries_and_connection_retry_are_auditable(tmp_path: Path) -> None:
    attempts: dict[str, int] = {}

    def policy(request: LLMRequest) -> str:
        role = request.caller_role.rsplit(".", 1)[-1]
        attempts[role] = attempts.get(role, 0) + 1
        if role == "shangshu":
            return "尚书省摘要：双方现金与地产暂无重大变化。"
        if role == "zhongshu" and attempts[role] == 1:
            raise LLMConnectionError("temporary Zhongshu outage")
        if role == "menxia" and attempts[role] == 1:
            return '{"reason":"bad","selected_option":{"option":"agree","target":"x"}}'
        if role == "menxia":
            return _review("agree")
        return _valid_choice(request)

    artifacts, captured = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    tang_decision = next(
        record
        for record in _records(run_directory / "decisions.jsonl")
        if record["request"]["player_id"] == "tang"
    )
    assert tang_decision["connection_retries"] == 1
    assert tang_decision["validation_retries"] == 0
    assert any(
        call["role"] == "menxia" and call["outcome"] == "validation_error"
        for call in tang_decision["court_trace"]["calls"]
    )
    assert any(
        request.caller_role == "tang.menxia" and "审核结构非法" in request.messages[-1].content
        for request in captured
    )
    verify_run(run_directory)


def test_tang_shangshu_exhaustion_falls_back_within_runner(tmp_path: Path) -> None:
    def policy(request: LLMRequest) -> str:
        if request.caller_role.endswith(".shangshu"):
            raise LLMConnectionError("尚书省持续不可用")
        if request.caller_role.endswith(".menxia"):
            return _review("agree")
        return _valid_choice(request)

    artifacts, captured = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    tang_decision = next(
        record
        for record in _records(run_directory / "decisions.jsonl")
        if record["request"]["player_id"] == "tang"
    )
    assert tang_decision["connection_retries"] == 2
    shangshu_calls = [
        call for call in tang_decision["court_trace"]["calls"] if call["role"] == "shangshu"
    ]
    assert [call["outcome"] for call in shangshu_calls] == [
        "connection_error",
        "connection_error",
        "connection_error",
        "summary_fallback",
    ]
    assert any(
        "尚书省重连次数耗尽，无法做出有效回复" in request.messages[-1].content
        for request in captured
        if request.caller_role == "tang.emperor"
    )
    verify_run(run_directory)
