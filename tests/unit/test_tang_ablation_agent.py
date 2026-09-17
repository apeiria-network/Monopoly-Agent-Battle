from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from monopoly_agent_battle.agents.tang_ablation import TangAblationCourtAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import (
    LLMCallError,
    LLMConnectionError,
    LLMRequest,
    LLMResponse,
    UsageMetrics,
)
from monopoly_agent_battle.performance.tracker import evidence_from_trace


class Stub:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(self.responses.pop(0), UsageMetrics(1, 1), request.model)


class Flaky:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResponse(str(outcome), UsageMetrics(1, 1), request.model)


def request(tmp_path: Path, sequence: int = 1) -> DecisionRequest:
    config = GameConfig(
        game_id="tang-abl-unit",
        experiment_id="tang-abl-unit",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    return build_decision_request(engine, sequence=sequence)


def choice(req: DecisionRequest, reason: str = "意见") -> str:
    return json.dumps(
        {"selected_option": {"option": req.options[0].option_id}, "reason": reason},
        ensure_ascii=False,
    )


def review(verdict: str) -> str:
    return json.dumps(
        {"reason": "审核意见", "selected_option": {"option": verdict}}, ensure_ascii=False
    )


def _build(
    clients: dict[str, Any],
    *,
    max_connection_retries: int = 2,
) -> TangAblationCourtAgent:
    profiles = {role: ModelProfile(provider="mock", model=f"{role}-model") for role in clients}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in clients
    }
    return TangAblationCourtAgent(
        player_id="a",
        shangshu_client=clients["shangshu"],
        shangshu_profile=profiles["shangshu"],
        zhongshu_client=clients["zhongshu"],
        zhongshu_profile=profiles["zhongshu"],
        menxia_client=clients["menxia"],
        menxia_profile=profiles["menxia"],
        emperor_client=clients["emperor"],
        emperor_profile=profiles["emperor"],
        conversations=conversations,
        max_connection_retries=max_connection_retries,
    )


def make(
    req: DecisionRequest, tmp_path: Path, reviews: list[str]
) -> tuple[TangAblationCourtAgent, dict[str, Stub]]:
    clients = {
        "shangshu": Stub(["全局信息摘要"]),
        "zhongshu": Stub([choice(req, "草案1")]),
        "menxia": Stub(reviews),
        "emperor": Stub([choice(req, "终裁")]),
    }
    return _build(clients), clients


def test_ablation_single_round_and_call_order(tmp_path: Path) -> None:
    req = request(tmp_path)
    agent, clients = make(req, tmp_path, [review("agree")])
    assert json.loads(agent(req))["reason"] == "终裁"
    assert [
        len(clients[role].requests) for role in ("shangshu", "zhongshu", "menxia", "emperor")
    ] == [1, 1, 1, 1]
    outcomes = [(call["role"], call["outcome"]) for call in agent.court_calls()]
    assert outcomes == [
        ("shangshu", "success"),
        ("zhongshu", "success"),
        ("menxia", "success"),
        ("emperor", "success"),
    ]


def test_ablation_disagree_still_goes_to_emperor_without_redraft(tmp_path: Path) -> None:
    req = request(tmp_path)
    agent, clients = make(req, tmp_path, [review("disagree")])
    assert json.loads(agent(req))["reason"] == "终裁"
    # 门下省反对不再触发退回重拟：中书省只起草一次。
    assert [
        len(clients[role].requests) for role in ("shangshu", "zhongshu", "menxia", "emperor")
    ] == [1, 1, 1, 1]
    emperor_prompt = "\n".join(
        message.content for message in clients["emperor"].requests[0].messages
    )
    assert emperor_prompt.count('"decision_maker": "zhongshu"') == 1
    assert emperor_prompt.count('"decision_maker": "menxia"') == 1
    assert '"option": "disagree"' in emperor_prompt
    assert "第2轮" not in emperor_prompt


def test_ablation_menxia_accepts_code_fenced_review(tmp_path: Path) -> None:
    req = request(tmp_path)
    fenced_review = f"```json\n{review('agree')}\n```"
    agent, clients = make(req, tmp_path, [fenced_review])
    assert json.loads(agent(req))["reason"] == "终裁"
    assert [len(clients[role].requests) for role in ("zhongshu", "menxia", "emperor")] == [1, 1, 1]


