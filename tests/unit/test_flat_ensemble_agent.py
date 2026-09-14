"""Unit coverage for the flat-ensemble (``flat_ensemble``) controller."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from monopoly_agent_battle.agents.flat_ensemble import (
    FlatEnsembleAgent,
    weighted_vote,
)
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionOption, DecisionRequest
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import (
    LLMConnectionError,
    LLMRequest,
    LLMResponse,
    UsageMetrics,
)

MEMBERS = ("member_1", "member_2", "member_3", "leader")


class StubClient:
    def __init__(self, responses: Sequence[str | Exception]) -> None:
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


def _engine(tmp_path: Path) -> GameEngine:
    config = GameConfig(
        game_id="flat-unit",
        experiment_id="flat-unit",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    return engine


def _valid_response(
    request: DecisionRequest, option_id: str | None = None, reason: str = "进言。"
) -> str:
    if option_id is None:
        option_id = request.options[0].option_id
    return json.dumps(
        {"selected_option": {"option": option_id}, "reason": reason},
        ensure_ascii=False,
    )


def _with_extra_options(request: DecisionRequest, count: int) -> DecisionRequest:
    """Clone a request with ``count`` extra no-target options for distinct votes."""
    template = request.options[0]
    extras = [
        DecisionOption(
            option_id=f"{template.option_id}-alt{i}",
            command_type=template.command_type,
            parameters=dict(template.parameters),
            title=f"备用选项{i}",
            preview=template.preview,
            response_format=dict(template.response_format),
            is_default=False,
            target=None,
        )
        for i in range(1, count + 1)
    ]
    return DecisionRequest(
        decision_id=request.decision_id,
        game_id=request.game_id,
        complete_rounds=request.complete_rounds,
        player_id=request.player_id,
        phase=request.phase,
        kind=request.kind,
        question=request.question,
        visible_state=request.visible_state,
        options=(*request.options, *extras),
        output_constraints=request.output_constraints,
    )


def _setup(
    tmp_path: Path,
    responses: dict[str, list[str | Exception]]
    | Callable[[DecisionRequest], dict[str, list[str | Exception]]],
) -> tuple[DecisionRequest, FlatEnsembleAgent, dict[str, StubClient], dict[str, AgentConversation]]:
    engine = _engine(tmp_path)
    request = build_decision_request(engine, sequence=1)
    replies = responses(request) if callable(responses) else responses
    clients = {role: StubClient(items) for role, items in replies.items()}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in MEMBERS
    }
    return request, _agent(clients, conversations), clients, conversations


def _agent(
    clients: dict[str, StubClient], conversations: dict[str, AgentConversation]
) -> FlatEnsembleAgent:
    return FlatEnsembleAgent(
        player_id="a",
        member_1_client=clients["member_1"],
        member_1_profile=ModelProfile(provider="mock", model="m1"),
        member_2_client=clients["member_2"],
        member_2_profile=ModelProfile(provider="mock", model="m2"),
        member_3_client=clients["member_3"],
        member_3_profile=ModelProfile(provider="mock", model="m3"),
        leader_client=clients["leader"],
        leader_profile=ModelProfile(provider="mock", model="leader"),
        conversations=conversations,
    )


def test_four_sessions_called_in_parallel_with_identical_baseline_prompts(tmp_path: Path) -> None:
    request, agent, clients, _ = _setup(
        tmp_path,
        lambda request: {role: [_valid_response(request)] for role in MEMBERS},
    )

    reply = agent(request)

    assert json.loads(reply)["selected_option"]["option"] == request.options[0].option_id
    # Four parallel calls, one per session, with baseline caller roles.
    assert {role: len(client.requests) for role, client in clients.items()} == {
        role: 1 for role in MEMBERS
    }
    assert {clients[role].requests[0].caller_role for role in MEMBERS} == {
        "a.member_1",
        "a.member_2",
        "a.member_3",
        "a.leader",
    }
    # All four receive the exact same baseline-level prompt (no role customization).
    prompts = [clients[role].requests[0].messages for role in MEMBERS]
    first_contents = [tuple(message.content for message in prompts[i]) for i in range(len(MEMBERS))]
    assert len({first_contents[i] for i in range(len(MEMBERS))}) == 1
    # No court artifacts leak into the baseline prompt.
    for role in MEMBERS:
        text = "\n".join(message.content for message in clients[role].requests[0].messages)
        assert '"oracle"' not in text
        assert '"decision_maker"' not in text


def test_leader_weight_breaks_two_two_tie(tmp_path: Path) -> None:
    request = _with_extra_options(build_decision_request(_engine(tmp_path), sequence=1), 1)
    option_a = request.options[0].option_id
    option_b = request.options[1].option_id
    agent, _ = _pair(
        tmp_path,
        request,
        {
            "member_1": option_a,
            "member_2": option_a,
            "member_3": option_b,
            "leader": option_b,
        },
    )

    reply = agent(request)

    # member_1+member_2 = 2.0 for A; member_3+leader = 1.0+1.5 = 2.5 for B -> B wins.
    assert json.loads(reply)["selected_option"]["option"] == option_b
    vote = cast(dict[str, object], agent.court_trace()["vote"])
    assert vote["selected_option"] == {"option": option_b}


def test_leader_is_outvoted_three_against_one(tmp_path: Path) -> None:
    request = _with_extra_options(build_decision_request(_engine(tmp_path), sequence=1), 1)
    option_a = request.options[0].option_id
    option_b = request.options[1].option_id
    agent, _ = _pair(
        tmp_path,
        request,
        {
            "member_1": option_a,
            "member_2": option_a,
            "member_3": option_a,
            "leader": option_b,
        },
    )

    reply = agent(request)

    # A = 3.0 vs B = 1.5 -> A wins, leader is outvoted.
    assert json.loads(reply)["selected_option"]["option"] == option_a


def test_all_different_leader_wins(tmp_path: Path) -> None:
    request = _with_extra_options(build_decision_request(_engine(tmp_path), sequence=1), 3)
    options = [request.options[i].option_id for i in range(4)]
    agent, _ = _pair(
        tmp_path,
        request,
        {
            "member_1": options[0],
            "member_2": options[1],
            "member_3": options[2],
            "leader": options[3],
        },
    )

    reply = agent(request)

    # Each option has one supporter; leader's 1.5 > 1.0 -> leader's option wins.
    assert json.loads(reply)["selected_option"]["option"] == options[3]


def test_final_reason_is_system_generated_vote_summary(tmp_path: Path) -> None:
    request, agent, _, _ = _setup(
        tmp_path,
        lambda request: {role: [_valid_response(request)] for role in MEMBERS},
    )

    reply = agent(request)

    reason = json.loads(reply)["reason"]
    assert "系统加权投票裁定" in reason
    assert "权重合计 4.5" in reason  # unanimous: 1+1+1+1.5 = 4.5
    assert "基干AI权重1.5" in reason


def test_each_session_records_only_own_reply_and_never_the_vote_result(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    first = _with_extra_options(build_decision_request(engine, sequence=1), 3)
    second = build_decision_request(engine, sequence=2)
    options = [first.options[i].option_id for i in range(4)]
    replies = {
        "member_1": [_valid_response(first, options[0], "理由一"), _valid_response(second)],
        "member_2": [_valid_response(first, options[1], "理由二"), _valid_response(second)],
        "member_3": [_valid_response(first, options[2], "理由三"), _valid_response(second)],
        "leader": [_valid_response(first, options[3], "理由四"), _valid_response(second)],
    }
    clients = {role: StubClient(items) for role, items in replies.items()}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in MEMBERS
    }
    agent = _agent(clients, conversations)

    first_reply = agent(first)
    # Leader won the all-different vote; the final decision is the leader's option.
    assert json.loads(first_reply)["selected_option"]["option"] == options[3]
    agent(second)

    # Each session's second prompt replays its OWN first reply as the assistant
    # message, and contains neither the vote result nor any peer's reply.
    own_replies = {
        "member_1": _valid_response(first, options[0], "理由一"),
        "member_2": _valid_response(first, options[1], "理由二"),
        "member_3": _valid_response(first, options[2], "理由三"),
        "leader": _valid_response(first, options[3], "理由四"),
    }
    for role in MEMBERS:
        messages = clients[role].requests[1].messages
        assistant_messages = [
            message.content for message in messages if message.role == "assistant"
        ]
        assert assistant_messages == [own_replies[role]]
        text = "\n".join(message.content for message in messages)
        for peer in MEMBERS:
            if peer != role:
                assert own_replies[peer] not in text
        # The system-authored vote summary never enters any session's history.
        assert "系统加权投票裁定" not in text


def test_court_trace_records_four_advice_calls_and_vote(tmp_path: Path) -> None:
    request, agent, _, _ = _setup(
        tmp_path,
        lambda request: {role: [_valid_response(request)] for role in MEMBERS},
    )

    agent(request)

    trace = agent.court_trace()
    assert trace["court"] == "flat_ensemble"
    calls = cast(list[dict[str, object]], trace["calls"])
    assert [call["role"] for call in calls] == ["member_1", "member_2", "member_3", "leader"]
    assert {call["outcome"] for call in calls} == {"success"}
    assert {call["content_type"] for call in calls} == {"advice"}
    vote = cast(dict[str, object], trace["vote"])
    assert vote["weights"] == {"member_1": 1.0, "member_2": 1.0, "member_3": 1.0, "leader": 1.5}


def test_last_llm_call_count_is_four_for_clean_success(tmp_path: Path) -> None:
    request, agent, _, _ = _setup(
        tmp_path,
        lambda request: {role: [_valid_response(request)] for role in MEMBERS},
    )

    agent(request)

    assert agent.last_llm_call_count == 4


def test_member_validation_retry_then_success(tmp_path: Path) -> None:
    request, agent, _, _ = _setup(
        tmp_path,
        lambda request: {
            "member_1": ["不是JSON", _valid_response(request)],
            "member_2": [_valid_response(request)],
            "member_3": [_valid_response(request)],
            "leader": [_valid_response(request)],
        },
    )

    reply = agent(request)

    assert json.loads(reply)["selected_option"]["option"] == request.options[0].option_id
    calls = agent.court_calls()
    # member_1 retried once after a validation error, then succeeded.
    member_1_outcomes = [call["outcome"] for call in calls if call["role"] == "member_1"]
    assert member_1_outcomes == ["validation_error", "success"]


def test_member_validation_exhausted_falls_back_to_default(tmp_path: Path) -> None:
    request, agent, _, _ = _setup(
        tmp_path,
        lambda request: {
            "member_1": ["bad", '{"selected_option":{"option":"nope"},"reason":"x"}', "still bad"],
            "member_2": [_valid_response(request)],
            "member_3": [_valid_response(request)],
            "leader": [_valid_response(request)],
        },
    )

    reply = agent(request)

    calls = agent.court_calls()
    member_1_outcomes = [call["outcome"] for call in calls if call["role"] == "member_1"]
    assert member_1_outcomes == [
        "validation_error",
        "validation_error",
        "validation_error",
        "advice_normalized",
    ]
    # The fallback advice (default option) participates in the vote; the three
    # valid sessions unanimously chose the default too, so it wins.
    assert json.loads(reply)["selected_option"]["option"] == request.options[0].option_id


def test_member_connection_exhaustion_falls_back_and_participates_in_vote(tmp_path: Path) -> None:
    request, agent, _, _ = _setup(
        tmp_path,
        lambda request: {
            "member_1": [LLMConnectionError("down")] * 3,
            "member_2": [_valid_response(request)],
            "member_3": [_valid_response(request)],
            "leader": [_valid_response(request)],
        },
    )

    for _ in range(2):
        try:
            agent(request)
        except LLMConnectionError:
            continue
    reply = agent(request)

    assert json.loads(reply)["selected_option"]["option"] == request.options[0].option_id
    calls = agent.court_calls()
    assert [call["outcome"] for call in calls] == [
        "connection_error",
        "connection_error",
        "connection_error",
        "connection_fallback",
        "success",
        "success",
        "success",
    ]
    fallback_content = cast(str, calls[3]["content"])
    assert "基干AI" not in fallback_content  # member_1 is not the backbone
    assert "成员一重连次数耗尽" in fallback_content


def test_weighted_vote_helper_never_ties() -> None:
    # With weights 1.5/1/1/1 the winner is always unique; verify a few splits.
    advice_all_same = {role: '{"selected_option":{"option":"x"},"reason":"r"}' for role in MEMBERS}
    assert weighted_vote(advice_all_same)["selected_option"] == {"option": "x"}

    advice_split = {
        "member_1": '{"selected_option":{"option":"a"},"reason":"r"}',
        "member_2": '{"selected_option":{"option":"a"},"reason":"r"}',
        "member_3": '{"selected_option":{"option":"b"},"reason":"r"}',
        "leader": '{"selected_option":{"option":"b"},"reason":"r"}',
    }
    assert weighted_vote(advice_split)["selected_option"] == {"option": "b"}


def _pair(
    tmp_path: Path,
    request: DecisionRequest,
    picks: dict[str, str],
) -> tuple[FlatEnsembleAgent, dict[str, StubClient]]:
    clients = {
        role: StubClient([_valid_response(request, option, f"{role}理由")])
        for role, option in picks.items()
    }
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in MEMBERS
    }
    return _agent(clients, conversations), clients
