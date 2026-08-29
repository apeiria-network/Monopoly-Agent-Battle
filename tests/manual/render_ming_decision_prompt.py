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
from monopoly_agent_battle.decision.protocol import command_from_option
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
    engine.state.properties[6].owner_id = "a"
    engine.state.players["a"].properties.add(6)
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
    def __init__(self, role: str, mode: str) -> None:
        self.role = role
        self.mode = mode
        self.request_data: DecisionRequest | None = None
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        request_data = self.request_data
        if request_data is None:
            raise RuntimeError("capture client request_data was not prepared")
        call_number = len(self.requests)
        role_choices = {
            "chief_grand_secretary": "mortgage",
            "grand_secretary_1": "end_turn",
            "grand_secretary_2": "mortgage",
        }
        if self.role == "emperor" or self.mode == "unanimous":
            option = "mortgage"
        elif self.mode == "vote" or call_number == 1:
            option = role_choices[self.role]
        else:
            option = "mortgage"
        selected = _selected_option(request_data, option)
        content = json.dumps(
            {"reason": f"{self.role}第{call_number}次固定意见", "selected_option": selected},
            ensure_ascii=False,
        )
        return LLMResponse(content, UsageMetrics(1, 1), request.model)


def _make_agent(mode: str) -> tuple[MingCourtAgent, dict[str, _CaptureClient]]:
    clients = {role: _CaptureClient(role, mode) for role in _ROLES}
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
    option = next(item for item in request.options if item.option_id == "mortgage")
    target: dict[str, object] | None = (
        {option.target.command_fields[0]: 1} if option.target is not None else None
    )
    events = engine.execute(command_from_option(request, option, target))
    for event in events:
        for conversation in agent.role_conversations.values():
            conversation.append_event(event, engine.state.complete_rounds)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT


def _capture(
    label: str, role: str, mode: str, second: bool, request_index: int
) -> tuple[tuple[LLMMessage, ...], object]:
    with TemporaryDirectory() as directory:
        engine = _make_engine(directory)
        agent, clients = _make_agent(mode)
        first = build_decision_request(engine, sequence=1)
        if second:
            _complete_first_decision(engine, agent, clients, first)
            request = build_decision_request(engine, sequence=2)
            _run_agent(agent, clients, request)
        else:
            request = first
            _run_agent(agent, clients, request)
        selected = clients[role].requests[request_index]
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
    if role == "emperor":
        assert "当前可见内阁草案" not in dynamic
        assert "当前决策投票结果" not in dynamic
    if label == "7":
        assert "当前可见内阁草案" in dynamic
    if label == "8":
        assert "当前决策投票结果" in dynamic


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
        ("1", "chief_grand_secretary", "unanimous", False, -1),
        ("2", "emperor", "unanimous", False, -1),
        ("3", "chief_grand_secretary", "unanimous", True, 2),
        ("4", "grand_secretary_1", "unanimous", True, 1),
        ("5", "chief_grand_secretary", "divergent", True, -1),
        ("6", "emperor", "divergent", True, -1),
        ("7", "chief_grand_secretary", "divergent", True, 4),
        ("8", "chief_grand_secretary", "vote", True, -1),
        ("9", "emperor", "vote", True, -1),
    )
    for label, role, mode, second, request_index in scenarios:
        messages, warning = _capture(label, role, mode, second, request_index)
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
