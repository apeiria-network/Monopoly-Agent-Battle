from __future__ import annotations

import json
from pathlib import Path

from monopoly_agent_battle.agents.tang import TangCourtAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMRequest, LLMResponse, UsageMetrics


class Stub:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(self.responses.pop(0), UsageMetrics(1, 1), request.model)


def request(tmp_path: Path, sequence: int = 1) -> DecisionRequest:
    config = GameConfig(
        game_id="tang-unit",
        experiment_id="tang-unit",
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


def make(
    req: DecisionRequest, tmp_path: Path, reviews: list[str]
) -> tuple[TangCourtAgent, dict[str, Stub]]:
    clients = {
        "zhongshu": Stub([choice(req, f"草案{i}") for i in range(3)]),
        "menxia": Stub(reviews),
        "emperor": Stub([choice(req, "终裁")]),
    }
    profiles = {role: ModelProfile(provider="mock", model=f"{role}-model") for role in clients}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in clients
    }
    return TangCourtAgent(
        player_id="a",
        zhongshu_client=clients["zhongshu"],
        zhongshu_profile=profiles["zhongshu"],
        menxia_client=clients["menxia"],
        menxia_profile=profiles["menxia"],
        emperor_client=clients["emperor"],
        emperor_profile=profiles["emperor"],
        conversations=conversations,
    ), clients


def test_tang_agree_order_and_review_contract(tmp_path: Path) -> None:
    req = request(tmp_path)
    agent, clients = make(req, tmp_path, [review("agree")])
    assert json.loads(agent(req))["reason"] == "终裁"
    assert [len(clients[role].requests) for role in ("zhongshu", "menxia", "emperor")] == [1, 1, 1]
    assert "agree" in clients["menxia"].requests[0].messages[-1].content


def test_tang_menxia_accepts_code_fenced_review(tmp_path: Path) -> None:
    req = request(tmp_path)
    fenced_review = f"```json\n{review('agree')}\n```"
    agent, clients = make(req, tmp_path, [fenced_review])
    assert json.loads(agent(req))["reason"] == "终裁"
    # A fenced-but-valid review is accepted on the first call: no extra round.
    assert [len(clients[role].requests) for role in ("zhongshu", "menxia", "emperor")] == [1, 1, 1]


def test_tang_three_disagree_has_no_fourth_round(tmp_path: Path) -> None:
    req = request(tmp_path)
    agent, clients = make(req, tmp_path, [review("disagree")] * 3)
    agent(req)
    assert [len(clients[role].requests) for role in ("zhongshu", "menxia", "emperor")] == [3, 3, 1]
    emperor_prompt = "\n".join(
        message.content for message in clients["emperor"].requests[0].messages
    )
    assert emperor_prompt.count('"decision_maker": "zhongshu"') == 3
    assert emperor_prompt.count('"content_type": "draft"') == 3
    assert "第3轮中书省草案" not in emperor_prompt
    assert "第4轮" not in emperor_prompt


def test_tang_menxia_non_object_json_retries_and_falls_back(tmp_path: Path) -> None:
    req = request(tmp_path)
    for raw in ["[]", '"invalid"', "123", "null"]:
        agent, clients = make(req, tmp_path, [raw] * 9)
        assert json.loads(agent(req))["reason"] == "终裁"
        assert len(clients["menxia"].requests) == 9


def test_tang_previous_decision_second_round_replays_as_assistant(tmp_path: Path) -> None:
    first = request(tmp_path, sequence=1)
    second = request(tmp_path, sequence=2)
    clients = {
        "zhongshu": Stub(
            [choice(first, "草案1"), choice(first, "草案2"), choice(second, "新草案")]
        ),
        "menxia": Stub([review("disagree"), review("agree"), review("agree")]),
        "emperor": Stub([choice(first, "终裁1"), choice(second, "终裁2")]),
    }
    profiles = {role: ModelProfile(provider="mock", model=f"{role}-model") for role in clients}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in clients
    }
    agent = TangCourtAgent(
        player_id="a",
        zhongshu_client=clients["zhongshu"],
        zhongshu_profile=profiles["zhongshu"],
        menxia_client=clients["menxia"],
        menxia_profile=profiles["menxia"],
        emperor_client=clients["emperor"],
        emperor_profile=profiles["emperor"],
        conversations=conversations,
    )
    first_emperor_reply = agent(first)
    agent.record_final_decision(first, first_emperor_reply)
    agent(second)
    zhongshu_assistants = [
        m.content for m in clients["zhongshu"].requests[-1].messages if m.role == "assistant"
    ]
    menxia_assistants = [
        m.content for m in clients["menxia"].requests[-1].messages if m.role == "assistant"
    ]
    assert any("草案2" in content for content in zhongshu_assistants)
    assert any("审核意见" in content and "agree" in content for content in menxia_assistants)

    zhongshu_history = "\n".join(m.content for m in clients["zhongshu"].requests[-1].messages)
    menxia_history = "\n".join(m.content for m in clients["menxia"].requests[-1].messages)
    assert '"decision_maker":"emperor"' in zhongshu_history
    assert '"content_type":"final_decision"' in zhongshu_history
    assert '"decision_maker":"emperor"' in menxia_history
    assert '"content_type":"final_decision"' in menxia_history


def test_tang_record_final_decision_is_idempotent(tmp_path: Path) -> None:
    req = request(tmp_path)
    agent, _ = make(req, tmp_path, [review("agree")])
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
