"""Integration coverage for the Ming-ablation (去汇总) court."""

from __future__ import annotations

import csv
import json
import random
import re
from pathlib import Path
from typing import Any

from monopoly_agent_battle.agents.ming_ablation import MingAblationCourtAgent
from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.config.models import (
    GameConfig,
    MingCourtRoleProfiles,
    ModelProfile,
    PlayerConfig,
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
from monopoly_agent_battle.performance.tracker import PerformanceTracker
from monopoly_agent_battle.reporting.llm_digest import write_llm_digest

_ROLES = ("chief_grand_secretary", "grand_secretary_1", "grand_secretary_2", "emperor")
_OFFICERS = _ROLES[:3]


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="ming-ablation-integration",
        experiment_id="ming-ablation-integration",
        seed=0,
        players=(
            PlayerConfig(
                player_id="ming_abl",
                seat=1,
                controller_type="ming_ablation_court",
                court_role_profiles=MingCourtRoleProfiles(
                    chief_grand_secretary="chief_grand_secretary",
                    grand_secretary_1="grand_secretary_1",
                    grand_secretary_2="grand_secretary_2",
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
            role: ModelProfile(provider="mock", model=f"mock-{role}-v1") for role in _ROLES
        },
        output_directory=output_directory,
    )


def _choice(request: LLMRequest, index: int = 0, reason: str = "选择合法操作。") -> str:
    options = options_from_prompt(request.messages[-1].content)
    option_id = options[min(index, len(options) - 1)]["option_id"]
    return json.dumps(
        {"selected_option": {"option": option_id}, "reason": reason}, ensure_ascii=False
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
        role: AgentConversation(agent_id=f"ming_abl.{role}", window_turns=1) for role in _ROLES
    }
    clients = {
        role: RecordingLLMClient(MockLLMClient(capture), artifacts) for role in conversations
    }
    agent = MingAblationCourtAgent(
        player_id="ming_abl",
        chief_client=clients["chief_grand_secretary"],
        chief_profile=profiles["chief_grand_secretary"],
        secretary_1_client=clients["grand_secretary_1"],
        secretary_1_profile=profiles["grand_secretary_1"],
        secretary_2_client=clients["grand_secretary_2"],
        secretary_2_profile=profiles["grand_secretary_2"],
        emperor_client=clients["emperor"],
        emperor_profile=profiles["emperor"],
        conversations=conversations,
    )
    controller = DispatchController(
        {"ming_abl": agent, "random": RandomBaselineController(random.Random(1))}
    )
    engine = GameEngine(config)
    tracker = PerformanceTracker(engine, {"ming_abl": "ming_ablation_court"})
    run_decision_game(
        engine,
        controller,
        artifacts,
        conversations={
            "ming_abl": conversations,
            **{f"ming_abl.{r}": c for r, c in conversations.items()},
        },
        performance_tracker=tracker,
    )
    return artifacts, captured


def _court_decisions(run_directory: Path) -> list[dict[str, Any]]:
    return [
        record
        for record in _records(run_directory / "decisions.jsonl")
        if record["request"]["player_id"] == "ming_abl"
    ]


def test_ming_ablation_run_audits_roles_and_replays(tmp_path: Path) -> None:
    artifacts, captured = _run(tmp_path, lambda request: _choice(request))
    run_directory = artifacts.run_directory
    decisions = _court_decisions(run_directory)
    assert decisions
    # Unanimous drafts: exactly 3 drafts + emperor per decision, no advice step.
    for record in decisions:
        calls = record["court_trace"]["calls"]
        assert [call["role"] for call in calls] == list(_ROLES)
        assert not any(call["content_type"] == "advice" for call in calls)
        assert record["court_trace"]["court"] == "ming_ablation"
    assert {call["caller_role"] for call in _records(run_directory / "llm_calls.jsonl")} == {
        f"ming_abl.{role}" for role in _ROLES
    }
    assert any(
        request.caller_role == "ming_abl.emperor"
        and "## 内阁意见与投票" in request.messages[-1].content
        and "全票通过" in request.messages[-1].content
        for request in captured
    )
    # Performance evidence lands for the three officers.
    performance = _records(run_directory / "performance.jsonl")
    assert performance
    officers = {
        officer for record in performance for officer in (record.get("officer_evidences") or {})
    } or {officer for record in performance for officer in (record.get("officers") or {})}
    assert officers <= set(_OFFICERS) or not officers
    # Digest contains every role's calls and no advice-only speaker.
    write_llm_digest(run_directory)
    with (run_directory / "llm_digest.csv").open(encoding="utf-8-sig", newline="") as handle:
        speakers = {row["发言者"] for row in csv.DictReader(handle)}
    assert speakers <= {f"ming_abl.{role}" for role in _ROLES} | {"random"}
    verify_run(run_directory)


def test_ming_ablation_split_workflow_reaches_emperor_with_tally(tmp_path: Path) -> None:
    def policy(request: LLMRequest) -> str:
        options = options_from_prompt(request.messages[-1].content)
        if request.caller_role.endswith(".grand_secretary_1") and len(options) > 1:
            # Dissent with the last option, supplying a legal grid target when
            # the option needs one (taxi: any of position+1..6 modulo 40).
            selected = options[-1]
            payload: dict[str, Any] = {"option": selected["option_id"]}
            if "target" in selected["response_format"]["selected_option"]:
                match = re.search(r"位置：格子 (\d+)", request.messages[-1].content)
                position = int(match.group(1)) if match else 0
                payload["target"] = (position + 1) % 40
            return json.dumps(
                {"selected_option": payload, "reason": "大学士一坚持异议。"},
                ensure_ascii=False,
            )
        return _choice(request)

    artifacts, captured = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    decisions = _court_decisions(run_directory)
    split_decisions = [
        record
        for record in decisions
        if len(record["court_trace"]["calls"]) == 7  # drafts + redrafts + emperor
    ]
    assert split_decisions
    for record in split_decisions:
        calls = record["court_trace"]["calls"]
        assert [call["role"] for call in calls] == list(_OFFICERS) * 2 + ["emperor"]
        assert [call["phase"] for call in calls[:3]] == ["first"] * 3
        assert [call["phase"] for call in calls[3:6]] == ["redraft"] * 3
        assert not any(call["content_type"] == "advice" for call in calls)
    assert any(
        request.caller_role == "ming_abl.emperor"
        and "加权多数：" in request.messages[-1].content
        and "得 2.5 票（首辅、大学士二）" in request.messages[-1].content
        and '"decision_maker":"grand_secretary_1","content_type":"draft"'
        in request.messages[-1].content
        for request in captured
    )
    verify_run(run_directory)


def test_ming_ablation_member_connection_exhaustion_falls_back_within_runner(
    tmp_path: Path,
) -> None:
    def policy(request: LLMRequest) -> str:
        if request.caller_role.endswith(".grand_secretary_1"):
            raise LLMConnectionError("大学士一持续不可用")
        return _choice(request)

    artifacts, captured = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    decision = _court_decisions(run_directory)[0]
    assert decision["connection_retries"] == 2
    secretary_calls = [
        call for call in decision["court_trace"]["calls"] if call["role"] == "grand_secretary_1"
    ]
    assert [call["outcome"] for call in secretary_calls] == [
        "connection_error",
        "connection_error",
        "connection_error",
        "connection_fallback",
    ]
    assert any(
        request.caller_role == "ming_abl.emperor"
        and "大学士一重连次数耗尽，无法做出有效回复。" in request.messages[-1].content
        for request in captured
    )
    verify_run(run_directory)
