"""Unit tests for the Stage 4C single-model baseline agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from monopoly_agent_battle.agents.baseline import BaselineAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import (
    LLMConnectionError,
    LLMRequest,
    LLMResponse,
    UsageMetrics,
)


class StubClient:
    """Record every request and return a fixed response."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(content=self.content, usage=UsageMetrics(1, 1), model=request.model)


def _make_request_and_engine(tmp_path: Path) -> tuple[GameEngine, DecisionRequest]:
    """Return a real (engine, decision request) pair anchored on tmp_path."""
    config = GameConfig(
        game_id="baseline-test",
        experiment_id="unit",
        seed=1,
        players=(
            PlayerConfig(player_id="a", seat=1),
            PlayerConfig(player_id="b", seat=2),
        ),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    request = build_decision_request(engine, sequence=1)
    return engine, request


def _agent(client: StubClient, profile: ModelProfile) -> tuple[BaselineAgent, AgentConversation]:
    conversation = AgentConversation(agent_id="a", window_turns=1)
    agent = BaselineAgent(
        player_id="a",
        client=client,
        profile=profile,
        conversation=conversation,
    )
    return agent, conversation


def test_baseline_agent_returns_client_content_and_builds_multi_message_request(
    tmp_path: Path,
) -> None:
    client = StubClient('{"selected_option": {"option": "end_turn"}, "reason": "r"}')
    profile = ModelProfile(
        provider="mock", model="mock-baseline-v1", temperature=0.5, max_tokens=100
    )
    _engine, request = _make_request_and_engine(tmp_path)

    agent, _conv = _agent(client, profile)
    content = agent(request)

    assert content == client.content
    llm_request = client.requests[0]
    assert llm_request.caller_role == "a"
    assert llm_request.model == "mock-baseline-v1"
    assert llm_request.temperature == 0.5
    assert llm_request.max_tokens == 100
    # First decision, no history → system + user (segments 1+2 + segments 5-10).
    roles = [message.role for message in llm_request.messages]
    assert roles == ["system", "user"]
    # Segment 2 (game rules) must be in the system message.
    assert "游戏规则" in llm_request.messages[0].content
    # Segment 8+9+10 belong to the trailing user message.
    assert "合法候选操作" in llm_request.messages[-1].content


def test_baseline_agent_replays_error_entries_from_conversation(tmp_path: Path) -> None:
    client = StubClient("x")
    _engine, request = _make_request_and_engine(tmp_path)
    agent, conversation = _agent(client, ModelProfile(provider="mock", model="m"))
    conversation.start_turn(1, segment3_budget_tokens=10_000)
    conversation.append_error(
        decision_id="d1",
        question_summary="## 当前决策\n你需要选一个合法选项。",
        bad_reply='{"selected_option":"bad"}',
        feedback_text="Error: 决策回复必须是一个JSON",
    )

    agent(request)

    messages = client.requests[0].messages
    roles = [message.role for message in messages]
    assert "assistant" in roles
    trailing_user = messages[-1]
    assert trailing_user.role == "user"
    assert "Error: 决策回复必须是一个JSON" in trailing_user.content


def test_baseline_agent_propagates_connection_errors(tmp_path: Path) -> None:
    class BrokenClient(StubClient):
        def complete(self, request: LLMRequest) -> LLMResponse:
            raise LLMConnectionError("down")

    _engine, request = _make_request_and_engine(tmp_path)
    agent, _conv = _agent(BrokenClient("x"), ModelProfile(provider="mock", model="m"))
    with pytest.raises(LLMConnectionError):
        agent(request)
