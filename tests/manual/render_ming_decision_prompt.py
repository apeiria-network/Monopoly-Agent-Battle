"""Render nine confirmed Ming court prompt scenarios for human review."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.prompts import render_decision_question
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import GameEvent, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMMessage

_DIVIDER = "=" * 72
_REPORT_PATH = Path("tests/manual/render_ming_decision_prompt_report.txt")
_ROLE_ROOT = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "monopoly_agent_battle"
    / "agents"
    / "agent_prompt_list"
)
_ROLE_FILES = {
    "chief_grand_secretary": "Ming/chief_grand_secretary.txt",
    "grand_secretary_1": "Ming/grand_secretary.txt",
    "grand_secretary_2": "Ming/grand_secretary.txt",
    "emperor": "Ming/emperor.txt",
}
_ROLES = ("chief_grand_secretary", "grand_secretary_1", "grand_secretary_2")
_DRAFT, _VOTE, _ADVICE, _FINAL = "draft", "vote_result", "advice", "final_decision"
_REDRAFT_INSTRUCTION = (
    "内阁意见不一致，请重新草拟决策，你可以参考其他官员的意见，也可以提出你的个人观点。"
)
_ADVICE_INSTRUCTION = "请你汇总3位官员的草拟决策，并为决策{selected_option}撰写对应决策理由"
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


def _role_instruction(role: str) -> str:
    return (_ROLE_ROOT / _ROLE_FILES[role]).read_text(encoding="utf-8").strip()


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


def _raw(label: str, option: str = "end_turn") -> str:
    return json.dumps({"reason": label, "selected_option": {"option": option}}, ensure_ascii=False)


def _vote(number: int) -> str:
    return json.dumps(
        {
            "weights": {
                "chief_grand_secretary": 1.5,
                "grand_secretary_1": 1.0,
                "grand_secretary_2": 1.0,
            },
            "totals": {"end_turn": 1.5, "mortgage:3": 2.0},
            "selected_option": {"option": "mortgage", "position": 3},
            "decision_number": number,
        },
        ensure_ascii=False,
    )


def _event() -> GameEvent:
    return GameEvent("property_mortgaged", {"player_id": "a", "position": 1, "amount": 60})


def _vote_text(number: int) -> str:
    return (
        "内阁最终表决结果：\n{"
        + "selected_option:{option: end_turn}"
        + "} 共计1.5票\n{"
        + "selected_option:{option: mortgage, position: 3}"
        + "} 共计2.0票"
    )

    return GameEvent("property_mortgaged", {"player_id": "a", "position": 1, "amount": 60})


def _conversation(role: str) -> AgentConversation:
    conversation = AgentConversation(agent_id=f"a.{role}", window_turns=1)
    conversation.start_turn(1)
    return conversation


def _internal(
    conversation: AgentConversation,
    decision_id: str,
    request: DecisionRequest,
    maker: str,
    content_type: str,
    raw: str,
    suffix: str,
) -> None:
    conversation.append_internal_decision(
        internal_decision_id=f"{decision_id}:{maker}:{content_type}:{suffix}",
        decision_id=decision_id,
        question_summary=render_decision_question(request),
        decision_maker=maker,
        content_type=content_type,
        raw_content=raw,
    )


def _context(conversation: AgentConversation, content: str) -> None:
    conversation.append_context(content)


def _own(
    conversation: AgentConversation, decision_id: str, request: DecisionRequest, raw: str
) -> None:
    conversation.append_decision(
        decision_id=decision_id,
        question_summary=render_decision_question(request),
        assistant_reply=raw,
        allow_duplicate_decision_id=True,
    )


def _first_history(conversation: AgentConversation, role: str, request: DecisionRequest) -> None:
    decision_id = "ming-prompt-1"
    choices = {
        "chief_grand_secretary": "end_turn",
        "grand_secretary_1": "mortgage",
        "grand_secretary_2": "redeem_mortgage",
    }
    _own(conversation, decision_id, request, _raw(f"{role}第一次草案", choices[role]))
    for other in _ROLES:
        if other != role:
            _internal(
                conversation,
                decision_id,
                request,
                other,
                _DRAFT,
                _raw(f"{other}第一次草案", choices[other]),
                "first",
            )
    _context(conversation, _REDRAFT_INSTRUCTION)
    _own(conversation, decision_id, request, _raw(f"{role}第一次重新草案", choices[role]))
    for other in _ROLES:
        if other != role:
            _internal(
                conversation,
                decision_id,
                request,
                other,
                _DRAFT,
                _raw(f"{other}第一次重新草拟", choices[other]),
                "redraft",
            )
    _internal(
        conversation,
        decision_id,
        request,
        "system",
        _VOTE,
        _vote_text(1),
        "history",
    )
    if role == "chief_grand_secretary":
        _context(
            conversation,
            _ADVICE_INSTRUCTION.format(selected_option='{"option":"mortgage"}'),
        )
        _own(conversation, decision_id, request, _raw("首辅第一次advice", "mortgage"))
    else:
        _internal(
            conversation,
            decision_id,
            request,
            "chief_grand_secretary",
            _ADVICE,
            _raw("首辅第一次advice", "mortgage"),
            "advice",
        )
    _internal(
        conversation,
        decision_id,
        request,
        "emperor",
        _FINAL,
        _raw("皇帝第一次final_decision", "mortgage"),
        "final",
    )
    conversation.append_event(_event(), complete_round=1)


def _emperor_history(conversation: AgentConversation, request: DecisionRequest) -> None:
    decision_id = "ming-prompt-1"
    _internal(
        conversation,
        decision_id,
        request,
        "chief_grand_secretary",
        _ADVICE,
        _raw("首辅第一次advice", "mortgage"),
        "advice",
    )
    _own(conversation, decision_id, request, _raw("皇帝第一次final_decision", "mortgage"))
    conversation.append_event(_event(), complete_round=1)


def _unanimous_current(conversation: AgentConversation, request: DecisionRequest) -> None:
    decision_id = "ming-prompt-1"
    _own(conversation, decision_id, request, _raw("首辅第一次一致草案"))
    for role in ("grand_secretary_1", "grand_secretary_2"):
        _internal(
            conversation, decision_id, request, role, _DRAFT, _raw(f"{role}第一次一致草案"), "first"
        )


def _second_first_round(
    conversation: AgentConversation, request: DecisionRequest, role: str
) -> None:
    decision_id = "ming-prompt-2"
    _own(conversation, decision_id, request, _raw(f"{role}第二次首次草案"))
    for other in _ROLES:
        if other != role:
            _internal(
                conversation,
                decision_id,
                request,
                other,
                _DRAFT,
                _raw(f"{other}第二次首次草案"),
                "first",
            )


def _second_redraft_round(
    conversation: AgentConversation, request: DecisionRequest, include_vote: bool
) -> None:
    decision_id = "ming-prompt-2"
    current_choices = {
        "chief_grand_secretary": "end_turn",
        "grand_secretary_1": "mortgage",
        "grand_secretary_2": "redeem_mortgage",
    }
    _own(
        conversation,
        decision_id,
        request,
        _raw("首辅第二次首次草案", current_choices["chief_grand_secretary"]),
    )
    for role in ("grand_secretary_1", "grand_secretary_2"):
        _internal(
            conversation,
            decision_id,
            request,
            role,
            _DRAFT,
            _raw(f"{role}第二次首次草案", current_choices[role]),
            "first",
        )
    _own(conversation, decision_id, request, _raw("首辅第二次重新草案", "end_turn"))
    for role in ("grand_secretary_1", "grand_secretary_2"):
        _internal(
            conversation,
            decision_id,
            request,
            role,
            _DRAFT,
            _raw(
                f"{role}第二次重新草拟",
                "mortgage" if role == "grand_secretary_1" else "redeem_mortgage",
            ),
            "redraft",
        )
    if include_vote:
        _internal(conversation, decision_id, request, "system", _VOTE, _vote_text(2), "current")


def _second_advice(conversation: AgentConversation, request: DecisionRequest) -> None:
    _internal(
        conversation,
        "ming-prompt-2",
        request,
        "chief_grand_secretary",
        _ADVICE,
        _raw("首辅第二次advice", "mortgage"),
        "advice",
    )


def _render(
    role: str, request: DecisionRequest, conversation: AgentConversation
) -> tuple[tuple[LLMMessage, ...], object]:
    messages, warning = compose_prompt(
        conversation, request, role_instruction=_role_instruction(role)
    )
    _assert_shape(messages)
    return messages, warning


def _assert_shape(messages: tuple[LLMMessage, ...]) -> None:
    assert messages and messages[0].role == "system"
    assert all(
        not (left.role == "user" and right.role == "user")
        for left, right in zip(messages, messages[1:], strict=False)
    )
    dynamic = "\n".join(message.content for message in messages[1:])
    assert dynamic.count("## 当前局面") == 1
    assert dynamic.count("## 当前决策") == 1
    assert dynamic.count("## 合法候选操作") == 1
    assert (
        dynamic.index("## 当前局面")
        < dynamic.index("## 当前决策")
        < dynamic.index("## 合法候选操作")
    )


def _write(
    buffer: StringIO, label: str, role: str, messages: tuple[LLMMessage, ...], warning: object
) -> None:
    buffer.write(f"\n{_DIVIDER}\nSCENARIO {label}: {role} — {_TITLES[label]}\n{_DIVIDER}\n")
    for index, message in enumerate(messages, 1):
        buffer.write(f"\n--- Message {index} [{message.role}] ---\n{message.content}\n")
    if warning is not None:
        buffer.write(f"\n[ContextWarning] {warning!r}\n")


def main() -> None:
    buffer = StringIO()
    with TemporaryDirectory() as directory:
        engine = _make_engine(directory)
        first_request = build_decision_request(engine, sequence=1)
        second_request = build_decision_request(engine, sequence=2)
        scenarios: list[tuple[str, str, DecisionRequest, AgentConversation]] = []

        conversation = _conversation("chief_grand_secretary")
        _unanimous_current(conversation, first_request)
        scenarios.append(("1", "chief_grand_secretary", first_request, conversation))
        conversation = _conversation("emperor")
        _second_advice(conversation, first_request)
        scenarios.append(("2", "emperor", first_request, conversation))
        conversation = _conversation("chief_grand_secretary")
        _first_history(conversation, "chief_grand_secretary", first_request)
        scenarios.append(("3", "chief_grand_secretary", second_request, conversation))
        conversation = _conversation("grand_secretary_1")
        _first_history(conversation, "grand_secretary_1", first_request)
        scenarios.append(("4", "grand_secretary_1", second_request, conversation))
        conversation = _conversation("chief_grand_secretary")
        _first_history(conversation, "chief_grand_secretary", first_request)
        _second_redraft_round(conversation, second_request, include_vote=False)
        _context(
            conversation,
            _ADVICE_INSTRUCTION.format(selected_option='{"option":"mortgage"}'),
        )
        scenarios.append(("5", "chief_grand_secretary", second_request, conversation))
        conversation = _conversation("emperor")
        _emperor_history(conversation, first_request)
        _second_advice(conversation, second_request)
        scenarios.append(("6", "emperor", second_request, conversation))
        conversation = _conversation("chief_grand_secretary")
        _first_history(conversation, "chief_grand_secretary", first_request)
        _second_first_round(conversation, second_request, "chief_grand_secretary")
        scenarios.append(("7", "chief_grand_secretary", second_request, conversation))
        conversation = _conversation("chief_grand_secretary")
        _first_history(conversation, "chief_grand_secretary", first_request)
        _second_redraft_round(conversation, second_request, include_vote=True)
        _context(
            conversation,
            _ADVICE_INSTRUCTION.format(selected_option='{"option":"mortgage"}'),
        )
        scenarios.append(("8", "chief_grand_secretary", second_request, conversation))
        conversation = _conversation("emperor")
        _emperor_history(conversation, first_request)
        _second_advice(conversation, second_request)
        scenarios.append(("9", "emperor", second_request, conversation))
        for label, role, request, conversation in scenarios:
            messages, warning = _render(role, request, conversation)
            _write(buffer, label, role, messages, warning)
    _REPORT_PATH.write_text(buffer.getvalue(), encoding="utf-8")
    print(f"Wrote {_REPORT_PATH} ({len(buffer.getvalue())} chars)")


if __name__ == "__main__":
    main()
