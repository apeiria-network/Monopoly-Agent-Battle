"""Integration tests for credential-free mock-LLM baseline games."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from monopoly_agent_battle.agents.baseline import BaselineAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.prompts import options_from_prompt
from monopoly_agent_battle.decision.runner import (
    DispatchController,
    RawDecisionController,
    run_decision_game,
)
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.replay import verify_run
from monopoly_agent_battle.llm.mock_client import MockLLMClient, ResponsePolicy
from monopoly_agent_battle.llm.protocol import LLMConnectionError, LLMRequest
from monopoly_agent_battle.llm.recording_client import RecordingLLMClient
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


def make_config(output_directory: Path) -> GameConfig:
    players = tuple(
        PlayerConfig(player_id=pid, seat=seat, model_profile="mock")
        for seat, pid in enumerate(("a", "b", "c", "d"), start=1)
    )
    return GameConfig(
        game_id="llm-run-game",
        experiment_id="llm-run",
        seed=5,
        players=players,
        max_complete_rounds=2,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        model_profiles={"mock": ModelProfile(provider="mock", model="mock-baseline-v1")},
        output_directory=output_directory,
    )


def _dispatch(
    config: GameConfig,
    artifacts: RunArtifacts,
    policy: ResponsePolicy | None = None,
) -> tuple[DispatchController, dict[str, AgentConversation]]:
    controllers: dict[str, RawDecisionController] = {}
    conversations: dict[str, AgentConversation] = {}
    for player in config.players:
        assert player.model_profile is not None
        client = RecordingLLMClient(MockLLMClient(policy=policy, seed=0), artifacts)
        conversation = AgentConversation(
            agent_id=player.player_id, window_turns=config.window_turns
        )
        conversations[player.player_id] = conversation
        controllers[player.player_id] = BaselineAgent(
            player_id=player.player_id,
            client=client,
            profile=config.model_profiles[player.model_profile],
            conversation=conversation,
        )
    return DispatchController(controllers), conversations


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _result_document(run_directory: Path) -> dict[str, Any]:
    return json.loads((run_directory / "result.json").read_text(encoding="utf-8"))


def test_mock_baseline_completes_full_game_with_audit(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    controller, conversations = _dispatch(config, artifacts)

    result = run_decision_game(
        GameEngine(config), controller, artifacts, conversations=conversations
    )

    assert result.status == "completed"
    decisions = _records(artifacts.run_directory / "decisions.jsonl")
    llm_calls = _records(artifacts.run_directory / "llm_calls.jsonl")
    result_document = _result_document(artifacts.run_directory)
    assert decisions
    assert len(llm_calls) >= len(decisions)
    assert result_document["llm_calls"] == len(llm_calls)
    assert result_document["reconnect_events"] == 0
    assert result_document["validity_status"] == "valid"
    assert all("caller_role" in record and "model" in record for record in llm_calls)
    verify_run(artifacts.run_directory)


def test_reconnect_rate_over_threshold_marks_game_invalid(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)

    def disconnect_policy(_request: LLMRequest) -> str:
        raise LLMConnectionError("service unavailable")

    controller, conversations = _dispatch(config, artifacts, disconnect_policy)
    run_decision_game(GameEngine(config), controller, artifacts, conversations=conversations)

    result_document = _result_document(artifacts.run_directory)
    assert result_document["reconnect_events"] > 0
    assert result_document["validity_status"] == "invalid"
    assert (artifacts.run_directory / "llm_calls.jsonl").exists()


def test_invalid_output_retries_with_feedback_then_executes(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    call_counts: list[int] = []

    def flaky_policy(request: LLMRequest) -> str:
        call_counts.append(len(call_counts))
        if len(call_counts) == 1:
            return '{"selected_option": {"option": "not-a-legal-option"}, "reason": "x"}'
        # Extract candidate list from the trailing user message (segments 5-10).
        trailing_user = request.messages[-1].content
        options = options_from_prompt(trailing_user)
        option_id = options[0]["option_id"]
        return json.dumps(
            {"selected_option": {"option": option_id}, "reason": "重试后选择默认操作。"},
            ensure_ascii=False,
        )

    controller, conversations = _dispatch(config, artifacts, flaky_policy)
    run_decision_game(GameEngine(config), controller, artifacts, conversations=conversations)

    decisions = _records(artifacts.run_directory / "decisions.jsonl")
    llm_calls = _records(artifacts.run_directory / "llm_calls.jsonl")
    first = decisions[0]
    assert first["validation_retries"] == 1
    assert first["fallback"] is False
    assert first["validation_errors"] == ["selected_option is not a legal candidate"]
    assert len(llm_calls) == len(decisions) + 1
    assert _result_document(artifacts.run_directory)["llm_calls"] == len(llm_calls)
