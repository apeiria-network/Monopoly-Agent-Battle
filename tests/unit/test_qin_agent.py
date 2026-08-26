from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from monopoly_agent_battle.agents.qin import QinCourtAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMRequest, LLMResponse, UsageMetrics
from monopoly_agent_battle.performance.random_generator import random_officer_performance


class StubClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            content=self.responses.pop(0),
            usage=UsageMetrics(input_tokens=1, output_tokens=1),
            model=request.model,
        )


def _request(tmp_path: Path) -> DecisionRequest:
    config = GameConfig(
        game_id="qin-unit",
        experiment_id="qin-unit",
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


def _choice(request: DecisionRequest, reason: str = "建议") -> str:
    return json.dumps(
        {
            "selected_option": {"option": request.options[0].option_id},
            "reason": reason,
        },
        ensure_ascii=False,
    )


def _comment() -> str:
    return json.dumps(
        {
            "reason": "综合比较",
            "assessments": [
                {"officer_id": "chancellor", "judgement": "agree", "reason": "理由一"},
                {"officer_id": "grand_marshal", "judgement": "neutral", "reason": "理由二"},
            ],
        },
        ensure_ascii=False,
    )


def _agent(
    request: DecisionRequest,
    clients: dict[str, StubClient],
    performance_generator: Any = random_officer_performance,
) -> QinCourtAgent:
    profiles = {
        role: ModelProfile(provider="mock", model=f"{role}-model")
        for role in ("chancellor", "grand_marshal", "imperial_counsellor", "emperor")
    }
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in profiles
    }
    return QinCourtAgent(
        player_id="a",
        chancellor_client=clients["chancellor"],
        chancellor_profile=profiles["chancellor"],
        grand_marshal_client=clients["grand_marshal"],
        grand_marshal_profile=profiles["grand_marshal"],
        imperial_counsellor_client=clients["imperial_counsellor"],
        imperial_counsellor_profile=profiles["imperial_counsellor"],
        emperor_client=clients["emperor"],
        emperor_profile=profiles["emperor"],
        conversations=conversations,
        performance_generator=performance_generator,
    )


def test_qin_call_order_and_segment_five_visibility(tmp_path: Path) -> None:
    request = _request(tmp_path)
    clients = {
        "chancellor": StubClient([_choice(request, "丞相意见")]),
        "grand_marshal": StubClient([_choice(request, "太尉意见")]),
        "imperial_counsellor": StubClient([_comment()]),
        "emperor": StubClient([_choice(request, "皇帝裁决")]),
    }
    agent = _agent(request, clients)

    result = agent(request)

    assert json.loads(result)["selected_option"]["option"] == request.options[0].option_id
    assert [call["role"] for call in agent.court_calls()] == [
        "chancellor",
        "grand_marshal",
        "imperial_counsellor",
        "emperor",
    ]
    emperor_text = "\n".join(message.content for message in clients["emperor"].requests[0].messages)
    assert '"decision_maker":"chancellor"' in emperor_text
    assert '"decision_maker":"grand_marshal"' in emperor_text
    assert '"content_type":"comment"' in emperor_text
    assert "## 合法候选操作" in emperor_text
    assert "judgement" in emperor_text


def test_qin_counsellor_receives_performance_at_segment_boundary(tmp_path: Path) -> None:
    request = _request(tmp_path)
    clients = {
        "chancellor": StubClient([_choice(request, "丞相意见")]),
        "grand_marshal": StubClient([_choice(request, "太尉意见")]),
        "imperial_counsellor": StubClient([_comment()]),
        "emperor": StubClient([_choice(request, "皇帝裁决")]),
    }

    def performance(_: DecisionRequest) -> str:
        return "## 官员绩效\n最近1个回合中，丞相、太尉的决策较差。"

    agent = _agent(request, clients, performance)

    agent(request)

    counsellor_text = "\n".join(
        message.content for message in clients["imperial_counsellor"].requests[0].messages
    )
    performance_pos = counsellor_text.index("## 官员绩效")
    question_pos = counsellor_text.index("## 当前决策")
    options_pos = counsellor_text.index("## 合法候选操作")
    assert performance_pos < question_pos < options_pos
    assert "最近1个回合中，丞相、太尉的决策较差。" in counsellor_text

    request = _request(tmp_path)
    clients = {
        "chancellor": StubClient([_choice(request)]),
        "grand_marshal": StubClient([_choice(request)]),
        "imperial_counsellor": StubClient(["{}", "[]", "invalid"]),
        "emperor": StubClient([_choice(request)]),
    }
    agent = _agent(request, clients)

    agent(request)

    assert len(clients["imperial_counsellor"].requests) == 3
    trace = agent.court_trace()
    calls = cast(list[dict[str, Any]], trace["calls"])
    assert any(
        call["role"] == "imperial_counsellor" and call["outcome"] == "validation_error"
        for call in calls
    )
    emperor_text = "\n".join(message.content for message in clients["emperor"].requests[0].messages)
    assert "御史大夫多次重试失败，无法回复" in emperor_text


