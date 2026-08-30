"""Render Qin four-role context scenarios through the real court workflow.

The checked-in report is the human-approved context-structure baseline. This
renderer drives a real ``QinCourtAgent`` with deterministic capture clients and
must reproduce that report byte for byte.
"""

from __future__ import annotations

from collections.abc import Callable
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.agents.qin import QinCourtAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.protocol import command_from_option, parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMMessage, LLMRequest, LLMResponse, UsageMetrics
from monopoly_agent_battle.performance.random_generator import random_officer_performance

_DIVIDER = "=" * 60
_REPORT_PATH = Path("tests/manual/render_qin_decision_prompt_report.txt")
_ROLES = ("chancellor", "grand_marshal", "imperial_counsellor", "emperor")


def _make_engine(directory: str) -> GameEngine:
    config = GameConfig(
        game_id="prompt-inspection",
        experiment_id="manual-review",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=Path(directory),
    )
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    engine.state.properties[3].owner_id = "b"
    engine.state.players["b"].properties.add(3)
    engine.state.players["a"].chance_cards.append("chance-swap-property")
    return engine


def _advice(role: str, decision_number: int) -> str:
    if role == "chancellor" or decision_number == 2:
        title = "丞相" if role == "chancellor" else "太尉"
        return (
            f'{{"reason":"第{decision_number}次{title}建议",'
            '"selected_option":{"option":"end_turn"}}'
        )
    return (
        f'{{"reason":"第{decision_number}次太尉建议",'
        '"selected_option":{"option":"use_chance_card-chance-swap-property",'
        '"target":{"swap_in_position":3,"swap_out_position":1}}}'
    )


def _comment(decision_number: int) -> str:
    return (
        f'{{"reason":"第{decision_number}次御史大夫评价",'
        '"assessments":[{"officer_id":"chancellor","judgement":"agree",'
        '"reason":"丞相意见可行"},{"officer_id":"grand_marshal",'
        '"judgement":"neutral","reason":"太尉意见仍需观察"}]}'
    )


def _final(decision_number: int) -> str:
    if decision_number == 1:
        return '{"reason":"第1次皇帝裁决","selected_option":{"option":"mortgage","target":1}}'
    return '{"reason":"第2次皇帝裁决","selected_option":{"option":"end_turn"}}'


class _CaptureClient:
    def __init__(self, role: str) -> None:
        self.role = role
        self.decision_number = 0
        self.requests: list[tuple[int, LLMRequest]] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        if self.decision_number not in {1, 2}:
            raise RuntimeError("capture client decision number was not prepared")
        self.requests.append((self.decision_number, request))
        if self.role in {"chancellor", "grand_marshal"}:
            content = _advice(self.role, self.decision_number)
        elif self.role == "imperial_counsellor":
            content = _comment(self.decision_number)
        else:
            content = _final(self.decision_number)
        return LLMResponse(content, UsageMetrics(1, 1), request.model)


def _make_agent(
    performance_generator: Callable[[DecisionRequest], str | None] = random_officer_performance,
) -> tuple[QinCourtAgent, dict[str, _CaptureClient]]:
    clients = {role: _CaptureClient(role) for role in _ROLES}
    profiles = {role: ModelProfile(provider="mock", model=f"qin-{role}") for role in _ROLES}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in _ROLES
    }
    return (
        QinCourtAgent(
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
        ),
        clients,
    )


def _run_agent(
    agent: QinCourtAgent,
    clients: dict[str, _CaptureClient],
    request: DecisionRequest,
    decision_number: int,
) -> str:
    for client in clients.values():
        client.decision_number = decision_number
    return agent(request)


def _complete_first_decision(
    engine: GameEngine,
    agent: QinCourtAgent,
    clients: dict[str, _CaptureClient],
    request: DecisionRequest,
) -> None:
    reply = _run_agent(agent, clients, request, 1)
    agent.record_final_decision(request, reply)
    validation = parse_and_validate(reply, request)
    if not validation.valid or validation.option is None:
        raise AssertionError(f"invalid deterministic emperor reply: {validation.error}")
    if validation.option.option_id != "mortgage":
        raise AssertionError("deterministic first emperor reply must select mortgage")
    events = engine.execute(command_from_option(request, validation.option, validation.target))
    for event in events:
        for conversation in agent.role_conversations.values():  # type: ignore[attr-defined]
            conversation.append_event(event, engine.state.complete_rounds)


def _capture(role: str, second: bool) -> tuple[tuple[LLMMessage, ...], object]:
    with TemporaryDirectory() as directory:
        engine = _make_engine(directory)
        agent, clients = _make_agent()
        first_request = build_decision_request(engine, sequence=1)
        if second:
            _complete_first_decision(engine, agent, clients, first_request)
            request = build_decision_request(engine, sequence=2)
            _run_agent(agent, clients, request, 2)
            decision_number = 2
        else:
            _run_agent(agent, clients, first_request, 1)
            decision_number = 1
        selected = next(
            captured
            for captured_number, captured in clients[role].requests
            if captured_number == decision_number
        )
        _assert_shape(selected.messages, role, second)
        return selected.messages, agent.last_context_warning