def test_ablation_menxia_call_error_exhaustion_advisory_fallback(tmp_path: Path) -> None:
    req = request(tmp_path)
    clients: dict[str, Any] = {
        "shangshu": Stub(["全局信息摘要"]),
        "zhongshu": Stub([choice(req, "草案1")]),
        "menxia": Flaky(
            [
                LLMCallError("Kimi endpoint returned HTTP 400"),
                LLMCallError("Kimi endpoint returned HTTP 400"),
                LLMCallError("Kimi endpoint returned HTTP 400"),
            ]
        ),
        "emperor": Stub([choice(req, "终裁")]),
    }
    agent = _build(clients)

    with pytest.raises(LLMCallError):
        agent(req)
    with pytest.raises(LLMCallError):
        agent(req)
    result = agent(req)

    assert json.loads(result)["reason"] == "终裁"
    assert len(clients["zhongshu"].requests) == 1
    assert len(clients["menxia"].requests) == 3
    assert len(clients["emperor"].requests) == 1
    emperor_text = "\n".join(message.content for message in clients["emperor"].requests[0].messages)
    assert "门下省重连次数耗尽，无法做出有效回复，审核意见仅供参考。" in emperor_text
    outcomes = [(call["role"], call["outcome"]) for call in agent.court_calls()]
    assert outcomes.count(("menxia", "call_error")) == 3
    assert ("menxia", "connection_fallback") in outcomes
    assert ("emperor", "success") in outcomes


def test_ablation_zhongshu_connection_exhaustion_falls_back_to_default_draft(
    tmp_path: Path,
) -> None:
    req = request(tmp_path)
    clients: dict[str, Any] = {
        "shangshu": Stub(["全局信息摘要"]),
        "zhongshu": Flaky(
            [
                LLMConnectionError("zhongshu down"),
                LLMConnectionError("zhongshu down"),
                LLMConnectionError("zhongshu down"),
            ]
        ),
        "menxia": Stub([review("agree")]),
        "emperor": Stub([choice(req, "终裁")]),
    }
    agent = _build(clients)

    with pytest.raises(LLMConnectionError):
        agent(req)
    with pytest.raises(LLMConnectionError):
        agent(req)
    result = agent(req)

    assert json.loads(result)["reason"] == "终裁"
    assert len(clients["zhongshu"].requests) == 3
    assert len(clients["menxia"].requests) == 1
    assert len(clients["emperor"].requests) == 1
    menxia_text = "\n".join(message.content for message in clients["menxia"].requests[0].messages)
    assert "中书省重连次数耗尽，无法做出有效回复，采用默认合法草案。" in menxia_text
    outcomes = [(call["role"], call["outcome"]) for call in agent.court_calls()]
    assert outcomes.count(("zhongshu", "connection_error")) == 3
    assert ("zhongshu", "connection_fallback") in outcomes
    assert ("emperor", "success") in outcomes


def test_ablation_menxia_non_object_json_retries_and_falls_back(tmp_path: Path) -> None:
    req = request(tmp_path)
    for raw in ["[]", '"invalid"', "123", "null"]:
        agent, clients = make(req, tmp_path, [raw] * 9)
        assert json.loads(agent(req))["reason"] == "终裁"
        # 驳回已移除，仅一轮审核：1 次初始 + 2 次校验重试 = 3 次调用。
        assert len(clients["menxia"].requests) == 3
        emperor_text = "\n".join(
            message.content for message in clients["emperor"].requests[-1].messages
        )
        assert "门下省多次审核回复非法，无法做出有效回复，审核意见仅供参考。" in emperor_text


def test_ablation_previous_decision_replays_as_assistant(tmp_path: Path) -> None:
    first = request(tmp_path, sequence=1)
    second = request(tmp_path, sequence=2)
    clients = {
        "shangshu": Stub(["决策1摘要", "决策2摘要"]),
        "zhongshu": Stub([choice(first, "草案1"), choice(second, "新草案")]),
        "menxia": Stub([review("disagree"), review("agree")]),
        "emperor": Stub([choice(first, "终裁1"), choice(second, "终裁2")]),
    }
    agent = _build(clients)
    first_emperor_reply = agent(first)
    agent.record_final_decision(first, first_emperor_reply)
    agent(second)
    zhongshu_assistants = [
        m.content for m in clients["zhongshu"].requests[-1].messages if m.role == "assistant"
    ]
    menxia_assistants = [
        m.content for m in clients["menxia"].requests[-1].messages if m.role == "assistant"
    ]
    assert any("草案1" in content for content in zhongshu_assistants)
    assert any("审核意见" in content and "disagree" in content for content in menxia_assistants)

    zhongshu_history = "\n".join(m.content for m in clients["zhongshu"].requests[-1].messages)
    menxia_history = "\n".join(m.content for m in clients["menxia"].requests[-1].messages)
    assert '"decision_maker":"emperor"' in zhongshu_history
    assert '"content_type":"final_decision"' in zhongshu_history
    assert '"decision_maker":"emperor"' in menxia_history
    assert zhongshu_history.count('"decision_maker":"shangshu"') == 2
    assert menxia_history.count('"decision_maker":"shangshu"') == 2


