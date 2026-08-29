"""Render nine Ming court prompt scenarios through the real court workflow."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.agents.ming import MingCourtAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.protocol import command_from_option, parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMMessage, LLMRequest, LLMResponse, UsageMetrics

_DIVIDER = "=" * 72
_REPORT_PATH = Path("tests/manual/render_ming_decision_prompt_report.txt")
_ROLES = ("chief_grand_secretary", "grand_secretary_1", "grand_secretary_2", "emperor")
_TITLES = {
    "1": "第一次决策：三人一致，首辅汇总 advice",
    "2": "第一次决策：首辅 advice 后皇帝终裁",
    "3": "第二次决策：首辅首次草拟",
    "4": "第二次决策：大学士一首次草拟",
    "5": "第二次决策：两轮草拟达成一致，首辅汇总 advice",
    "6": "第二次决策：两轮草拟达成一致，皇帝终裁",
    "7": "第二次决策：首次分歧后首辅重新草拟",
    "8": "第二次决策：两轮草拟仍不一致，首辅查看投票并汇总",
    "9": "第二次决策：两轮草拟仍不一致，皇帝读取 advice 后终裁",
}

def _make_engine(directory: str) -> GameEngine:
    config = GameConfig(
        game_id="ming-prompt-inspection",
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
    return engine


def _selected_option(request: DecisionRequest, option_id: str) -> dict[str, object]:
    option = next(item for item in request.options if item.option_id == option_id)
    selected: dict[str, object] = {"option": option_id}
    if option.target is not None:
        values = option.target.legal_values[-1]
        if len(option.target.fields) == 1:
            selected["target"] = values[0]
        else:
            selected["target"] = dict(zip(option.target.fields, values, strict=True))
    return selected


class _CaptureClient:
    def __init__(self, role: str, first_mode: str, second_mode: str) -> None:
        self.role = role
        self.modes = (first_mode, second_mode)
        self.request_data: DecisionRequest | None = None
        self._request_identity: int | None = None
        self._decision_number = 0
        self._call_number = 0
        self.requests: list[LLMRequest] = []
        self.phases: list[tuple[int, str]] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        request_data = self.request_data
        if request_data is None:
            raise RuntimeError("capture client request_data was not prepared")
        if self._request_identity != id(request_data):
            self._request_identity = id(request_data)
            self._decision_number += 1
            self._call_number = 0
        self._call_number += 1
        mode = self.modes[self._decision_number - 1]
        phase = self._phase(mode)
        self.phases.append((self._decision_number, phase))
        option = self._option(request_data, mode, phase)
        content = json.dumps(
            {
                "reason": f"{self.role}-{self._decision_number}-{phase}",
                "selected_option": _selected_option(request_data, option),
            },
            ensure_ascii=False,
        )
        return LLMResponse(content, UsageMetrics(1, 1), request.model)

    def _phase(self, mode: str) -> str:
        if self.role == "emperor":
            return "final"
        if self.role == "chief_grand_secretary":
            advice_call = 2 if mode == "unanimous" else 3
            if self._call_number == advice_call:
                return "advice"
        return "first" if self._call_number == 1 else "redraft"

    def _option(self, request: DecisionRequest, mode: str, phase: str) -> str:
        option_ids = [item.option_id for item in request.options]
        first = option_ids[0]
        alternate = option_ids[1] if len(option_ids) > 1 else first
        if self.role == "emperor" and self._decision_number == 1 and mode == "vote":
            return alternate
        if phase == "redraft" and mode == "consensus":
            return first
        if mode == "unanimous":
            return first
        return alternate if self.role == "grand_secretary_1" else first


def _make_agent(
    first_mode: str, second_mode: str = "unanimous"
) -> tuple[MingCourtAgent, dict[str, _CaptureClient]]:
    clients = {
        role: _CaptureClient(role, first_mode, second_mode) for role in _ROLES
    }
    profiles = {role: ModelProfile(provider="mock", model=f"ming-{role}") for role in _ROLES}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in _ROLES
    }
    return (
        MingCourtAgent(
            player_id="a",
            chief_client=clients["chief_grand_secretary"],
            chief_profile=profiles["chief_grand_secretary"],
            secretary_1_client=clients["grand_secretary_1"],
            secretary_1_profile=profiles["grand_secretary_1"],
            secretary_2_client=clients["grand_secretary_2"],
            secretary_2_profile=profiles["grand_secretary_2"],
            emperor_client=clients["emperor"],
            emperor_profile=profiles["emperor"],
            conversations=conversations,
        ),
        clients,
    )


def _run_agent(
    agent: MingCourtAgent,
    clients: dict[str, _CaptureClient],
    request: DecisionRequest,
) -> str:
    for client in clients.values():
        client.request_data = request
    return agent(request)


def _complete_first_decision(
    engine: GameEngine,
    agent: MingCourtAgent,
    clients: dict[str, _CaptureClient],
    request: DecisionRequest,
) -> None:
    reply = _run_agent(agent, clients, request)
    agent.record_final_decision(request, reply)
    validation = parse_and_validate(reply, request)
    if not validation.valid or validation.option is None:
        raise AssertionError(f"invalid deterministic emperor reply: {validation.error}")
    if validation.option.option_id != "mortgage":
        raise AssertionError("deterministic emperor reply must select mortgage")
    events = engine.execute(
        command_from_option(request, validation.option, validation.target)
    )
    for event in events:
        for conversation in agent.role_conversations.values():
            conversation.append_event(event, engine.state.complete_rounds)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT


def _capture(
    label: str,
    role: str,
    first_mode: str,
    second_mode: str | None,
    second: bool,
    phase: str,
) -> tuple[tuple[LLMMessage, ...], object]:
    with TemporaryDirectory() as directory:
        engine = _make_engine(directory)
        agent, clients = _make_agent(first_mode, second_mode or "unanimous")
        first = build_decision_request(engine, sequence=1)
        if second:
            _complete_first_decision(engine, agent, clients, first)
            request = build_decision_request(engine, sequence=2)
            _run_agent(agent, clients, request)
        else:
            request = first
            _run_agent(agent, clients, request)
        selected = next(
            request_item
            for request_item, request_phase in zip(
                clients[role].requests, clients[role].phases, strict=True
            )
            if request_phase == ((2 if second else 1), phase)
        )
        _assert_shape(selected.messages, role, label)
        return selected.messages, agent.last_context_warning


def _assert_shape(messages: tuple[LLMMessage, ...], role: str, label: str) -> None:
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
    assert (
        lines.index("## 当前局面")
        < lines.index("## 当前决策")
        < lines.index("## 合法候选操作")
    )
    assert "## 当前决策投票结果" not in dynamic
    if label in {"3", "4", "5", "7", "8"}:
        assert '"content_type":"draft"' in dynamic
        assert "内阁最终表决结果：" in dynamic
        assert '"content_type":"final_decision"' in dynamic
        expected_redrafts = 2 if label in {"5", "7", "8"} else 1
        assert dynamic.count("内阁意见不一致，请重新草拟") >= expected_redrafts
    if label in {"3", "5", "7", "8"}:
        minimum_summaries = 2 if label in {"5", "8"} else 1
        assert dynamic.count("请你汇总3位官员的草拟决策") >= minimum_summaries
    if label == "4":
        assert "请你汇总3位官员的草拟决策" not in dynamic
    if label in {"5", "8"}:
        assert dynamic.count("三人最终草案汇总") == 0
    if label == "8":
        assert dynamic.count("请你汇总3位官员的草拟决策") == 2
    if label == "7":
        assert "## 当前可见内阁草案" not in dynamic
    if role == "emperor":
        assert '"content_type":"draft"' not in dynamic
        assert "内阁最终表决结果：" not in dynamic
        assert "内阁意见不一致，请重新草拟" not in dynamic
    if label == "4":
        assert '"content_type":"advice"' in dynamic


def _write(
    buffer: StringIO,
    label: str,
    role: str,
    messages: tuple[LLMMessage, ...],
    warning: object,
) -> None:
    buffer.write(
        f"\n{_DIVIDER}\nSCENARIO {label}: {role} — {_TITLES[label]}\n{_DIVIDER}\n"
    )
    for index, message in enumerate(messages, 1):
        buffer.write(f"\n--- Message {index} [{message.role}] ---\n{message.content}\n")
    if warning is not None:
        buffer.write(f"\n[ContextWarning] {warning!r}\n")


def _render_once() -> str:
    buffer = StringIO()
    scenarios = (
        ("1", "chief_grand_secretary", "unanimous", None, False, "advice"),
        ("2", "emperor", "unanimous", None, False, "final"),
        ("3", "chief_grand_secretary", "vote", "unanimous", True, "first"),
        ("4", "grand_secretary_1", "vote", "unanimous", True, "first"),
        ("5", "chief_grand_secretary", "vote", "consensus", True, "advice"),
        ("6", "emperor", "vote", "consensus", True, "final"),
        ("7", "chief_grand_secretary", "vote", "vote", True, "redraft"),
        ("8", "chief_grand_secretary", "vote", "vote", True, "advice"),
        ("9", "emperor", "vote", "vote", True, "final"),
    )
    for label, role, first_mode, second_mode, second, phase in scenarios:
        messages, warning = _capture(
            label, role, first_mode, second_mode, second, phase
        )
        _write(buffer, label, role, messages, warning)
    return buffer.getvalue()


def main() -> None:
    first = _render_once()
    second = _render_once()
    if first != second:
        raise AssertionError("Ming prompt rendering is not deterministic")
    _REPORT_PATH.write_text(first, encoding="utf-8")
    print(f"Wrote {_REPORT_PATH} ({len(first)} chars)")


if __name__ == "__main__":
    main()
