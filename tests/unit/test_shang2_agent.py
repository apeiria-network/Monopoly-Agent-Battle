"""Unit coverage for the redesigned Shang2 court agent."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from monopoly_agent_battle.agents.shang2 import Shang2CourtAgent, oracle_for
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionOption, DecisionRequest
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

_OMENS = ("大吉", "中吉", "小吉", "小凶", "中凶", "大凶")
_MINISTERS = ("minister_1", "minister_2", "minister_3")


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
        game_id="shang2-unit",
        experiment_id="shang2-unit",
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


def _valid_response(request: DecisionRequest, option_id: str | None = None) -> str:
    if option_id is None:
        option_id = request.options[0].option_id
    return json.dumps(
        {"selected_option": {"option": option_id}, "reason": "大臣进言。"},
        ensure_ascii=False,
    )


def _with_extra_option(request: DecisionRequest) -> DecisionRequest:
    """Clone a request with one extra no-target option for distinct suggestions."""
    template = request.options[0]
    extra = DecisionOption(
        option_id=f"{template.option_id}-alt",
        command_type=template.command_type,
        parameters=dict(template.parameters),
        title="备用选项",
        preview=template.preview,
        response_format=dict(template.response_format),
        is_default=False,
        target=None,
    )
    return DecisionRequest(
        decision_id=request.decision_id,
        game_id=request.game_id,
        complete_rounds=request.complete_rounds,
        player_id=request.player_id,
        phase=request.phase,
        kind=request.kind,
        question=request.question,
        visible_state=request.visible_state,
        options=(*request.options, extra),
        output_constraints=request.output_constraints,
    )


def _setup(
    tmp_path: Path,
    responses: (
        dict[str, list[str | Exception]]
        | Callable[[DecisionRequest], dict[str, list[str | Exception]]]
    ),
) -> tuple[
    DecisionRequest,
    Shang2CourtAgent,
    dict[str, StubClient],
    dict[str, AgentConversation],
]:
    engine = _engine(tmp_path)
    request = build_decision_request(engine, sequence=1)
    if callable(responses):
        replies = responses(request)
    else:
        replies = responses
    clients = {role: StubClient(items) for role, items in replies.items()}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1)
        for role in (*_MINISTERS, "emperor")
    }
    return request, _agent(clients, conversations), clients, conversations


def _agent(
    clients: dict[str, StubClient],
    conversations: dict[str, AgentConversation],
) -> Shang2CourtAgent:
    return Shang2CourtAgent(
        player_id="a",
        seed=1,
        minister_1_client=clients["minister_1"],
        minister_1_profile=ModelProfile(provider="mock", model="m1"),
        minister_2_client=clients["minister_2"],
        minister_2_profile=ModelProfile(provider="mock", model="m2"),
        minister_3_client=clients["minister_3"],
        minister_3_profile=ModelProfile(provider="mock", model="m3"),
        emperor_client=clients["emperor"],
        emperor_profile=ModelProfile(provider="mock", model="emperor"),
        conversations=conversations,
    )


def test_ministers_advise_first_and_emperor_receives_oracle_copies(tmp_path: Path) -> None:
    request, agent, clients, _ = _setup(
        tmp_path,
        lambda request: {
            "minister_1": [_valid_response(request)],
            "minister_2": [_valid_response(request)],
            "minister_3": [_valid_response(request)],
            "emperor": [_valid_response(request)],
        },
    )

    reply = agent(request)

    assert json.loads(reply)["selected_option"]["option"] == request.options[0].option_id
    assert clients["emperor"].requests[0].caller_role == "a.emperor"
    assert {clients[role].requests[0].caller_role for role in _MINISTERS} == {
        "a.minister_1",
        "a.minister_2",
        "a.minister_3",
    }

    emperor_text = clients["emperor"].requests[0].messages[-1].content
    assert emperor_text.count('"oracle":') == 3
    for role in _MINISTERS:
        assert f'"decision_maker":"{role}"' in emperor_text
    for role in _MINISTERS:
        text = "\n".join(message.content for message in clients[role].requests[0].messages)
        assert '"oracle":' not in text
        assert "## 合法候选操作" in text

    trace = agent.court_trace()
    oracles = cast(list[dict[str, object]], trace["oracles"])
    calls = cast(list[dict[str, object]], trace["calls"])
    assert trace["court"] == "shang2"
    assert [call["role"] for call in calls] == [
        "minister_1",
        "minister_2",
        "minister_3",
        "emperor",
    ]
    assert len(oracles) == 1
    record = oracles[0]
    assert record["oracle"] in _OMENS
    assert record["option"] == request.options[0].option_id
    assert isinstance(record["derivation"], str) and record["derivation"]


def test_dedup_shares_one_oracle_across_the_same_suggestion(tmp_path: Path) -> None:
    request = _with_extra_option(build_decision_request(_engine(tmp_path), sequence=1))
    distinct = request.options[-1].option_id
    clients = {
        "minister_1": StubClient([_valid_response(request)]),
        "minister_2": StubClient([_valid_response(request)]),
        "minister_3": StubClient([_valid_response(request, distinct)]),
        "emperor": StubClient([_valid_response(request)]),
    }
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1)
        for role in (*_MINISTERS, "emperor")
    }
    agent = _agent(clients, conversations)

    agent(request)

    emperor_text = clients["emperor"].requests[0].messages[-1].content
    omen_values = [fragment.split('"')[0] for fragment in emperor_text.split('"oracle":"')[1:]]
    assert len(omen_values) == 3
    assert omen_values[0] == omen_values[1]
    trace = agent.court_trace()
    oracles = cast(list[dict[str, object]], trace["oracles"])
    assert len(oracles) == 2
    assert {record["option"] for record in oracles} == {
        request.options[0].option_id,
        distinct,
    }


def test_ministers_see_history_without_oracles_and_emperor_replays_with_them(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    first = build_decision_request(engine, sequence=1)
    second = build_decision_request(engine, sequence=2)
    responses = {
        "minister_1": [_valid_response(first), _valid_response(second)],
        "minister_2": [_valid_response(first), _valid_response(second)],
        "minister_3": [_valid_response(first), _valid_response(second)],
        "emperor": [_valid_response(first), _valid_response(second)],
    }
    clients = {role: StubClient(items) for role, items in responses.items()}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1)
        for role in (*_MINISTERS, "emperor")
    }
    agent = _agent(clients, conversations)

    first_reply = agent(first)
    conversations["emperor"].append_decision(
        decision_id=first.decision_id,
        question_summary="## 决策",
        assistant_reply=first_reply,
    )
    agent.record_final_decision(first, first_reply)
    agent(second)

    # Ministers replay their own reply plus peers and the emperor's final,
    # never the oracle field.
    for role in _MINISTERS:
        messages = clients[role].requests[1].messages
        text = "\n".join(message.content for message in messages)
        assert '"oracle":' not in text
        for peer in _MINISTERS:
            if peer != role:
                assert f'"decision_maker":"{peer}"' in text
        assert '"decision_maker":"emperor"' in text
        assistant_messages = [message for message in messages if message.role == "assistant"]
        assert [message.content for message in assistant_messages] == [_valid_response(first)]

    # The emperor replays the first decision's advice with its oracles.
    emperor_text = "\n".join(message.content for message in clients["emperor"].requests[1].messages)
    assert emperor_text.count('"oracle":') == 6
    oracles = cast(list[dict[str, object]], agent.court_trace()["oracles"])
    assert len(oracles) == 1


def test_emperor_validation_retry_does_not_repeat_ministers_or_change_omens(
    tmp_path: Path,
) -> None:
    request, agent, clients, _ = _setup(
        tmp_path,
        lambda request: {
            "minister_1": [_valid_response(request)],
            "minister_2": [_valid_response(request)],
            "minister_3": [_valid_response(request)],
            "emperor": [
                '{"selected_option":{"option":"illegal"},"reason":"x"}',
                _valid_response(request),
            ],
        },
    )

    first = agent(request)
    second = agent(request)
    assert first != second
    for role in _MINISTERS:
        assert len(clients[role].requests) == 1
    assert len(clients["emperor"].requests) == 2
    first_text = clients["emperor"].requests[0].messages[-1].content
    second_text = clients["emperor"].requests[1].messages[-1].content
    assert first_text.count('"oracle":') == 3
    assert second_text.count('"oracle":') == 3
    assert [call["role"] for call in agent.court_calls()] == [
        "minister_1",
        "minister_2",
        "minister_3",
        "emperor",
        "emperor",
    ]


def test_emperor_connection_failure_reraises_without_repeating_ministers(
    tmp_path: Path,
) -> None:
    request, agent, clients, _ = _setup(
        tmp_path,
        lambda request: {
            "minister_1": [_valid_response(request)],
            "minister_2": [_valid_response(request)],
            "minister_3": [_valid_response(request)],
            "emperor": [LLMConnectionError("down")],
        },
    )

    try:
        agent(request)
    except LLMConnectionError:
        pass
    else:
        raise AssertionError("expected connection error to propagate")

    for role in _MINISTERS:
        assert len(clients[role].requests) == 1


def test_minister_connection_failure_retries_only_that_minister(tmp_path: Path) -> None:
    request, agent, clients, _ = _setup(
        tmp_path,
        lambda request: {
            "minister_1": [LLMConnectionError("down"), _valid_response(request)],
            "minister_2": [_valid_response(request)],
            "minister_3": [_valid_response(request)],
            "emperor": [_valid_response(request)],
        },
    )

    try:
        agent(request)
    except LLMConnectionError:
        pass
    else:
        raise AssertionError("expected connection error to propagate")
    reply = agent(request)

    assert json.loads(reply)["selected_option"]["option"] == request.options[0].option_id
    assert len(clients["minister_1"].requests) == 2
    for role in ("minister_2", "minister_3"):
        assert len(clients[role].requests) == 1
    assert [call["role"] for call in agent.court_calls()] == [
        "minister_1",
        "minister_1",
        "minister_2",
        "minister_3",
        "emperor",
    ]


def test_minister_connection_exhaustion_falls_back_but_emperor_still_decides(
    tmp_path: Path,
) -> None:
    request, agent, clients, _ = _setup(
        tmp_path,
        lambda request: {
            "minister_1": [LLMConnectionError("down")] * 3,
            "minister_2": [_valid_response(request)],
            "minister_3": [_valid_response(request)],
            "emperor": [_valid_response(request)],
        },
    )

    for _ in range(2):
        try:
            agent(request)
        except LLMConnectionError:
            continue
    reply = agent(request)

    assert json.loads(reply)["selected_option"]["option"] == request.options[0].option_id
    outcomes = [call["outcome"] for call in agent.court_calls()]
    assert outcomes == [
        "connection_error",
        "connection_error",
        "connection_error",
        "connection_fallback",
        "success",
        "success",
        "success",
    ]
    fallback_content = cast(str, agent.court_calls()[3]["content"])
    assert "大臣一重连次数耗尽，无法做出有效回复。" in fallback_content
    emperor_text = clients["emperor"].requests[0].messages[-1].content
    assert '"oracle":' in emperor_text
    assert "大臣一重连次数耗尽" in emperor_text


def test_minister_validation_retries_then_falls_back(tmp_path: Path) -> None:
    request, agent, _, _ = _setup(
        tmp_path,
        lambda request: {
            "minister_1": [
                "不是JSON",
                '{"selected_option":{"option":"bad"},"reason":"x"}',
                "still bad",
            ],
            "minister_2": [_valid_response(request)],
            "minister_3": [_valid_response(request)],
            "emperor": [_valid_response(request)],
        },
    )

    reply = agent(request)

    assert json.loads(reply)["selected_option"]["option"] == request.options[0].option_id
    calls = agent.court_calls()
    assert [call["outcome"] for call in calls] == [
        "success",
        "validation_error",
        "success",
        "validation_error",
        "success",
        "validation_error",
        "advice_normalized",
        "success",
        "success",
        "success",
    ]
    assert calls[-1]["role"] == "emperor"
    advice_normalized = calls[6]
    assert advice_normalized["role"] == "minister_1"
    assert "系统采用默认合法选项。" in cast(str, advice_normalized["content"])
    oracles = cast(list[dict[str, object]], agent.court_trace()["oracles"])
    assert len(oracles) == 1


def test_ministers_run_without_seeing_current_peer_advice(tmp_path: Path) -> None:
    request, agent, clients, _ = _setup(
        tmp_path,
        lambda request: {
            "minister_1": [_valid_response(request)],
            "minister_2": [_valid_response(request)],
            "minister_3": [_valid_response(request)],
            "emperor": [_valid_response(request)],
        },
    )

    agent(request)

    for role in _MINISTERS:
        text = "\n".join(message.content for message in clients[role].requests[0].messages)
        for peer in _MINISTERS:
            if peer != role:
                assert f'"decision_maker":"{peer}"' not in text
        assert '"decision_maker":"emperor"' not in text
    emperor_text = clients["emperor"].requests[0].messages[-1].content
    for role in _MINISTERS:
        assert f'"decision_maker":"{role}"' in emperor_text


def test_oracle_is_deterministic_and_covers_all_omens() -> None:
    first = oracle_for(seed=7, player_id="a", decision_id="d-1", option="end_turn", target=None)
    second = oracle_for(seed=7, player_id="a", decision_id="d-1", option="end_turn", target=None)
    assert first == second
    assert first[0] in _OMENS
    assert first[1].startswith('["shang2-oracle-v1"')

    counts: dict[str, int] = {omen: 0 for omen in _OMENS}
    for index in range(600):
        omen, _ = oracle_for(
            seed=7, player_id="a", decision_id=f"d-{index}", option="end_turn", target=None
        )
        counts[omen] += 1
    assert set(counts) == set(_OMENS)
    assert all(count >= 40 for count in counts.values())

    no_target = [
        oracle_for(seed=7, player_id="a", decision_id=f"d-{index}", option="mortgage", target=None)[
            0
        ]
        for index in range(30)
    ]
    with_target = [
        oracle_for(seed=7, player_id="a", decision_id=f"d-{index}", option="mortgage", target=3)[0]
        for index in range(30)
    ]
    assert no_target != with_target


def test_options_from_prompt_still_parses_minister_prompts(tmp_path: Path) -> None:
    request, agent, clients, _ = _setup(
        tmp_path,
        lambda request: {
            "minister_1": [_valid_response(request)],
            "minister_2": [_valid_response(request)],
            "minister_3": [_valid_response(request)],
            "emperor": [_valid_response(request)],
        },
    )
    agent(request)

    options = options_from_prompt(clients["minister_2"].requests[0].messages[-1].content)
    assert options and options[0]["option_id"] == request.options[0].option_id
