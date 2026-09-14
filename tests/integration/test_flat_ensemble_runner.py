"""Integration coverage for the flat-ensemble (``flat_ensemble``) controller."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from monopoly_agent_battle.agents.flat_ensemble import FlatEnsembleAgent
from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.config.models import (
    FlatEnsembleRoleProfiles,
    GameConfig,
    ModelProfile,
    PlayerConfig,
)
from monopoly_agent_battle.context.conversation import AgentConversation, DecisionEntry
from monopoly_agent_battle.decision.prompts import options_from_prompt
from monopoly_agent_battle.decision.runner import DispatchController, run_decision_game
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.replay import verify_run
from monopoly_agent_battle.llm.mock_client import MockLLMClient, ResponsePolicy
from monopoly_agent_battle.llm.protocol import LLMConnectionError, LLMRequest
from monopoly_agent_battle.llm.recording_client import RecordingLLMClient
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts

_ROLES = ("member_1", "member_2", "member_3", "leader")
_VOTE_REASON_MARK = "系统加权投票裁定"


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _result(run_directory: Path) -> dict[str, Any]:
    return json.loads((run_directory / "result.json").read_text(encoding="utf-8"))


def _config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="flat-integration",
        experiment_id="flat-integration",
        seed=0,
        players=(
            PlayerConfig(
                player_id="fe",
                seat=1,
                controller_type="flat_ensemble",
                court_role_profiles=FlatEnsembleRoleProfiles(
                    member_1="member",
                    member_2="member",
                    member_3="member",
                    leader="leader",
                ),
            ),
            PlayerConfig(player_id="random", seat=2, controller_type="random_baseline"),
        ),
        max_complete_rounds=2,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        model_profiles={
            "member": ModelProfile(provider="mock", model="mock-flat-member-v1"),
            "leader": ModelProfile(provider="mock", model="mock-flat-leader-v1"),
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
        role: AgentConversation(agent_id=f"fe.{role}", window_turns=config.window_turns)
        for role in _ROLES
    }
    clients = {role: RecordingLLMClient(MockLLMClient(policy), artifacts) for role in _ROLES}
    member_profile = config.model_profiles["member"]
    leader_profile = config.model_profiles["leader"]
    agent = FlatEnsembleAgent(
        player_id="fe",
        member_1_client=clients["member_1"],
        member_1_profile=member_profile,
        member_2_client=clients["member_2"],
        member_2_profile=member_profile,
        member_3_client=clients["member_3"],
        member_3_profile=member_profile,
        leader_client=clients["leader"],
        leader_profile=leader_profile,
        conversations=conversations,
    )
    return (
        DispatchController(
            {
                "fe": agent,
                "random": RandomBaselineController(random.Random(1)),
            }
        ),
        {"fe": conversations},
    )


def _run(
    tmp_path: Path, policy: ResponsePolicy
) -> tuple[RunArtifacts, list[LLMRequest], dict[str, Any]]:
    config = _config(tmp_path)
    artifacts = RunArtifacts.create(config)
    captured: list[LLMRequest] = []

    def capture_policy(request: LLMRequest) -> str:
        captured.append(request)
        return policy(request)

    controller, conversations = _dispatch(config, artifacts, capture_policy)
    run_decision_game(GameEngine(config), controller, artifacts, conversations=conversations)
    return artifacts, captured, conversations


def _decision_entries(conversation: AgentConversation) -> list[str]:
    turns = [*conversation.completed_turns]
    if conversation.current_turn is not None:
        turns.append(conversation.current_turn)
    return [
        entry.assistant_reply
        for turn in turns
        for entry in turn.entries
        if isinstance(entry, DecisionEntry)
    ]


def test_flat_ensemble_runs_full_game_and_records_vote_trace(tmp_path: Path) -> None:
    artifacts, captured, conversations = _run(tmp_path, _valid_choice)
    run_directory = artifacts.run_directory
    decisions = _records(run_directory / "decisions.jsonl")
    llm_calls = _records(run_directory / "llm_calls.jsonl")
    result = _result(run_directory)
    fe_decisions = [record for record in decisions if record["request"]["player_id"] == "fe"]

    assert fe_decisions
    for record in fe_decisions:
        trace = record["court_trace"]
        assert trace["court"] == "flat_ensemble"
        assert [call["role"] for call in trace["calls"]] == [
            "member_1",
            "member_2",
            "member_3",
            "leader",
        ]
        assert {call["outcome"] for call in trace["calls"]} == {"success"}
        vote = trace["vote"]
        assert vote["weights"] == {
            "member_1": 1.0,
            "member_2": 1.0,
            "member_3": 1.0,
            "leader": 1.5,
        }
        assert vote["selected_option"]["option"] == record["validation"]["selected_option"]

    assert [record["call_id"] for record in llm_calls] == list(range(1, len(llm_calls) + 1))
    assert {record["caller_role"] for record in llm_calls} == {
        "fe.member_1",
        "fe.member_2",
        "fe.member_3",
        "fe.leader",
    }
    assert result["llm_calls"] == len(llm_calls)
    assert result["reconnect_events"] == 0
    assert result["llm_fallbacks"] == 0
    assert result["validity_status"] == "valid"
    # Four LLM calls per flat-ensemble decision.
    assert result["llm_calls"] == 4 * len(fe_decisions)

    # No court artifacts leak into any session's baseline-level prompt.
    for request in captured:
        text = "\n".join(message.content for message in request.messages)
        assert '"decision_maker"' not in text
        assert '"oracle"' not in text

    # The system-authored vote summary never enters any session's history: each
    # session keeps only its own replies (the runner's auto-append is deduped).
    for conversation in conversations["fe"].values():
        for reply in _decision_entries(conversation):
            assert _VOTE_REASON_MARK not in reply

    verify_run(run_directory)


def test_member_connection_exhaustion_falls_back_and_participates_in_vote(tmp_path: Path) -> None:
    member_attempts = 0

    def policy(request: LLMRequest) -> str:
        nonlocal member_attempts
        if request.caller_role == "fe.member_1":
            member_attempts += 1
            raise LLMConnectionError("member unavailable")
        return _valid_choice(request)

    artifacts, _, _ = _run(tmp_path, policy)
    run_directory = artifacts.run_directory
    first_fe = next(
        record
        for record in _records(run_directory / "decisions.jsonl")
        if record["request"]["player_id"] == "fe"
    )
    llm_calls = _records(run_directory / "llm_calls.jsonl")
    result = _result(run_directory)

    calls = first_fe["court_trace"]["calls"]
    assert [call["role"] for call in calls] == [
        "member_1",
        "member_1",
        "member_1",
        "member_1",
        "member_2",
        "member_3",
        "leader",
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
    assert "成员一重连次数耗尽，无法做出有效回复。" in calls[3]["content"]
    assert first_fe["connection_retries"] == 2
    assert {record["caller_role"] for record in llm_calls} == {
        "fe.member_1",
        "fe.member_2",
        "fe.member_3",
        "fe.leader",
    }
    assert result["reconnect_events"] >= 2
    assert result["llm_fallbacks"] == 0
    assert result["validity_status"] == "valid"
    verify_run(run_directory)
