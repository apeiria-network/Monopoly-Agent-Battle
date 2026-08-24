from __future__ import annotations

import json
from pathlib import Path

import pytest

from monopoly_agent_battle.agents.shang import ShangCourtAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.prompts import options_from_prompt
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
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return LLMResponse(
            content=response,
            usage=UsageMetrics(input_tokens=1, output_tokens=1),
            model=request.model,
        )


def _request(tmp_path: Path) -> DecisionRequest:
    config = GameConfig(
        game_id="shang-unit",
        experiment_id="shang-unit",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    return build_decision_request(engine, sequence=1)


def _valid_response(request: DecisionRequest) -> str:
    option_id = request.options[0].option_id
    return json.dumps(
        {"selected_option": {"option": option_id}, "reason": "皇帝作出选择。"},
        ensure_ascii=False,
    )


def _agent(
    request: DecisionRequest,
    priest: StubClient,
    emperor: StubClient,
) -> ShangCourtAgent:
    return ShangCourtAgent(
        player_id=request.player_id,
        great_priest_client=priest,
        great_priest_profile=ModelProfile(provider="mock", model="priest-model"),
        emperor_client=emperor,
        emperor_profile=ModelProfile(provider="mock", model="emperor-model"),
        emperor_conversation=AgentConversation(agent_id=request.player_id, window_turns=1),
    )


def test_priest_receives_only_current_question_and_emperor_receives_oracle(tmp_path: Path) -> None:
    request = _request(tmp_path)
    oracle = "龟甲示现：当慎察时机。"
    expected = _valid_response(request)
    priest = StubClient([oracle])
    emperor = StubClient([expected])
    agent = _agent(request, priest, emperor)

    assert agent(request) == expected

    priest_request = priest.requests[0]
    assert priest_request.caller_role == "a.great_priest"
    assert priest_request.model == "priest-model"
    assert [message.role for message in priest_request.messages] == ["system", "user"]
    priest_text = "\n".join(message.content for message in priest_request.messages)
    assert "暂定技术提示词" in priest_text
    assert "## 当前决策" in priest_request.messages[-1].content
    assert "## 合法候选操作" not in priest_text
    assert '"option_id"' not in priest_text
    assert "神谕" not in priest_request.messages[-1].content

    emperor_request = emperor.requests[0]
    assert emperor_request.caller_role == "a.emperor"
    emperor_text = "\n".join(message.content for message in emperor_request.messages)
    assert oracle in emperor_text
    assert "## 合法候选操作" in emperor_text
    assert options_from_prompt(emperor_request.messages[-1].content)
    assert "RNG" not in emperor_text
    assert "runtime" not in emperor_text

    trace = agent.court_trace()
    calls = agent.court_calls()
    assert trace["decision_id"] == request.decision_id
    assert [call["role"] for call in calls] == ["great_priest", "emperor"]
    assert calls[0]["decision_maker"] == "great_priest"
    assert calls[0]["content_type"] == "oracle"
    assert calls[1]["decision_maker"] == "emperor"
    assert calls[1]["content_type"] == "final_decision"
    assert agent.conversation.current_turn is None


def test_priest_connection_failure_retries_priest_stage(tmp_path: Path) -> None:
    request = _request(tmp_path)
    expected = _valid_response(request)
    priest = StubClient([LLMConnectionError("priest down"), "神谕"])
    emperor = StubClient([expected])
    agent = _agent(request, priest, emperor)

    with pytest.raises(LLMConnectionError):
        agent(request)
    assert agent.last_llm_call_count == 1
    assert len(emperor.requests) == 0

    response = agent(request)
    assert response == expected
    assert len(priest.requests) == 2
    assert len(emperor.requests) == 1
    calls = agent.court_calls()
    assert [call["outcome"] for call in calls] == [
        "connection_error",
        "success",
        "success",
    ]


def test_emperor_connection_failure_does_not_repeat_priest(tmp_path: Path) -> None:
    request = _request(tmp_path)
    priest = StubClient(["神谕"])
    emperor = StubClient([LLMConnectionError("emperor down"), _valid_response(request)])
    agent = _agent(request, priest, emperor)

    with pytest.raises(LLMConnectionError):
        agent(request)
    assert agent.last_llm_call_count == 2

    agent(request)
    assert len(priest.requests) == 1
    assert len(emperor.requests) == 2
    calls = agent.court_calls()
    assert [call["role"] for call in calls] == [
        "great_priest",
        "emperor",
        "emperor",
    ]


def test_emperor_validation_retry_does_not_repeat_priest(tmp_path: Path) -> None:
    request = _request(tmp_path)
    priest = StubClient(["神谕"])
    emperor = StubClient(
        [
            '{"selected_option":{"option":"illegal"},"reason":"x"}',
            _valid_response(request),
        ]
    )
    agent = _agent(request, priest, emperor)

    first = agent(request)
    second = agent(request)
    assert first != second
    assert len(priest.requests) == 1
    assert len(emperor.requests) == 2
    assert "神谕" in emperor.requests[1].messages[-1].content
    calls = agent.court_calls()
    assert [call["role"] for call in calls] == [
        "great_priest",
        "emperor",
        "emperor",
    ]
