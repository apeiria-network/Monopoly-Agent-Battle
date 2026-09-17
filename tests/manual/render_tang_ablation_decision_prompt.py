"""Render eight Tang-ablation court prompt scenarios through the real workflow.

Scenarios cover the four roles (shangshu, zhongshu, menxia, emperor) at the
first and the second decision within the same action turn.  The Menxia review
is fixed to ``disagree`` so the emperor's context can be checked for the
ablation-specific path: a rejected draft still goes straight to the emperor
without any redraft round.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.agents.tang_ablation import TangAblationCourtAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.protocol import command_from_option, parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.commands import RollDice
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMMessage, LLMRequest, LLMResponse, UsageMetrics

_DIVIDER = "=" * 72
_REPORT_PATH = Path("tests/manual/render_tang_ablation_decision_prompt_report.txt")
_ROLES = ("shangshu", "zhongshu", "menxia", "emperor")
_TITLES = {
    "1": "第一次决策：尚书省生成全局信息摘要",
    "2": "第一次决策：中书省读取摘要后草拟",
    "3": "第一次决策：门下省读取草案后表态（反对，不打回）",
    "4": "第一次决策：皇帝读取草案与反对意见后终裁",
    "5": "第二次决策：尚书省生成全局信息摘要（含首次决策历史）",
    "6": "第二次决策：中书省读取摘要后草拟（含首次决策历史）",
    "7": "第二次决策：门下省读取草案后表态（含首次决策历史）",
    "8": "第二次决策：皇帝读取草案与反对意见后终裁（含首次决策历史）",
}


def _make_engine(directory: str) -> GameEngine:
    """A minimal engine placed in ROLLING so the action turn opens with a real
    dice roll whose move and forced-purchase events enter the history broadcast.
    """
    config = GameConfig(
        game_id="tang-ablation-prompt-inspection",
        experiment_id="manual-review",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=Path(directory),
    )
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ROLLING
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
    def __init__(self, role: str) -> None:
        self.role = role
        self.request_data: DecisionRequest | None = None
        self._request_identity: int | None = None
        self._decision_number = 0
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        request_data = self.request_data
        if request_data is None:
            raise RuntimeError("capture client request_data was not prepared")
        if self._request_identity != id(request_data):
            self._request_identity = id(request_data)
            self._decision_number += 1
        number = self._decision_number
        if self.role == "shangshu":
            content = f"尚书省摘要{number}：双方现金与地产态势见当前局面。"
        elif self.role == "menxia":
            content = json.dumps(
                {
                    "reason": f"门下省审核{number}：草案风险过高，反对，仅供皇帝参考。",
                    "selected_option": {"option": "disagree"},
                },
                ensure_ascii=False,
            )
        else:
            option_ids = [item.option_id for item in request_data.options]
            option_id = "mortgage" if self.role == "emperor" and number == 1 else option_ids[0]
            content = json.dumps(
                {
                    "reason": f"{self.role}理由{number}",
                    "selected_option": _selected_option(request_data, option_id),
                },
                ensure_ascii=False,
            )
        return LLMResponse(content, UsageMetrics(1, 1), request.model)


def _make_agent() -> tuple[TangAblationCourtAgent, dict[str, _CaptureClient]]:
    clients = {role: _CaptureClient(role) for role in _ROLES}
    profiles = {role: ModelProfile(provider="mock", model=f"tang-abl-{role}") for role in _ROLES}
    conversations = {
        role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in _ROLES
    }
    return (
        TangAblationCourtAgent(
            player_id="a",
            shangshu_client=clients["shangshu"],
            shangshu_profile=profiles["shangshu"],
            zhongshu_client=clients["zhongshu"],
            zhongshu_profile=profiles["zhongshu"],
            menxia_client=clients["menxia"],
            menxia_profile=profiles["menxia"],
            emperor_client=clients["emperor"],
            emperor_profile=profiles["emperor"],
            conversations=conversations,
        ),
        clients,
    )


def _run_agent(
    agent: TangAblationCourtAgent,
    clients: dict[str, _CaptureClient],
    request: DecisionRequest,
) -> str:
    for client in clients.values():
        client.request_data = request
    return agent(request)


def _complete_first_decision(
    engine: GameEngine,
    agent: TangAblationCourtAgent,
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
    events = engine.execute(command_from_option(request, validation.option, validation.target))
    for event in events:
        for conversation in agent.role_conversations.values():
            conversation.append_event(event, engine.state.complete_rounds)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT


def _capture(label: str, role: str, second: bool) -> tuple[tuple[LLMMessage, ...], object]:
    with TemporaryDirectory() as directory:
        engine = _make_engine(directory)
        agent, clients = _make_agent()
        for conversation in agent.role_conversations.values():
            conversation.start_turn(1)
        # 真实掷骰开局：2+3=5 移动到 Reading Railroad 并强制购买，产生真实
        # 骰子/移动/购买事件并写入各角色会话的历史播报。
        dice = iter((2, 3))
        engine.random.randint = lambda _low, _high: next(dice)  # type: ignore[method-assign]
        for event in engine.execute(RollDice("a")):
            for conversation in agent.role_conversations.values():
                conversation.append_event(event, engine.state.complete_rounds)
        first = build_decision_request(engine, sequence=1)
        if second:
            _complete_first_decision(engine, agent, clients, first)
            request = build_decision_request(engine, sequence=2)
            _run_agent(agent, clients, request)
        else:
            request = first
            _run_agent(agent, clients, request)
        decision_number = 2 if second else 1
        selected = clients[role].requests[decision_number - 1]
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
    assert lines.index("## 当前局面") < lines.index("## 当前决策") < lines.index("## 合法候选操作")
    # 回合0的真实掷骰、移动与强制购买必须出现在历史事件播报中。
    assert "[第0轮] 玩家a掷出2+3=5点。" in dynamic
    assert "[第0轮] 玩家a移动到第5格（Reading Railroad）。" in dynamic
    assert "[第0轮] 玩家a购买第5格（Reading Railroad），支付200。" in dynamic
    second = label in {"5", "6", "7", "8"}
    if role == "shangshu":
        # 尚书省从不接收任何朝廷内部消息。
        assert '"decision_maker"' not in dynamic
        assert '"content_type"' not in dynamic
    else:
        # 其余三角色的当前决策上下文都必须含本次尚书省摘要。
        assert '"decision_maker":"shangshu"' in dynamic
        assert '"content_type":"summary"' in dynamic
    if role == "menxia":
        # 门下省必须看到当中书省草案（内部消息紧凑 JSON）。
        assert '"content_type":"draft"' in dynamic
    if role == "emperor":
        # 皇帝必须看到本次草案与审核意见（pre_decision_context 带空格 JSON）。
        assert '"content_type": "draft"' in dynamic
        assert '"content_type": "review"' in dynamic
        assert '"option": "disagree"' in dynamic
    if role == "zhongshu" and not second:
        # 中书省看不到门下省对本次草案的审核意见。
        assert '"content_type":"review"' not in dynamic
    if second and role in {"zhongshu", "menxia"}:
        # 第二次决策：可见第一次决策的皇帝最终决策回放。
        assert '"content_type":"final_decision"' in dynamic
    if role == "emperor" and second:
        # 第二次决策的草案与审核意见在 pre_decision_context 中各出现一次。
        assert dynamic.count('"content_type": "draft"') == 1
        assert dynamic.count('"content_type": "review"') == 1


def _write(
    buffer: StringIO,
    label: str,
    role: str,
    messages: tuple[LLMMessage, ...],
    warning: object,
) -> None:
    buffer.write(f"\n{_DIVIDER}\nSCENARIO {label}: {role} — {_TITLES[label]}\n{_DIVIDER}\n")
    for index, message in enumerate(messages, 1):
        buffer.write(f"\n--- Message {index} [{message.role}] ---\n{message.content}\n")
    if warning is not None:
        buffer.write(f"\n[ContextWarning] {warning!r}\n")


def _render_once() -> str:
    buffer = StringIO()
    scenarios = (
        ("1", "shangshu", False),
        ("2", "zhongshu", False),
        ("3", "menxia", False),
        ("4", "emperor", False),
        ("5", "shangshu", True),
        ("6", "zhongshu", True),
        ("7", "menxia", True),
        ("8", "emperor", True),
    )
    for label, role, second in scenarios:
        messages, warning = _capture(label, role, second)
        _write(buffer, label, role, messages, warning)
    return buffer.getvalue()


def main() -> None:
    first = _render_once()
    second = _render_once()
    if first != second:
        raise AssertionError("Tang-ablation prompt rendering is not deterministic")
    _REPORT_PATH.write_text(first, encoding="utf-8")
    print(f"Wrote {_REPORT_PATH} ({len(first)} chars)")


if __name__ == "__main__":
    main()
