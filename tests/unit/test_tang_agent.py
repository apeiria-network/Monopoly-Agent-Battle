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


def request(tmp_path: Path) -> DecisionRequest:
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
    return build_decision_request(engine, sequence=1)


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


def test_tang_three_disagree_has_no_fourth_round(tmp_path: Path) -> None:
    req = request(tmp_path)
    agent, clients = make(req, tmp_path, [review("disagree")] * 3)
    agent(req)
    assert [len(clients[role].requests) for role in ("zhongshu", "menxia", "emperor")] == [3, 3, 1]
    emperor_prompt = "\n".join(
        message.content for message in clients["emperor"].requests[0].messages
    )
    assert "第3轮中书省草案" in emperor_prompt
    assert "第4轮" not in emperor_prompt


def test_tang_menxia_non_object_json_retries_and_falls_back(tmp_path: Path) -> None:
    req = request(tmp_path)
    invalid = ["[]", '"invalid"', "123", "null"]
    for raw in invalid:
        agent, clients = make(req, tmp_path, [raw] * 9)
        result = json.loads(agent(req))
        assert result["reason"] == "终裁"
        assert len(clients["menxia"].requests) == 9


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
