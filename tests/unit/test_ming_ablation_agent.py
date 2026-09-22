from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from monopoly_agent_battle.agents.ming_ablation import MingAblationCourtAgent
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


class FlakyStub(Stub):
    """Raise the configured error on the configured 1-based call numbers."""

    def __init__(
        self, responses: list[str], fail_calls: set[int], error: Exception | None = None
    ) -> None:
        super().__init__(responses)
        self.fail_calls = fail_calls
        self.error = error if error is not None else ConnectionError("simulated timeout")
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls in self.fail_calls:
            raise self.error
        return super().complete(request)


ROLES = ("chief_grand_secretary", "grand_secretary_1", "grand_secretary_2", "emperor")


def make_request(tmp_path: Path, sequence: int = 1) -> DecisionRequest:
    config = GameConfig(
        game_id="ming-ablation-unit",
        experiment_id="ming-ablation-unit",
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


def build_agent(
    clients: dict[str, Stub],
) -> tuple[MingAblationCourtAgent, dict[str, Stub], dict[str, AgentConversation]]:
    profiles = {role: ModelProfile(provider="mock", model=f"{role}-model") for role in clients}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in ROLES
    }
    agent = MingAblationCourtAgent(
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
    return agent, clients, conversations


def unanimous_clients(req: DecisionRequest) -> dict[str, Stub]:
    options = [item.option_id for item in req.options]
    return {
        "chief": Stub([choice(req, options[0], "首辅草案")]),
        "secretary_1": Stub([choice(req, options[0], "大学士一草案")]),
        "secretary_2": Stub([choice(req, options[0], "大学士二草案")]),
        "emperor": Stub([choice(req, options[0], "终裁")]),
    }


def emperor_prompts(clients: dict[str, Stub], index: int = 0) -> str:
    return "\n".join(message.content for message in clients["emperor"].requests[index].messages)


def test_ablation_unanimous_no_advice_call_and_cabinet_block(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    agent, clients, _ = build_agent(unanimous_clients(req))

    reply = agent(req)

    assert json.loads(reply)["reason"] == "终裁"
    # Core ablation: 3 drafts + emperor only; the chief never writes an advice.
    assert [
        len(clients[key].requests) for key in ("chief", "secretary_1", "secretary_2", "emperor")
    ] == [1, 1, 1, 1]
    prompt = emperor_prompts(clients)
    assert "## 内阁意见与投票" in prompt
    assert '"decision_maker":"chief_grand_secretary","content_type":"draft"' in prompt
    assert '"decision_maker":"grand_secretary_1","content_type":"draft"' in prompt
    assert '"decision_maker":"grand_secretary_2","content_type":"draft"' in prompt
    assert "全票通过" in prompt
    assert '"content_type":"advice"' not in prompt
    # Opinions first, tally last.
    assert prompt.index("首辅：") < prompt.index("大学士一：") < prompt.index("大学士二：")
    assert prompt.index("大学士二：") < prompt.index("投票结果：")
    trace_calls = cast(list[dict[str, object]], agent.court_trace()["calls"])
    assert agent.court_trace()["court"] == "ming_ablation"
    assert not any(call["content_type"] == "advice" for call in trace_calls)
    assert any(call["content_type"] == "final_decision" for call in trace_calls)


def test_ablation_cabinet_block_only_reaches_emperor(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    agent, _, conversations = build_agent(unanimous_clients(req))

    agent(req)

    emperor_entries = conversations["emperor"].current_turn.entries  # type: ignore[union-attr]
    cabinet = [
        entry
        for entry in emperor_entries
        if getattr(entry, "content_type", "") == "cabinet_opinions"
    ]
    assert len(cabinet) == 1
    assert getattr(cabinet[0], "decision_maker", "") == "system"
    for role in ROLES[:3]:
        turn = conversations[role].current_turn
        assert turn is not None
        assert not any(
            getattr(entry, "content_type", "") == "cabinet_opinions" for entry in turn.entries
        )


def test_ablation_split_redrafts_tally_and_officer_vote_history(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    option_ids = [item.option_id for item in req.options]
    alternate = option_ids[1]
    clients = {
        "chief": Stub(
            [choice(req, option_ids[0], "首辅首稿"), choice(req, option_ids[0], "首辅重拟")]
        ),
        "secretary_1": Stub(
            [choice(req, alternate, "大学士一首稿"), choice(req, alternate, "大学士一重拟")]
        ),
        "secretary_2": Stub(
            [choice(req, option_ids[0], "大学士二首稿"), choice(req, option_ids[0], "大学士二重拟")]
        ),
        "emperor": Stub([choice(req, option_ids[0], "终裁")]),
    }
    agent, clients, conversations = build_agent(clients)

    agent(req)

    assert [
        len(clients[key].requests) for key in ("chief", "secretary_1", "secretary_2", "emperor")
    ] == [2, 2, 2, 1]
    redraft_prompts = [
        "\n".join(message.content for message in clients[key].requests[1].messages)
        for key in ("chief", "secretary_1", "secretary_2")
    ]
    assert all("内阁意见不一致，请重新草拟" in prompt for prompt in redraft_prompts)
    for prompt in redraft_prompts:
        assert prompt.index('"content_type":"draft"') < prompt.index("内阁意见不一致，请重新草拟")
    prompt = emperor_prompts(clients)
    # The block carries the REDRAFTED drafts, not the first versions.
    assert "首辅重拟" in prompt
    assert "大学士一重拟" in prompt
    assert "首辅首稿" not in prompt.split("## 当前局面")[0].split("内阁意见与投票")[-1]
    assert "得 2.5 票（首辅、大学士二）" in prompt
    assert "得 1.0 票（大学士一）" in prompt
    assert "加权多数：" in prompt
    # Officers keep the vote history but never see the cabinet block.
    for role in ROLES[:3]:
        turn = conversations[role].current_turn
        assert turn is not None
        assert any(getattr(entry, "content_type", "") == "vote_result" for entry in turn.entries)
        assert not any(
            getattr(entry, "content_type", "") == "cabinet_opinions" for entry in turn.entries
        )


def test_ablation_three_way_split_tally_names_chief(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    base = req.options[0]
    third = DecisionOption(
        option_id="third_option",
        command_type=base.command_type,
        parameters=base.parameters,
        title="第三候选",
        preview=base.preview,
        response_format=base.response_format,
        is_default=False,
        target=base.target,
    )
    req = replace(req, options=(*req.options, third))
    option_ids = [item.option_id for item in req.options]
    clients = {
        "chief": Stub(
            [choice(req, option_ids[0], "首辅首稿"), choice(req, option_ids[0], "首辅重拟")]
        ),
        "secretary_1": Stub(
            [choice(req, option_ids[1], "大学士一首稿"), choice(req, option_ids[1], "大学士一重拟")]
        ),
        "secretary_2": Stub(
            [choice(req, option_ids[0], "大学士二首稿"), choice(req, option_ids[2], "大学士二重拟")]
        ),
        "emperor": Stub([choice(req, option_ids[0], "终裁")]),
    }
    agent, clients, _ = build_agent(clients)

    agent(req)

    prompt = emperor_prompts(clients)
    assert "三方意见各不相同" in prompt
    assert "首辅意见" in prompt
    assert "得 1.5 票" in prompt
    assert "加权多数为首辅意见：" in prompt


def test_ablation_history_carries_full_cabinet_block(tmp_path: Path) -> None:
    req1 = make_request(tmp_path, sequence=1)
    req2 = make_request(tmp_path, sequence=2)
    option_ids = [item.option_id for item in req1.options]
    clients = {
        "chief": Stub(
            [choice(req1, option_ids[0], "首辅草案一"), choice(req2, option_ids[0], "首辅草案二")]
        ),
        "secretary_1": Stub(
            [
                choice(req1, option_ids[0], "大学士一草案一"),
                choice(req2, option_ids[0], "大学士一草案二"),
            ]
        ),
        "secretary_2": Stub(
            [
                choice(req1, option_ids[0], "大学士二草案一"),
                choice(req2, option_ids[0], "大学士二草案二"),
            ]
        ),
        "emperor": Stub(
            [choice(req1, option_ids[0], "终裁一"), choice(req2, option_ids[0], "终裁二")]
        ),
    }
    agent, clients, _ = build_agent(clients)

    raw1 = agent(req1)
    agent.record_final_decision(req1, raw1)
    agent(req2)

    prompt = emperor_prompts(clients, index=1)
    # History replay + the current decision each carry one full block.
    assert prompt.count("## 内阁意见与投票") == 2
    assert "首辅草案一" in prompt  # first decision's block preserved in history
    assert "首辅草案二" in prompt


def test_ablation_secretary_validation_exhaustion_fallback_enters_block(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    options = [item.option_id for item in req.options]
    clients = {
        "chief": Stub([choice(req, options[0], "首辅草案")]),
        "secretary_1": Stub(["bad", "worse", "still bad"]),
        "secretary_2": Stub([choice(req, options[0], "大学士二草案")]),
        "emperor": Stub([choice(req, options[0], "终裁")]),
    }
    agent, clients, _ = build_agent(clients)

    agent(req)

    prompt = emperor_prompts(clients)
    assert "系统采用默认合法选项。" in prompt
    trace_calls = cast(list[dict[str, object]], agent.court_trace()["calls"])
    fallback_calls = [
        call
        for call in trace_calls
        if call["role"] == "grand_secretary_1" and call["outcome"] == "advice_normalized"
    ]
    assert len(fallback_calls) == 1
    assert len(clients["emperor"].requests) == 1


def test_ablation_secretary_connection_exhaustion_falls_back(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    option_ids = [item.option_id for item in req.options]
    secretary_1 = FlakyStub([choice(req, option_ids[0], "大学士一草案")], fail_calls={1, 2, 3})
    clients = {
        "chief": Stub([choice(req, option_ids[0], "首辅草案")]),
        "secretary_1": secretary_1,
        # secretary_2 is re-drafted on every retry: its result is discarded
        # each time secretary_1 raises before it can be stored.
        "secretary_2": Stub([choice(req, option_ids[0], "大学士二草案")] * 3),
        "emperor": Stub([choice(req, option_ids[0], "终裁")]),
    }
    agent, clients, _ = build_agent(clients)

    with pytest.raises(ConnectionError):
        agent(req)
    with pytest.raises(ConnectionError):
        agent(req)
    reply = agent(req)

    assert json.loads(reply)["reason"] == "终裁"
    assert secretary_1.calls == 3
    prompt = emperor_prompts(clients)
    assert "大学士一重连次数耗尽，无法做出有效回复。" in prompt
    trace_calls = cast(list[dict[str, object]], agent.court_trace()["calls"])
    outcomes = [(call["role"], call["outcome"]) for call in trace_calls]
    assert outcomes.count(("grand_secretary_1", "connection_error")) == 3
    assert ("grand_secretary_1", "connection_fallback") in outcomes
    assert ("emperor", "success") in outcomes


def test_ablation_emperor_validation_retry(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    options = [item.option_id for item in req.options]
    clients = {
        "chief": Stub([choice(req, options[0], "首辅草案")]),
        "secretary_1": Stub([choice(req, options[0], "大学士一草案")]),
        "secretary_2": Stub([choice(req, options[0], "大学士二草案")]),
        "emperor": Stub(["not json", choice(req, options[0], "终裁")]),
    }
    agent, clients, _ = build_agent(clients)

    reply = agent(req)

    assert json.loads(reply)["reason"] == "终裁"
    assert len(clients["emperor"].requests) == 2
    trace_calls = cast(list[dict[str, object]], agent.court_trace()["calls"])
    assert any(
        call["role"] == "emperor" and call["outcome"] == "validation_error" for call in trace_calls
    )


def test_ablation_record_final_decision_is_idempotent(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    agent, _, conversations = build_agent(unanimous_clients(req))

    raw = agent(req)
    agent.record_final_decision(req, raw)
    agent.record_final_decision(req, raw)

    turn = conversations["grand_secretary_1"].current_turn
    assert turn is not None
    finals = [
        entry for entry in turn.entries if getattr(entry, "content_type", "") == "final_decision"
    ]
    assert len(finals) == 1


def test_ablation_prompt_assembly(tmp_path: Path) -> None:
    req = make_request(tmp_path)
    agent, clients, _ = build_agent(unanimous_clients(req))

    agent(req)

    chief_system = clients["chief"].requests[0].messages[0].content
    assert "汇总" not in chief_system
    secretary_system = clients["secretary_1"].requests[0].messages[0].content
    assert "由皇帝最终裁决" in secretary_system
    assert "由首辅汇总" not in secretary_system
    emperor_system = clients["emperor"].requests[0].messages[0].content
    assert "最终意见和加权投票结果" in emperor_system