def test_ablation_record_final_decision_is_idempotent(tmp_path: Path) -> None:
    req = request(tmp_path)
    agent, _ = make(req, tmp_path, [review("disagree")])
    agent(req)
    reply = choice(req, "持久化")
    agent.record_final_decision(req, reply)
    agent.record_final_decision(req, reply)
    for role in ("zhongshu", "menxia"):
        conversation = agent.role_conversations[role]
        entries = [
            entry
            for turn in (*conversation.completed_turns, conversation.current_turn)
            if turn
            for entry in turn.entries
        ]
        assert (
            sum(getattr(entry, "content_type", None) == "final_decision" for entry in entries) == 1
        )


def test_ablation_shangshu_summary_delivered_after_question(tmp_path: Path) -> None:
    req = request(tmp_path)
    agent, clients = make(req, tmp_path, [review("agree")])
    agent(req)
    for role in ("zhongshu", "menxia", "emperor"):
        history = "\n".join(m.content for m in clients[role].requests[0].messages)
        assert '"decision_maker":"shangshu"' in history
        assert '"content_type":"summary"' in history
        assert "全局信息摘要" in history
        assert history.index("## 决策") < history.index('"decision_maker":"shangshu"')
        assert history.index('"decision_maker":"shangshu"') < history.index("## 当前决策")


def test_ablation_shangshu_called_once_across_role_retries(tmp_path: Path) -> None:
    req = request(tmp_path)
    clients = {
        "shangshu": Stub(["全局信息摘要"]),
        "zhongshu": Stub([choice(req, "草案1")]),
        "menxia": Stub(["非法审核", review("agree")]),
        "emperor": Stub([choice(req, "终裁1"), choice(req, "终裁2")]),
    }
    agent = _build(clients)
    agent(req)
    agent(req, feedback="Error: 终裁回复非法")
    assert len(clients["shangshu"].requests) == 1
    assert len(clients["menxia"].requests) == 2
    assert len(clients["emperor"].requests) == 2


def test_ablation_shangshu_connection_exhaustion_uses_fallback_summary(tmp_path: Path) -> None:
    req = request(tmp_path)
    shangshu = Flaky([LLMConnectionError("尚书省不可用")] * 3)
    clients = {
        "shangshu": shangshu,
        "zhongshu": Stub([choice(req, "草案1")]),
        "menxia": Stub([review("agree")]),
        "emperor": Stub([choice(req, "终裁")]),
    }
    agent = _build(clients, max_connection_retries=1)
    with pytest.raises(ConnectionError):
        agent(req)
    assert json.loads(agent(req))["reason"] == "终裁"
    assert len(shangshu.requests) == 2
    history = "\n".join(m.content for m in clients["emperor"].requests[0].messages)
    assert "尚书省重连次数耗尽，无法做出有效回复" in history
    outcomes = [call["outcome"] for call in agent.court_calls() if call["role"] == "shangshu"]
    assert outcomes == ["connection_error", "connection_error", "summary_fallback"]


def test_ablation_shangshu_excluded_from_performance_evidence(tmp_path: Path) -> None:
    req = request(tmp_path)
    trace: dict[str, object] = {
        "court": "tang_ablation",
        "calls": [
            {
                "role": "shangshu",
                "caller_role": "a.shangshu",
                "outcome": "success",
                "content": "全局信息摘要",
                "decision_maker": "shangshu",
                "content_type": "summary",
            },
            {
                "role": "zhongshu",
                "caller_role": "a.zhongshu",
                "outcome": "success",
                "content": choice(req, "草案"),
                "content_type": "draft",
            },
            {
                "role": "menxia",
                "caller_role": "a.menxia",
                "outcome": "success",
                "content": review("agree"),
                "content_type": "review",
            },
        ],
    }
    evidence = evidence_from_trace(req, trace, req.options[0].option_id, None)
    assert evidence is not None
    assert "shangshu" not in evidence.officer_signatures
    assert set(evidence.officer_signatures) == {"zhongshu"}
