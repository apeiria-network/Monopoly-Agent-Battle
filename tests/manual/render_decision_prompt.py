"""Render Stage 4C decision-prompt scenarios for human review.

Run from the repository root:
    .venv/Scripts/python.exe tests/manual/render_decision_prompt.py

Writes the full transcript to tests/manual/render_decision_prompt_report.txt
(UTF-8) and echoes a short summary to stdout. Five scenarios exercise the
10-segment composer's key branches:

  A – First decision of the game (no completed turns; no in-turn history).
      Expected: messages = [system(段 1+2), user(段 5-10)].
  B – Fresh action turn after prior completed turns (window=1).
      Expected: segment 3 renders past events; segment 4 is still empty.
  C – Same action turn, second decision.
      Expected: segment 4 replays prior decision(s) with assistant + user interleaved.
  D – Segment 3 overflow triggers truncation and a warning.
      Expected: earliest events are dropped; ContextWarning emitted.
  E – Validation errors during a single decision → runner exhausts retries and
      falls back. ErrorEntry replays each bad reply + feedback within the
      current turn; DecisionEntry.assistant_reply carries the synthesized
      default-option JSON whose ``reason`` explains the fallback.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.context.validation_feedback import build_feedback
from monopoly_agent_battle.decision.protocol import parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import GameEvent, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMMessage

_DIVIDER = "=" * 60
_REPORT_PATH = Path("tests/manual/render_decision_prompt_report.txt")


def _write_header(buf: StringIO, label: str, title: str) -> None:
    buf.write(f"\n{_DIVIDER}\n")
    buf.write(f"SCENARIO {label}: {title}\n")
    buf.write(f"{_DIVIDER}\n")


def _write_messages(buf: StringIO, messages: tuple[LLMMessage, ...], warning: object) -> None:
    for i, msg in enumerate(messages, 1):
        buf.write(f"\n--- Message {i} [{msg.role}] ---\n")
        buf.write(msg.content)
        buf.write("\n")
    if warning is not None:
        buf.write(f"\n[ContextWarning] {warning!r}\n")


def _make_engine(directory: str) -> GameEngine:
    """A minimal engine placed in ASSET_MANAGEMENT so a real request is buildable."""
    config = GameConfig(
        game_id="prompt-inspection",
        experiment_id="manual-review",
        seed=1,
        players=(
            PlayerConfig(player_id="a", seat=1),
            PlayerConfig(player_id="b", seat=2),
        ),
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


def _event(event_type: str, **payload: object) -> GameEvent:
    return GameEvent(event_type=event_type, payload=payload)


def scenario_a(buf: StringIO, directory: str) -> None:
    _write_header(buf, "A", "首次决策 — 无任何历史（段 3、段 4 均省略）")
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=1)
    conversation = AgentConversation(agent_id="a", window_turns=1)
    messages, warning = compose_prompt(conversation, request)
    _write_messages(buf, messages, warning)


def scenario_b(buf: StringIO, directory: str) -> None:
    _write_header(buf, "B", "新一轮行动回合刚开始 — 段 3 累积历史，段 4 为空")
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=5)

    conversation = AgentConversation(agent_id="a", window_turns=1)
    # 玩家 a 的第 1 个行动回合（complete_rounds=0）：投骰、移动、结束。
    conversation.start_turn(1, segment3_budget_tokens=2000)
    for evt in (
        _event("dice_rolled", player_id="a", dice=(3, 4)),
        _event("player_moved", player_id="a", to=7),
    ):
        conversation.append_event(evt, complete_round=0)
    # a 是最后一名玩家前的座位；turn_ended 时轮次尚未推进，仍在第 0 轮。
    conversation.append_event(_event("turn_ended", player_id="a"), complete_round=0)

    # 玩家 b 的一个行动回合被观察到（假设 a 是首位，b 之后 complete_rounds 保持 0）。
    conversation.append_event(_event("turn_started", player_id="b"), complete_round=0)
    conversation.append_event(_event("dice_rolled", player_id="b", dice=(2, 5)), complete_round=0)
    conversation.append_event(_event("player_moved", player_id="b", to=14), complete_round=0)
    conversation.append_event(
        _event("property_purchased", player_id="b", position=14, price=140), complete_round=0
    )
    # b 若为末位玩家，其 turn_ended 之后 complete_rounds+1。
    conversation.append_event(_event("turn_ended", player_id="b"), complete_round=1)

    # 现在进入玩家 a 的第 2 个行动回合，位于第 1 完整轮次。
    conversation.start_turn(2, segment3_budget_tokens=2000)

    messages, warning = compose_prompt(conversation, request)
    _write_messages(buf, messages, warning)


def scenario_c(buf: StringIO, directory: str) -> None:
    _write_header(buf, "C", "同回合多次决策 — 段 4 出现 assistant/user 交替")
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=2)

    conversation = AgentConversation(agent_id="a", window_turns=1)
    conversation.start_turn(1, segment3_budget_tokens=2000)
    # 假设 a 已经在本回合内做过一次决策；期间发生了几个事件（本回合为第 0 完整轮次）。
    conversation.append_event(_event("dice_rolled", player_id="a", dice=(2, 3)), complete_round=0)
    conversation.append_event(_event("player_moved", player_id="a", to=5), complete_round=0)
    conversation.append_decision(
        decision_id="prompt-inspection-c-1",
        question_summary="## 当前决策\n现在是你的资产管理阶段。（示例快照）",
        assistant_reply=(
            '{"reason": "第一次先抵押第 1 格筹资。", '
            '"selected_option": {"option": "mortgage_property", "target": 1}}'
        ),
    )
    conversation.append_event(
        _event("property_mortgaged", player_id="a", position=1, amount=60), complete_round=0
    )

    messages, warning = compose_prompt(conversation, request)
    _write_messages(buf, messages, warning)


def scenario_d(buf: StringIO, directory: str) -> None:
    _write_header(buf, "D", "段 3 溢出 — 大量历史事件触发裁剪与警告")
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=5)

    conversation = AgentConversation(agent_id="a", window_turns=1)
    conversation.start_turn(1, segment3_budget_tokens=100)  # 初始预算充足
    # 塞入大量事件（远超后续极小预算），分散在多个完整轮次中。
    for i in range(20):
        conversation.append_event(
            _event("dice_rolled", player_id="a", dice=(3, 4)), complete_round=i // 4
        )
        conversation.append_event(
            _event("player_moved", player_id="a", to=7 + i % 3), complete_round=i // 4
        )

    # 用极小预算触发全部裁剪与警告。
    conversation.start_turn(2, segment3_budget_tokens=5)
    messages, warning = compose_prompt(conversation, request)
    _write_messages(buf, messages, warning)


def scenario_e(buf: StringIO, directory: str) -> None:
    _write_header(
        buf,
        "E",
        "校验失败错误记录 — 单次决策内两次出错后触发回退，回退 assistant 用系统默认 JSON",
    )
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=1)
    from monopoly_agent_battle.decision.prompts import render_decision_question

    conversation = AgentConversation(agent_id="a", window_turns=1)
    conversation.start_turn(1, segment3_budget_tokens=2000)

    bad_reply_1 = '{"selected_option": {"option": "not-a-real-option"}, "reason": "尝试1"}'
    validation_1 = parse_and_validate(bad_reply_1, request)
    conversation.append_error(
        decision_id=request.decision_id,
        question_summary=render_decision_question(request),
        bad_reply=bad_reply_1,
        feedback_text=build_feedback(validation_1, request),
    )
    bad_reply_2 = '{"selected_option": {"option": "mortgage"}, "reason": "尝试2"}'
    validation_2 = parse_and_validate(bad_reply_2, request)
    conversation.append_error(
        decision_id=request.decision_id,
        question_summary=render_decision_question(request),
        bad_reply=bad_reply_2,
        feedback_text=build_feedback(validation_2, request),
    )

    conversation.append_decision(
        decision_id=request.decision_id,
        question_summary=render_decision_question(request),
        assistant_reply=(
            '{"selected_option": {"option": "end_turn"}, '
            '"reason": "多次重试仍未给出合法回复，自动选择系统默认选项。"}'
        ),
    )

    messages, warning = compose_prompt(conversation, request)
    _write_messages(buf, messages, warning)


def main() -> None:
    buf = StringIO()
    with TemporaryDirectory() as directory:
        scenario_a(buf, directory)
        scenario_b(buf, directory)
        scenario_c(buf, directory)
        scenario_d(buf, directory)
        scenario_e(buf, directory)
    _REPORT_PATH.write_text(buf.getvalue(), encoding="utf-8")
    print(f"Wrote {_REPORT_PATH} ({len(buf.getvalue())} chars)")


if __name__ == "__main__":
    main()
