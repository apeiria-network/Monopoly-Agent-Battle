"""Render ten Tang court prompt scenarios for human review."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.agents.tang import TangCourtAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.prompts import options_from_prompt
from monopoly_agent_battle.decision.protocol import command_from_option, parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMMessage, LLMRequest, LLMResponse, UsageMetrics

_DIVIDER = "=" * 72
_REPORT_PATH = Path("tests/manual/render_tang_decision_prompt_report.txt")
_ROLES = ("zhongshu", "menxia", "emperor")
_TITLES = {
    "1": "首次外部决策：中书省无历史首轮起草",
    "2": "首次外部决策：门下省审核第一轮中书草案",
    "3": "首次外部决策：门下省同意后皇帝依据通过轮终裁",
    "4": "当前决策首轮否决：中书省依据审核重拟第二轮草案",
    "5": "当前决策第二轮审核：门下省查看首轮否决与第二轮草案",
    "6": "当前决策第二轮通过：皇帝仅查看最终通过轮",
    "7": "当前决策三轮否决：皇帝查看完整三轮讨论",
    "8": "同一行动回合第二次决策：中书省回看两轮抵押决策历史",
    "9": "同一行动回合第二次决策：门下省审核抵押后的资产管理草案",
    "10": "同一行动回合第二次决策：皇帝回看前次通过轮并裁决新草案",
}


class _CaptureClient:
    def __init__(self, role: str, reviews: list[str]) -> None:
        self.role = role
        self.reviews = reviews
        self.target = 1
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.role == "menxia":
            verdict = self.reviews.pop(0)
            content = _review(verdict, f"第{len(self.requests)}轮审核意见")
        else:
            content = _engine_decision(
                request, f"{self.role}第{len(self.requests)}次意见", self.target
            )
        return LLMResponse(content, UsageMetrics(1, 1), request.model)


def _make_engine(directory: str) -> GameEngine:
    config = GameConfig(
        game_id="tang-prompt-inspection",
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
    for position in (1, 3):
        engine.state.properties[position].owner_id = "a"
        engine.state.players["a"].properties.add(position)
    return engine


def _engine_decision(request: LLMRequest, reason: str, target: int) -> str:
    options = options_from_prompt(request.messages[-1].content)
    option = next((item for item in options if item["option_id"] == "mortgage"), options[0])
    selected: dict[str, object] = {"option": option["option_id"]}
    if option["option_id"] == "mortgage":
        selected["target"] = target
    return json.dumps({"reason": reason, "selected_option": selected}, ensure_ascii=False)


def _review(verdict: str, reason: str) -> str:
    return json.dumps(
        {"reason": reason, "selected_option": {"option": verdict}},
        ensure_ascii=False,
    )


def _make_agent(
    reviews: list[str], conversations: dict[str, AgentConversation] | None = None
) -> tuple[TangCourtAgent, dict[str, _CaptureClient]]:
    clients = {role: _CaptureClient(role, list(reviews)) for role in _ROLES}
    profiles = {role: ModelProfile(provider="mock", model=f"tang-{role}") for role in _ROLES}
    if conversations is None:
        conversations = {
            role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in _ROLES
        }
    for conversation in conversations.values():
        if conversation.current_turn is None:
            conversation.start_turn(1)
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


def _capture_single(
    engine: GameEngine, role: str, reviews: list[str]
) -> tuple[tuple[LLMMessage, ...], object]:
    request = build_decision_request(engine, sequence=1)
    agent, clients = _make_agent(reviews)
    agent(request)
    return clients[role].requests[-1].messages, agent.last_context_warning


def _capture_same_turn_second(
    engine: GameEngine, role: str, first_reviews: list[str], second_reviews: list[str]
) -> tuple[tuple[LLMMessage, ...], object]:
    conversations = {
        name: AgentConversation(agent_id=f"a.{name}", window_turns=1) for name in _ROLES
    }
    agent, clients = _make_agent(first_reviews, conversations)
    first_request = build_decision_request(engine, sequence=1)
    first_reply = agent(first_request)
    agent.record_final_decision(first_request, first_reply)
    validation = parse_and_validate(first_reply, first_request)
    if not validation.valid or validation.option is None:
        raise AssertionError("first Tang decision must be a valid engine decision")
    events = engine.execute(
        command_from_option(first_request, validation.option, validation.target)
    )
    for event in events:
        for conversation in conversations.values():
            conversation.append_event(event, engine.state.complete_rounds)

    clients["menxia"].reviews.extend(second_reviews)
    clients["zhongshu"].target = 3
    clients["emperor"].target = 3
    second_request = build_decision_request(engine, sequence=2)
    agent(second_request)
    return clients[role].requests[-1].messages, agent.last_context_warning


def _write(
    buffer: StringIO,
    label: str,
    role: str,
    messages: tuple[LLMMessage, ...],
    warning: object,
) -> None:
    buffer.write(f"\n{_DIVIDER}\nSCENARIO {label}: {_TITLES[label]} [{role}]\n{_DIVIDER}\n")
    for index, message in enumerate(messages, 1):
        buffer.write(f"\n--- Message {index} [{message.role}] ---\n{message.content}\n")
    if warning is not None:
        buffer.write(f"\n[ContextWarning] {warning!r}\n")


def main() -> None:
    buffer = StringIO()
    with TemporaryDirectory() as directory:
        for label, role, reviews in (
            ("1", "zhongshu", ["agree"]),
            ("2", "menxia", ["agree"]),
            ("3", "emperor", ["agree"]),
            ("4", "zhongshu", ["disagree", "agree"]),
            ("5", "menxia", ["disagree", "agree"]),
            ("6", "emperor", ["disagree", "agree"]),
            ("7", "emperor", ["disagree", "disagree", "disagree"]),
        ):
            messages, warning = _capture_single(_make_engine(directory), role, reviews)
            _write(buffer, label, role, messages, warning)
        for label, role, first_reviews, second_reviews in (
            ("8", "zhongshu", ["disagree", "agree"], ["agree"]),
            ("9", "menxia", ["disagree", "agree"], ["agree"]),
            ("10", "emperor", ["disagree", "agree"], ["disagree", "agree"]),
        ):
            messages, warning = _capture_same_turn_second(
                _make_engine(directory), role, first_reviews, second_reviews
            )
            _write(buffer, label, role, messages, warning)
    _REPORT_PATH.write_text(buffer.getvalue(), encoding="utf-8")
    print(f"Wrote {_REPORT_PATH} ({len(buffer.getvalue())} chars)")


if __name__ == "__main__":
    main()
