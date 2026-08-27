"""Render Qin four-role context scenarios for human review.

The scenarios use the same engine fixture and ASSET_MANAGEMENT decision timing
as the former Qin-oriented F/G scenarios.  A-D render the first decision for
Chancellor, Grand Marshal, Imperial Counsellor, and Emperor; E-H render the
second decision in the same action turn for those roles.
"""

from __future__ import annotations

from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.prompts import render_decision_question
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMMessage
from monopoly_agent_battle.performance.random_generator import random_officer_performance

_DIVIDER = "=" * 60
_REPORT_PATH = Path("tests/manual/render_qin_decision_prompt_report.txt")
_ROLES = ("chancellor", "grand_marshal", "imperial_counsellor", "emperor")
_PROMPT_ROOT = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "monopoly_agent_battle"
    / "agents"
    / "agent_prompt_list"
)


def _load_prompt(relative_path: str) -> str:
    return (_PROMPT_ROOT / relative_path).read_text(encoding="utf-8").strip()


_ROLE_INSTRUCTIONS = {
    "chancellor": _load_prompt("Qin/Qin_chancellor.txt"),
    "grand_marshal": _load_prompt("Qin/Qin_grand_marshal.txt"),
    "imperial_counsellor": _load_prompt("Qin/Qin_imperial_counsellor.txt"),
    "emperor": _load_prompt("Qin/Qin_emperor.txt"),
}
_NORMAL_OUTPUT_REQUIREMENT = _load_prompt("normal_output_requirement.txt")
_COUNSELLOR_OUTPUT_REQUIREMENT = _load_prompt("Qin/Qin_cousellor_output_requirement.txt")
_COUNSELLOR_SPECIAL_CONTEXT = _load_prompt("Qin/Qin_cousellor_candidates.txt")


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
    if role == "chancellor":
        return (
            f'{{"reason":"第{decision_number}次丞相建议",'
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
    return f'{{"reason":"第{decision_number}次皇帝裁决","selected_option":{{"option":"end_turn"}}}}'


def _append_internal(
    conversation: AgentConversation,
    decision_id: str,
    question: str,
    role: str,
    content_type: str,
    raw_content: str,
) -> None:
    conversation.append_internal_decision(
        internal_decision_id=f"{decision_id}:{role}:{content_type}",
        decision_id=decision_id,
        question_summary=question,
        decision_maker=role,
        content_type=content_type,
        raw_content=raw_content,
    )


def _append_current_court_messages(
    conversation: AgentConversation, request: DecisionRequest, decision_number: int, role: str
) -> None:
    decision_id = f"qin-prompt-{decision_number}"
    question = render_decision_question(request)
    if role in {"imperial_counsellor", "emperor"}:
        for officer in ("chancellor", "grand_marshal"):
            _append_internal(
                conversation,
                decision_id,
                question,
                officer,
                "advice",
                _advice(officer, decision_number),
            )
    if role == "emperor":
        _append_internal(
            conversation,
            decision_id,
            question,
            "imperial_counsellor",
            "comment",
            _comment(decision_number),
        )


def _append_previous_decision(
    conversation: AgentConversation, request: DecisionRequest, role: str
) -> None:
    decision_id = "qin-prompt-1"
    question = render_decision_question(request)
    if role in {"chancellor", "grand_marshal"}:
        conversation.append_decision(
            decision_id=decision_id,
            question_summary=question,
            assistant_reply=_advice(role, 1),
        )
        other = "grand_marshal" if role == "chancellor" else "chancellor"
        _append_internal(conversation, decision_id, question, other, "advice", _advice(other, 1))
        _append_internal(
            conversation, decision_id, question, "imperial_counsellor", "comment", _comment(1)
        )
        _append_internal(
            conversation, decision_id, question, "emperor", "final_decision", _final(1)
        )
    elif role == "imperial_counsellor":
        _append_current_court_messages(conversation, request, 1, role)
        conversation.append_decision(
            decision_id=decision_id,
            question_summary=question,
            assistant_reply=_comment(1),
        )
        _append_internal(
            conversation, decision_id, question, "emperor", "final_decision", _final(1)
        )
    else:
        _append_current_court_messages(conversation, request, 1, role)
        conversation.append_decision(
            decision_id=decision_id,
            question_summary=question,
            assistant_reply=_final(1),
        )


def _conversation(role: str, prior_request: DecisionRequest, second: bool) -> AgentConversation:
    conversation = AgentConversation(agent_id=f"a.{role}", window_turns=1)
    conversation.start_turn(1)
    if second:
        _append_previous_decision(conversation, prior_request, role)
    return conversation


def _render(
    role: str,
    request: DecisionRequest,
    prior_request: DecisionRequest,
    second: bool,
) -> tuple[tuple[LLMMessage, ...], object]:
    conversation = _conversation(role, prior_request, second)
    performance = random_officer_performance(request) if role == "imperial_counsellor" else None
    if role in {"imperial_counsellor", "emperor"}:
        _append_current_court_messages(conversation, request, 2 if second else 1, role)
    messages, warning = compose_prompt(
        conversation,
        request,
        pre_decision_context=performance,
        role_instruction=_ROLE_INSTRUCTIONS[role],
        segment3_prompt=(
            _COUNSELLOR_OUTPUT_REQUIREMENT
            if role == "imperial_counsellor"
            else _NORMAL_OUTPUT_REQUIREMENT
        ),
        post_decision_context=(
            _COUNSELLOR_SPECIAL_CONTEXT if role == "imperial_counsellor" else None
        ),
    )
    return messages, warning


def _write(
    buf: StringIO,
    label: str,
    role: str,
    messages: tuple[LLMMessage, ...],
    warning: object,
) -> None:
    buf.write(f"\n{_DIVIDER}\nSCENARIO {label}: 秦代 {role}\n{_DIVIDER}\n")
    for index, message in enumerate(messages, 1):
        buf.write(f"\n--- Message {index} [{message.role}] ---\n{message.content}\n")
    if warning is not None:
        buf.write(f"\n[ContextWarning] {warning!r}\n")


def main() -> None:
    buf = StringIO()
    with TemporaryDirectory() as directory:
        engine = _make_engine(directory)
        first_request = build_decision_request(engine, sequence=1)
        second_request = replace(build_decision_request(engine, sequence=2), complete_rounds=1)
        for label, role in zip("ABCD", _ROLES, strict=True):
            messages, warning = _render(role, first_request, first_request, second=False)
            _write(buf, label, role, messages, warning)
        for label, role in zip("EFGH", _ROLES, strict=True):
            messages, warning = _render(role, second_request, first_request, second=True)
            _write(buf, label, role, messages, warning)
    _REPORT_PATH.write_text(buf.getvalue(), encoding="utf-8")
    print(f"Wrote {_REPORT_PATH} ({len(buf.getvalue())} chars)")


if __name__ == "__main__":
    main()