def _assert_shape(messages: tuple[LLMMessage, ...], role: str, second: bool) -> None:
    assert messages and messages[0].role == "system"
    assert sum(message.role == "system" for message in messages) == 1
    assert all(
        not (left.role == "user" and right.role == "user")
        for left, right in zip(messages, messages[1:], strict=False)
    )
    dynamic = "\n".join(message.content for message in messages[1:])
    lines = dynamic.splitlines()
    assert lines.count("## 当前局面") == 1
    assert lines.count("## 当前决策") == 1
    assert lines.count("## 合法候选操作") == 1
    assert lines.index("## 当前局面") < lines.index("## 当前决策") < lines.index("## 合法候选操作")
    current = dynamic.rsplit("## 当前局面", 1)[-1]
    if role in {"chancellor", "grand_marshal"}:
        assert '"content_type":"advice"' not in current
        assert '"content_type":"comment"' not in current
    if role == "imperial_counsellor":
        assert dynamic.count('"content_type":"advice"') == (4 if second else 2)
        assert '"content_type":"comment"' not in current
    if role == "emperor":
        assert dynamic.count('"content_type":"comment"') == (2 if second else 1)
    if second:
        assert sum(message.role == "assistant" for message in messages) == 1
        if role != "emperor":
            assert '"content_type":"final_decision"' in dynamic


def _capture_performance(rounds: int) -> tuple[tuple[LLMMessage, ...], object]:
    with TemporaryDirectory() as directory:
        engine = _make_engine(directory)
        engine.state.complete_rounds = rounds
        first_request = build_decision_request(engine, sequence=1)
        performance = random_officer_performance(first_request)
        if performance is None:
            raise AssertionError("performance scenario did not produce performance text")

        def fixed_performance(_: DecisionRequest) -> str:
            return performance

        agent, clients = _make_agent(fixed_performance)
        _complete_first_decision(engine, agent, clients, first_request)
        second_request = build_decision_request(engine, sequence=2)
        _run_agent(agent, clients, second_request, 2)
        captured = clients["imperial_counsellor"].requests[-1][1]
        _assert_performance_shape(captured.messages, rounds)
        dynamic = "\n".join(message.content for message in captured.messages[1:])
        assert "第1次" in dynamic
        assert "第2次" in dynamic
        return captured.messages, agent.last_context_warning


def _assert_performance_shape(messages: tuple[LLMMessage, ...], rounds: int) -> None:
    dynamic = "\n".join(message.content for message in messages[1:])
    assert dynamic.count("## 官员绩效") == 2
    second_advice_pos = dynamic.index("第2次丞相建议")
    assert dynamic.index("## 官员绩效") < second_advice_pos < dynamic.rindex("## 官员绩效")
    assert dynamic.count("最近1个回合中") == 2
    assert dynamic.count("最近多个回合中") == (2 if rounds >= 3 else 0)
    assert dynamic.rindex("## 官员绩效") < dynamic.rindex("## 当前决策")


def _write(
    buffer: StringIO,
    label: str,
    role: str,
    messages: tuple[LLMMessage, ...],
    warning: object,
) -> None:
    buffer.write(f"\n{_DIVIDER}\nSCENARIO {label}: 秦代 {role}\n{_DIVIDER}\n")
    for index, message in enumerate(messages, 1):
        buffer.write(f"\n--- Message {index} [{message.role}] ---\n{message.content}\n")
    if warning is not None:
        buffer.write(f"\n[ContextWarning] {warning!r}\n")


def _render_once() -> str:
    buffer = StringIO()
    for label, role in zip("ABCD", _ROLES, strict=True):
        messages, warning = _capture(role, second=False)
        _write(buffer, label, role, messages, warning)
    for label, role in zip("EFGH", _ROLES, strict=True):
        messages, warning = _capture(role, second=True)
        _write(buffer, label, role, messages, warning)
    for label, rounds in (("I", 1), ("J", 3)):
        messages, warning = _capture_performance(rounds)
        _write(buffer, label, f"imperial_counsellor（已完成{rounds}轮）", messages, warning)
    return buffer.getvalue()


def main() -> None:
    first = _render_once()
    second = _render_once()
    if first != second:
        raise AssertionError("Qin prompt rendering is not deterministic")
    _REPORT_PATH.write_text(first, encoding="utf-8")
    print(f"Wrote {_REPORT_PATH} ({len(first)} chars)")


if __name__ == "__main__":
    main()