def test_qin_adviser_retries_before_defaulting(tmp_path: Path) -> None:
    request = _request(tmp_path)
    clients = {
        "chancellor": StubClient(["{}", _choice(request, "重试成功")]),
        "grand_marshal": StubClient(["invalid", "still invalid", "bad"]),
        "imperial_counsellor": StubClient([_comment()]),
        "emperor": StubClient([_choice(request)]),
    }
    agent = _agent(request, clients)

    agent(request)

    assert len(clients["chancellor"].requests) == 2
    assert len(clients["grand_marshal"].requests) == 3
    counsellor_text = "\n".join(
        message.content for message in clients["imperial_counsellor"].requests[0].messages
    )
    assert "重试成功" in counsellor_text


def test_qin_counsellor_non_object_assessment_triggers_fallback(tmp_path: Path) -> None:
    request = _request(tmp_path)
    bad = json.dumps({"assessments": [1, {}]}, ensure_ascii=False)
    clients = {
        "chancellor": StubClient([_choice(request)]),
        "grand_marshal": StubClient([_choice(request)]),
        "imperial_counsellor": StubClient([bad, bad, bad]),
        "emperor": StubClient([_choice(request)]),
    }
    agent = _agent(request, clients)

    # Must not raise AttributeError; malformed structure retries then falls back.
    agent(request)

    assert len(clients["imperial_counsellor"].requests) == 3
    emperor_text = "\n".join(message.content for message in clients["emperor"].requests[0].messages)
    assert "御史大夫多次重试失败，无法回复" in emperor_text


def test_qin_current_decision_hides_emperor_final_from_officers(tmp_path: Path) -> None:
    request = _request(tmp_path)
    clients = {
        "chancellor": StubClient([_choice(request, "丞相意见")]),
        "grand_marshal": StubClient([_choice(request, "太尉意见")]),
        "imperial_counsellor": StubClient([_comment()]),
        "emperor": StubClient([_choice(request, "皇帝裁决")]),
    }
    agent = _agent(request, clients)

    agent(request)

    # During the current decision no role sees the emperor's not-yet-public final decision.
    for role in ("chancellor", "grand_marshal", "imperial_counsellor"):
        text = "\n".join(message.content for message in clients[role].requests[0].messages)
        assert '"content_type":"final_decision"' not in text


def test_qin_record_final_decision_broadcasts_once(tmp_path: Path) -> None:
    request = _request(tmp_path)
    clients = {
        "chancellor": StubClient([_choice(request, "丞相意见")]),
        "grand_marshal": StubClient([_choice(request, "太尉意见")]),
        "imperial_counsellor": StubClient([_comment()]),
        "emperor": StubClient([_choice(request, "皇帝裁决")]),
    }
    agent = _agent(request, clients)
    agent(request)

    reply = _choice(request, "最终执行")
    # Runner is the single source of the final decision; a repeat is idempotent.
    agent.record_final_decision(request, reply)
    agent.record_final_decision(request, reply)

    for role in ("chancellor", "grand_marshal", "imperial_counsellor"):
        conversation = agent._conversations[role]  # type: ignore[attr-defined]
        finals = [
            entry
            for turn in (*conversation.completed_turns, conversation.current_turn)
            if turn is not None
            for entry in turn.entries
            if getattr(entry, "content_type", None) == "final_decision"
        ]
        assert len(finals) == 1
