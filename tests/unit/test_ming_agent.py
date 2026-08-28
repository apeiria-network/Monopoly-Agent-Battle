from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

from monopoly_agent_battle.agents.ming import MingCourtAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionOption, DecisionRequest
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


def make_request(tmp_path: Path, sequence: int = 1) -> DecisionRequest:
    config = GameConfig(
        game_id="ming-unit",
        experiment_id="ming-unit",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    request = build_decision_request(engine, sequence=sequence)
    if len(request.options) == 1:
        base = request.options[0]
        alternate = DecisionOption(
            option_id="alternate_end_turn",
            command_type=base.command_type,
            parameters=base.parameters,
            title="备用结束回合",
            preview=base.preview,
            response_format=base.response_format,
            is_default=False,
            target=base.target,
        )
        request = replace(request, options=(base, alternate))
    return request


def choice(req: DecisionRequest, option: str | None = None, reason: str = "意见") -> str:
    selected = option or req.options[0].option_id
    return json.dumps(
        {"selected_option": {"option": selected}, "reason": reason}, ensure_ascii=False
    )


def make_agent(req: DecisionRequest) -> tuple[MingCourtAgent, dict[str, Stub]]:
    options = [item.option_id for item in req.options]
    first = [
        choice(req, options[0], "首辅草案"),
        choice(req, options[0], "大学士一草案"),
        choice(req, options[0], "大学士二草案"),
    ]
    clients = {
        "chief": Stub(first[:1] + [choice(req, options[0], "汇总")]),
        "secretary_1": Stub(first[1:2]),
        "secretary_2": Stub(first[2:3]),
        "emperor": Stub([choice(req, options[0], "终裁")]),
    }
    profiles = {role: ModelProfile(provider="mock", model=f"{role}-model") for role in clients}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1)
        for role in (
            "chief_grand_secretary",
            "grand_secretary_1",
            "grand_secretary_2",
            "emperor",
        )
    }
    agent = MingCourtAgent(
        player_id="a",
        chief_client=clients["chief"],
        chief_profile=profiles["chief"],
        secretary_1_client=clients["secretary_1"],
        secretary_1_profile=profiles["secretary_1"],
        secretary_2_client=clients["secretary_2"],
        secretary_2_profile=profiles["secretary_2"],
        emperor_client=clients["emperor"],
        emperor_profile=profiles["emperor"],
        conversations=conversations,
    )
    return agent, clients


def test_ming_unanimous_workflow_and_emperor_visibility(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    agent, clients = make_agent(req)

    reply = agent(req)

    assert json.loads(reply)["reason"] == "终裁"
    assert [
        len(clients[key].requests) for key in ("chief", "secretary_1", "secretary_2", "emperor")
    ] == [2, 1, 1, 1]
    emperor_prompt = "\n".join(
        message.content for message in clients["emperor"].requests[0].messages
    )
    assert '"content_type":"advice"' in emperor_prompt
    assert "当前决策投票结果" not in emperor_prompt


def test_ming_redraft_and_weighted_vote_are_isolated(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    option_ids = [item.option_id for item in req.options]
    alternate = option_ids[1] if len(option_ids) > 1 else option_ids[0]
    clients = {
        "chief": Stub(
            [
                choice(req, option_ids[0]),
                choice(req, option_ids[0], "重拟"),
                choice(req, option_ids[0], "汇总"),
            ]
        ),
        "secretary_1": Stub([choice(req, alternate), choice(req, alternate, "重拟")]),
        "secretary_2": Stub([choice(req, option_ids[0]), choice(req, option_ids[0], "重拟")]),
        "emperor": Stub([choice(req, option_ids[0], "终裁")]),
    }
    profiles = {role: ModelProfile(provider="mock", model=f"{role}-model") for role in clients}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1)
        for role in (
            "chief_grand_secretary",
            "grand_secretary_1",
            "grand_secretary_2",
            "emperor",
        )
    }
    agent = MingCourtAgent(
        player_id="a",
        chief_client=clients["chief"],
        chief_profile=profiles["chief"],
        secretary_1_client=clients["secretary_1"],
        secretary_1_profile=profiles["secretary_1"],
        secretary_2_client=clients["secretary_2"],
        secretary_2_profile=profiles["secretary_2"],
        emperor_client=clients["emperor"],
        emperor_profile=profiles["emperor"],
        conversations=conversations,
    )

    agent(req)

    assert [
        len(clients[key].requests) for key in ("chief", "secretary_1", "secretary_2", "emperor")
    ] == [3, 2, 2, 1]
    redraft_prompts = [
        "\n".join(message.content for message in clients[key].requests[1].messages)
        for key in ("chief", "secretary_1", "secretary_2")
    ]
    assert all("当前可见内阁草案" in prompt for prompt in redraft_prompts)
    assert all("当前决策投票结果" not in prompt for prompt in redraft_prompts)
    advice_prompt = "\n".join(message.content for message in clients["chief"].requests[2].messages)
    assert "当前决策投票结果" in advice_prompt


def test_ming_advice_is_system_forced_and_history_is_complete(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    option_ids = [item.option_id for item in req.options]
    alternate = option_ids[1]
    clients = {
        "chief": Stub(
            [
                choice(req, option_ids[0], "首辅首稿"),
                choice(req, option_ids[0], "首辅重拟"),
                choice(req, alternate, "错误汇总"),
                choice(req, alternate, "错误汇总重试"),
                choice(req, alternate, "终裁"),
            ]
        ),
        "secretary_1": Stub(
            [choice(req, alternate, "大学士一"), choice(req, alternate, "大学士一重拟")]
        ),
        "secretary_2": Stub(
            [choice(req, option_ids[0], "大学士二"), choice(req, option_ids[0], "大学士二重拟")]
        ),
        "emperor": Stub([choice(req, alternate, "终裁")]),
    }
    profiles = {role: ModelProfile(provider="mock", model=f"{role}-model") for role in clients}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1)
        for role in (
            "chief_grand_secretary",
            "grand_secretary_1",
            "grand_secretary_2",
            "emperor",
        )
    }
    agent = MingCourtAgent(
        player_id="a",
        chief_client=clients["chief"],
        chief_profile=profiles["chief"],
        secretary_1_client=clients["secretary_1"],
        secretary_1_profile=profiles["secretary_1"],
        secretary_2_client=clients["secretary_2"],
        secretary_2_profile=profiles["secretary_2"],
        emperor_client=clients["emperor"],
        emperor_profile=profiles["emperor"],
        conversations=conversations,
    )

    raw_final = agent(req)
    agent.record_final_decision(req, raw_final)

    calls = cast(list[dict[str, object]], agent.court_trace()["calls"])
    advice_call = next(
        call
        for call in calls
        if call["phase"] == "advice" and call["outcome"] == "advice_normalized"
    )
    advice = json.loads(cast(str, advice_call["content"]))
    assert advice["selected_option"]["option"] == option_ids[0]
    assert any(call["content_type"] == "advice" for call in calls)
    chief_turn = conversations["chief_grand_secretary"].current_turn
    assert chief_turn is not None
    assert any(
        getattr(entry, "assistant_reply", None) == cast(str, advice_call["content"])
        for entry in chief_turn.entries
    )
    for role in ("grand_secretary_1", "grand_secretary_2"):
        turn = conversations[role].current_turn
        assert turn is not None
        assert any(getattr(entry, "content_type", None) == "advice" for entry in turn.entries)
        assert any(getattr(entry, "content_type", None) == "vote_result" for entry in turn.entries)
