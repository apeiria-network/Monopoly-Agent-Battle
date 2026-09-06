"""Unit tests for the LLM protocol and the mock/recording clients."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.models import (
    DecisionKind,
    DecisionOption,
    DecisionRequest,
    OptionTarget,
)
from monopoly_agent_battle.llm.fake_client import FakeLLMClient
from monopoly_agent_battle.llm.mock_client import MockLLMClient, script_policy
from monopoly_agent_battle.llm.protocol import (
    LLMCallError,
    LLMConnectionError,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    UsageMetrics,
)
from monopoly_agent_battle.llm.recording_client import RecordingLLMClient
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


def _prompt_with_options(options: list[dict[str, object]]) -> str:
    return "## 当前局面\n状况。\n## 合法候选操作\n" + json.dumps(
        options, ensure_ascii=False, indent=2
    )


def _make_request(prompt: str, caller_role: str = "a") -> LLMRequest:
    return LLMRequest(
        messages=(LLMMessage(role="user", content=prompt),),
        model="mock-baseline-v1",
        caller_role=caller_role,
    )


def _llm_config(tmp_path: Path) -> GameConfig:
    return GameConfig(
        game_id="llm-game",
        experiment_id="llm-exp",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )


def _llm_call_records(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "llm-exp" / "llm-game" / "llm_calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_llm_connection_error_is_retryable() -> None:
    error = LLMConnectionError("down")
    assert isinstance(error, ConnectionError)
    assert isinstance(error, LLMCallError)


def test_uncached_input_tokens_are_clamped_for_inconsistent_provider_usage() -> None:
    usage = UsageMetrics(input_tokens=5, output_tokens=1, cached_input_tokens=8)

    assert usage.cached_input_tokens == 5
    assert usage.uncached_input_tokens == 0


def test_mock_selects_first_rendered_candidate_and_estimates_tokens() -> None:
    client = MockLLMClient(seed=0)
    request = _make_request(
        _prompt_with_options(
            [{"option_id": "end_turn", "title": "t", "preview": "p", "response_format": {}}]
        )
    )
    response = client.complete(request)
    document = json.loads(response.content)
    assert document["selected_option"]["option"] == "end_turn"
    assert response.model == "mock-baseline-v1"
    assert response.usage.input_tokens > 0
    assert response.usage.output_tokens > 0


def test_mock_same_seed_reproduces_response_sequence() -> None:
    options: list[dict[str, object]] = [
        {"option_id": f"opt-{index}", "title": "", "preview": "", "response_format": {}}
        for index in range(4)
    ]
    prompt = _prompt_with_options(options)
    first = MockLLMClient(seed=7)
    second = MockLLMClient(seed=7)
    assert [first.complete(_make_request(prompt)).content for _ in range(5)] == [
        second.complete(_make_request(prompt)).content for _ in range(5)
    ]


def test_fake_selects_legal_target_and_uses_context_request() -> None:
    option = DecisionOption(
        option_id="sell",
        command_type="SellBuilding",
        parameters={},
        title="sell",
        preview="sell",
        response_format={},
        target=OptionTarget("space", ("position",), ("target_position",), ((3,), (5,))),
    )
    decision = DecisionRequest(
        decision_id="d1",
        game_id="g",
        complete_rounds=0,
        player_id="a",
        phase="decision",
        kind=DecisionKind.ASSET_MANAGEMENT,
        question="choose",
        visible_state={},
        options=(option,),
        output_constraints={},
    )
    request = LLMRequest(
        messages=(LLMMessage(role="system", content="完整上下文"),),
        model="fake-v1",
        caller_role="a",
        decision_request=decision,
    )
    response = FakeLLMClient(seed=42).complete(request)
    assert json.loads(response.content)["selected_option"]["target"] in (3, 5)


def test_fake_same_seed_reproduces_responses() -> None:
    options: list[dict[str, object]] = [
        {"option_id": f"opt-{index}", "title": "", "preview": "", "response_format": {}}
        for index in range(4)
    ]
    prompt = _prompt_with_options(options)
    first = FakeLLMClient(seed=7)
    second = FakeLLMClient(seed=7)
    assert [first.complete(_make_request(prompt)).content for _ in range(5)] == [
        second.complete(_make_request(prompt)).content for _ in range(5)
    ]


def test_fake_special_role_formats() -> None:
    priest = FakeLLMClient().complete(_make_request("context", "p.great_priest"))
    counsellor = FakeLLMClient().complete(_make_request("context", "p.imperial_counsellor"))
    menxia = FakeLLMClient().complete(_make_request("context", "p.menxia"))
    assert "神谕" in priest.content
    assert {item["judgement"] for item in json.loads(counsellor.content)["assessments"]} <= {
        "agree",
        "disagree",
        "neutral",
    }
    assert json.loads(menxia.content)["selected_option"]["option"] in {"agree", "disagree"}
    client = MockLLMClient(policy=script_policy(["one", "two"]))
    assert client.complete(_make_request("ignored")).content == "one"
    assert client.complete(_make_request("ignored")).content == "two"
    assert client.complete(_make_request("ignored")).content == "two"


def _fake_summary_decision_request() -> DecisionRequest:
    options = tuple(
        DecisionOption(
            option_id=option_id,
            command_type="EndTurn",
            parameters={},
            title=option_id,
            preview=option_id,
            response_format={},
        )
        for option_id in ("end_turn", "mortgage")
    )
    return DecisionRequest(
        decision_id="d1",
        game_id="g",
        complete_rounds=0,
        player_id="a",
        phase="decision",
        kind=DecisionKind.ASSET_MANAGEMENT,
        question="choose",
        visible_state={},
        options=options,
        output_constraints={},
    )


def test_fake_ignores_summary_marker_left_in_earlier_messages() -> None:
    stale = (
        "请你汇总3位官员的草拟决策，并为决策"
        '{"option":"use_chance_card-chance-steal","target":"tang-court"}'
        "撰写对应决策理由"
    )
    request = LLMRequest(
        messages=(
            LLMMessage(role="user", content=stale),
            LLMMessage(role="assistant", content='{"selected_option":{"option":"end_turn"}}'),
            LLMMessage(role="user", content="## 当前决策\n请选择"),
        ),
        model="fake-random-v1",
        caller_role="a.chief_grand_secretary",
        decision_request=_fake_summary_decision_request(),
    )
    document = json.loads(FakeLLMClient(seed=42).complete(request).content)
    assert document["selected_option"]["option"] in {"end_turn", "mortgage"}
    assert document["reason"].startswith("模拟模型根据上下文随机选择候选操作")


def test_fake_follows_summary_marker_in_final_message() -> None:
    instruction = '请你汇总3位官员的草拟决策，并为决策{"option":"end_turn"}撰写对应决策理由'
    request = LLMRequest(
        messages=(
            LLMMessage(role="user", content="上下文"),
            LLMMessage(role="assistant", content='{"selected_option":{"option":"mortgage"}}'),
            LLMMessage(role="user", content=instruction + "\n\n## 当前决策\n请选择"),
        ),
        model="fake-random-v1",
        caller_role="a.chief_grand_secretary",
        decision_request=_fake_summary_decision_request(),
    )
    document = json.loads(FakeLLMClient(seed=42).complete(request).content)
    assert document["selected_option"] == {"option": "end_turn"}
    assert document["reason"] == "模拟模型采用内阁确定结果。"


def test_recording_client_writes_success_record(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(_llm_config(tmp_path))
    client = RecordingLLMClient(MockLLMClient(seed=0), artifacts)
    response = client.complete(
        _make_request(
            _prompt_with_options(
                [{"option_id": "end_turn", "title": "t", "preview": "p", "response_format": {}}]
            ),
            caller_role="a",
        )
    )

    records = _llm_call_records(tmp_path)
    assert len(records) == 1
    record = records[0]
    assert record["call_id"] == 1
    assert record["caller_role"] == "a"
    assert record["model"] == "mock-baseline-v1"
    assert record["input_tokens"] > 0
    assert record["cached_input_tokens"] == 0
    assert record["uncached_input_tokens"] == record["input_tokens"]
    assert record["output_tokens"] > 0
    assert record["thinking_tokens"] == 0
    assert record["tool_calls"] == 0
    assert record["tool_call_failures"] == 0
    assert record["response_summary"] == response.content
    assert record["error"] is None


def test_recording_client_records_failure_and_reraises(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(_llm_config(tmp_path))

    def disconnect_policy(_request: LLMRequest) -> str:
        raise LLMConnectionError("down")

    client = RecordingLLMClient(MockLLMClient(policy=disconnect_policy), artifacts)
    with pytest.raises(LLMConnectionError):
        client.complete(_make_request("prompt"))

    records = _llm_call_records(tmp_path)
    assert len(records) == 1
    assert records[0]["error"] == "down"
    assert records[0]["response_summary"] is None
    assert records[0]["cached_input_tokens"] == 0
    assert records[0]["uncached_input_tokens"] == 0


def test_recording_client_preserves_call_ids_across_failure_and_success(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(_llm_config(tmp_path))
    attempts = 0

    def mixed_policy(request: LLMRequest) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LLMConnectionError("temporary outage")
        return MockLLMClient(seed=0).complete(request).content

    client = RecordingLLMClient(MockLLMClient(policy=mixed_policy), artifacts)
    with pytest.raises(LLMConnectionError):
        client.complete(_make_request("first"))
    client.complete(
        _make_request(
            _prompt_with_options(
                [{"option_id": "end_turn", "title": "t", "preview": "p", "response_format": {}}]
            )
        )
    )

    records = _llm_call_records(tmp_path)
    assert [record["call_id"] for record in records] == [1, 2]
    assert records[0]["error"] == "temporary outage"
    assert records[0]["response_summary"] is None
    assert records[1]["error"] is None
    assert records[1]["response_summary"] is not None


def test_recording_client_writes_cached_and_uncached_input_tokens(tmp_path: Path) -> None:
    class CachedUsageClient:
        def complete(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                content="answer",
                usage=UsageMetrics(input_tokens=100, output_tokens=3, cached_input_tokens=75),
                model=request.model,
            )

    artifacts = RunArtifacts.create(_llm_config(tmp_path))
    RecordingLLMClient(CachedUsageClient(), artifacts).complete(_make_request("prompt"))

    record = _llm_call_records(tmp_path)[0]
    assert record["cached_input_tokens"] == 75
    assert record["uncached_input_tokens"] == 25
