"""Unit tests for the single-model baseline agent."""

from __future__ import annotations

import pytest

from monopoly_agent_battle.agents.baseline import BaselineAgent
from monopoly_agent_battle.config.models import ModelProfile
from monopoly_agent_battle.decision.models import DecisionKind, DecisionRequest
from monopoly_agent_battle.llm.protocol import (
    LLMConnectionError,
    LLMMessage,
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


def _make_request() -> DecisionRequest:
    return DecisionRequest(
        decision_id="decision-1",
        game_id="g",
        complete_rounds=0,
        player_id="a",
        phase="asset_management",
        kind=DecisionKind.ASSET_MANAGEMENT,
        question="q",
        visible_state={},
        options=(),
        output_constraints={},
    )


def _agent(client: StubClient, profile: ModelProfile) -> BaselineAgent:
    return BaselineAgent(
        player_id="a",
        client=client,
        profile=profile,
        prompt_renderer=lambda _request: "PROMPT",
    )


def test_baseline_agent_returns_client_content_and_builds_request() -> None:
    client = StubClient('{"selected_option": {"option": "end_turn"}, "reason": "r"}')
    profile = ModelProfile(
        provider="mock", model="mock-baseline-v1", temperature=0.5, max_tokens=100
    )

    content = _agent(client, profile)(_make_request())

    assert content == client.content
    request = client.requests[0]
    assert request.caller_role == "a"
    assert request.model == "mock-baseline-v1"
    assert request.temperature == 0.5
    assert request.max_tokens == 100
    assert request.messages == (LLMMessage(role="user", content="PROMPT"),)


def test_baseline_agent_appends_transient_validation_feedback() -> None:
    client = StubClient("x")
    _agent(client, ModelProfile(provider="mock", model="m"))(
        _make_request(), feedback="response is not valid JSON"
    )
    prompt = client.requests[0].messages[0].content
    assert "## 上次输出反馈" in prompt
    assert "response is not valid JSON" in prompt


def test_baseline_agent_propagates_connection_errors() -> None:
    class BrokenClient(StubClient):
        def complete(self, request: LLMRequest) -> LLMResponse:
            raise LLMConnectionError("down")

    with pytest.raises(LLMConnectionError):
        _agent(BrokenClient("x"), ModelProfile(provider="mock", model="m"))(_make_request())
