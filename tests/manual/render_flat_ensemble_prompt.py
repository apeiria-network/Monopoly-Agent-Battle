"""Render flat-ensemble (``flat_ensemble``) context-confirmation scenarios.

Run from the repository root:
    .venv/Scripts/python.exe tests/manual/render_flat_ensemble_prompt.py

Writes the full flat-ensemble context-confirmation report to
``tests/manual/render_flat_ensemble_prompt_report.txt`` (UTF-8) and echoes a
short summary to stdout.

The ``flat_ensemble`` agent is the "organized structure removed" control: four
sessions, each running the **same** baseline-level ``compose_prompt`` and
context system as the single-LLM baseline, aggregated by a weighted vote.
The scenarios below advance the real ``GameEngine`` (``RollDice`` /
``command_from_option``), exactly like the court render scripts
(render_ming/qin/tang_decision_prompt.py), so the rendered prompts reflect
real post-decision state and real engine events:

  A – First decision of the game with several held Chance cards (no in-turn
      history).  Expected: messages = [system(段 1+2+3+固定输出约定),
      user(段 6-10)], byte-identical to the baseline, with no court artifacts
      (oracle / decision_maker) leaking.
  B – The engine really rolls dice and lands on a Chance square, so a
      ``card_drawn`` event is produced.  Expected: the session (viewer = player
      id) sees its OWN chance-card name in history (本人可见自己的机会卡名称),
      not the generic observer form — matching the baseline exactly.
  C – Within the same action turn the session already made one decision; the
      engine advances with ``command_from_option`` so the second request
      reflects real post-decision state.  Expected: segment 5 replays this
      session's OWN first reply as an assistant message; the system-authored
      weighted-vote summary never enters any session's history, and no peer
      session's reply is ever visible.
  D – Across turns: the engine runs two real action turns (a rolls, lands on a
      Chance square and draws a card, then ends; b acts and ends) before a's
      new action turn, so segment 4 accumulates real completed-turn history.
      Expected: segment 4 shows a's own chance-card name (self view) plus b's
      events, and segment 5 carries the current decision.

Scenarios A–C are the human-review evidence; the four-session independence,
weighted-vote weights, fallback participation and full-game replay are covered
by ``tests/unit/test_flat_ensemble_agent.py`` and
``tests/integration/test_flat_ensemble_runner.py``.
"""

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
from monopoly_agent_battle.decision.protocol import command_from_option, parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.commands import EndTurn, RollDice
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMMessage

_DIVIDER = "=" * 60
_REPORT_PATH = Path("tests/manual/render_flat_ensemble_prompt_report.txt")
_VOTE_REASON_MARK = "系统加权投票裁定"


def _write_confirmation_checklist(buf: StringIO) -> None:
    """Write the flat-ensemble owner-review checklist before the messages."""
    buf.write("FLAT-ENSEMBLE 上下文确认清单（供项目负责人逐项人工审核）\n")
    buf.write(f"{'=' * 60}\n\n")
    buf.write(
        "审阅范围：以下 messages 均由 compose_prompt() 生成，与单 LLM Baseline 走完全相同"
        "的 10 段装配路径（不传 role_instruction、不传 segment3_prompt）。每个会话使用"
        "玩家 id 作为 viewer，因此可见性与 Baseline 一致。所有场景均通过真实 GameEngine"
        "推进（RollDice / command_from_option），与朝廷渲染脚本同源。四会话并行调用、加权"
        "投票、兜底参与投票与端到端回放由 test_flat_ensemble_agent.py / "
        "test_flat_ensemble_runner.py 覆盖。\n\n"
    )
    items = (
        (
            "1. 与 Baseline 同构的 10 段",
            "段 1（角色与目标）+ 段 2（游戏规则）+ 段 3（固定 JSON 输出要求）仅在 system；"
            "段 4–10 属于动态 user；同回合既有模型回复严格作为 assistant。四会话均使用"
            "Baseline 默认提示词，无任何朝廷角色设定。见 A。",
        ),
        (
            "2. 可见性与 Baseline 一致",
            "每个会话以玩家 id 为 viewer，引擎真实掷骰抽卡后，本人可见自己的机会卡名称、"
            "旁观者只见泛称，与 Baseline 完全相同（不像朝廷官员那样只看泛称）。见 B。",
        ),
        (
            "3. 四会话互相不可见",
            "每个会话只记录自己的回复；投票结果不回传任何会话；任一会话的历史中不含"
            "其他会话的回复。见 C；互不可见由单元测试 test_each_session_records_only_"
            "own_reply_and_never_the_vote_result 覆盖。",
        ),
        (
            "4. 投票结果不进入上下文",
            "最终决策的 reason 为系统生成的投票摘要，但绝不写入任何会话的历史；各会话的"
            "自身 reason 留在 court_trace 供审计。见 C。",
        ),
        (
            "5. 无朝廷内部消息泄露",
            "flat_ensemble 无朝廷角色、无内部意见传递；任何会话的 Prompt 中均不出现 "
            "oracle、decision_maker 等朝廷专有字段。见 A。",
        ),
    )
    for title, detail in items:
        buf.write(f"- {title}：{detail}\n")
    buf.write(
        "\n报告中的完整 messages 是上述清单的人工审阅证据；自动化断言仅防止已确认 "
        "语义发生回归，不能替代负责人确认。报告中单列的 ContextWarning 是私有审计/运行时证据，"
        "不是 system、user 或 assistant 消息。\n"
    )


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
        buf.write(
            "\n"
            "--- 以下为私有审计/运行时信息，供负责人人工审阅，绝不进入 Agent 的 LLM 消息 ---\n"
            f"[ContextWarning] {warning!r}\n"
            "--- 各会话只向 LLMRequest.messages 传入上面的 system/user/assistant 消息 ---\n"
        )


def _make_engine(directory: str) -> GameEngine:
    """A minimal engine placed in ASSET_MANAGEMENT so a real request is buildable."""
    config = GameConfig(
        game_id="flat-prompt-inspection",
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


def _flat_member_conversation() -> AgentConversation:
    """A flat-ensemble session conversation: viewer = player id (like baseline)."""
    return AgentConversation(agent_id="a", window_turns=1)


def _assert_no_court_artifacts(messages: tuple[LLMMessage, ...]) -> None:
    for message in messages:
        if "oracle" in message.content or "decision_maker" in message.content:
            raise AssertionError("flat-ensemble session prompt must not contain court artifacts")


def _selected_option(request: DecisionRequest, option_id: str) -> dict[str, object]:
    """Build the selected_option JSON for ``option_id`` using its last legal target."""
    option = next(item for item in request.options if item.option_id == option_id)
    selected: dict[str, object] = {"option": option_id}
    if option.target is not None:
        values = option.target.legal_values[-1]
        if len(option.target.fields) == 1:
            selected["target"] = values[0]
        else:
            selected["target"] = dict(zip(option.target.fields, values, strict=True))
    return selected


def scenario_a(buf: StringIO, directory: str) -> None:
    _write_header(buf, "A", "首次决策 — 持有多张机会卡，无任何历史（段 4、段 5 均省略）")
    engine = _make_engine(directory)
    player = engine.state.players["a"]
    player.chance_cards.extend(["chance-jail", "chance-build"])

    request = build_decision_request(engine, sequence=1)
    conversation = _flat_member_conversation()
    messages, warning = compose_prompt(conversation, request)
    if [message.role for message in messages] != ["system", "user"]:
        raise AssertionError("Scenario A must begin with exactly system + dynamic user messages")
    system, dynamic_user = messages
    if "## 输出要求" not in system.content or system.content.index(
        "游戏规则"
    ) >= system.content.index("## 输出要求"):
        raise AssertionError(
            "Scenario A must place fixed output requirements after rules in system"
        )
    if "## 输出要求" in dynamic_user.content:
        raise AssertionError(
            "Scenario A dynamic user message must not repeat fixed output requirements"
        )
    if '"response_format"' not in dynamic_user.content:
        raise AssertionError(
            "Scenario A candidate JSON must retain option-specific response_format"
        )
    if "手中机会卡不得超过3张" not in system.content:
        raise AssertionError("Scenario A system rules must state the three-card Chance limit")
    _assert_no_court_artifacts(messages)
    # Byte-identical to the single-LLM baseline prompt for the same state: the
    # session uses the same compose_prompt path and the player id as viewer.
    baseline_messages, _ = compose_prompt(AgentConversation(agent_id="a", window_turns=1), request)
    if tuple(m.content for m in messages) != tuple(m.content for m in baseline_messages):
        raise AssertionError("Scenario A session prompt must be byte-identical to the baseline")
    _write_messages(buf, messages, warning)


def scenario_b(buf: StringIO, directory: str) -> None:
    _write_header(
        buf,
        "B",
        "引擎真实掷骰并落在机会格抽卡 — 本人可见机会卡名称（与 Baseline 一致）",
    )
    engine = _make_engine(directory)
    player = engine.state.players["a"]
    player.position = 3
    engine.state.chance_draw_pile = ["chance-waiver"]
    engine.state.turn_phase = TurnPhase.ROLLING
    dice = iter((1, 3))
    engine.random.randint = lambda _low, _high: next(dice)  # type: ignore[method-assign]

    conversation = _flat_member_conversation()
    conversation.start_turn(1)
    for event in engine.execute(RollDice("a")):
        conversation.append_event(event, complete_round=engine.state.complete_rounds)

    if len(player.chance_cards) != 2:
        raise AssertionError(
            "Scenario B must leave player a holding two Chance cards after the draw"
        )
    request = build_decision_request(engine, sequence=1)

    messages, warning = compose_prompt(conversation, request)
    text = "\n".join(message.content for message in messages)
    if "抽得机会卡「" not in text:
        raise AssertionError(
            "Scenario B session must see its own Chance-card name in history (self view)"
        )
    if "抽得一张机会卡" in text:
        raise AssertionError(
            "Scenario B session must not see the generic observer form for its own card"
        )
    _assert_no_court_artifacts(messages)
    _write_messages(buf, messages, warning)


def scenario_c(buf: StringIO, directory: str) -> None:
    _write_header(
        buf,
        "C",
        "同回合第二次决策 — 引擎推进后，段 5 仅回放本人首次回复，无投票结果",
    )
    engine = _make_engine(directory)
    first_request = build_decision_request(engine, sequence=1)
    mortgage = next(
        (option for option in first_request.options if option.command_type == "mortgage"),
        None,
    )
    if mortgage is None:
        raise AssertionError("Scenario C needs a mortgage option to advance the engine")
    own_reply = json.dumps(
        {
            "reason": "选择抵押地产以筹集现金。",
            "selected_option": _selected_option(first_request, mortgage.option_id),
        },
        ensure_ascii=False,
    )
    validation = parse_and_validate(own_reply, first_request)
    if not validation.valid or validation.option is None:
        raise AssertionError(f"Scenario C own reply must be valid: {validation.error}")

    conversation = _flat_member_conversation()
    conversation.start_turn(1)
    conversation.append_decision(
        decision_id=first_request.decision_id,
        question_summary=render_decision_question(first_request),
        assistant_reply=own_reply,
    )
    events = engine.execute(
        command_from_option(first_request, validation.option, validation.target)
    )
    for event in events:
        conversation.append_event(event, complete_round=engine.state.complete_rounds)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT

    second_request = build_decision_request(engine, sequence=2)
    messages, warning = compose_prompt(conversation, second_request)
    assistant_messages = [message.content for message in messages if message.role == "assistant"]
    if assistant_messages != [own_reply]:
        raise AssertionError(
            "Scenario C must replay this session's own first reply as the only assistant message"
        )
    text = "\n".join(message.content for message in messages)
    if _VOTE_REASON_MARK in text:
        raise AssertionError(
            "Scenario C must never expose the system-authored vote summary to a session"
        )
    _assert_no_court_artifacts(messages)
    _write_messages(buf, messages, warning)


def scenario_d(buf: StringIO, directory: str) -> None:
    _write_header(
        buf,
        "D",
        "跨回合真实推进 — 段 4 累积真实历史（含本人抽卡），段 5 当前决策",
    )
    engine = _make_engine(directory)
    player_a = engine.state.players["a"]
    player_a.position = 3
    engine.state.chance_draw_pile = ["chance-waiver"]
    engine.state.turn_phase = TurnPhase.ROLLING
    # Dice are rigged via the engine RNG: a (1,3)->7(Chance), b (1,2)->3(own),
    # a again (2,1)->10(just visiting). window_turns=2 keeps both completed
    # turns in the segment 4 history broadcast.
    dice = iter((1, 3, 1, 2, 2, 1))
    engine.random.randint = lambda _low, _high: next(dice)  # type: ignore[method-assign]

    conversation = AgentConversation(agent_id="a", window_turns=2)
    # Turn 1 (a, round 0): roll, land on a Chance square, draw a card, end turn.
    conversation.start_turn(1)
    turn1_roll = list(engine.execute(RollDice("a")))
    for event in turn1_roll:
        conversation.append_event(event, complete_round=engine.state.complete_rounds)
    if not any(event.event_type == "card_drawn" for event in turn1_roll):
        raise AssertionError("Scenario D turn 1 must draw a Chance card")
    for event in engine.execute(EndTurn("a")):
        conversation.append_event(event, complete_round=engine.state.complete_rounds)
    # Turn 2 (b, round 0): roll, move, end turn -> complete_rounds becomes 1.
    conversation.start_turn(2)
    for event in engine.execute(RollDice("b")):
        conversation.append_event(event, complete_round=engine.state.complete_rounds)
    for event in engine.execute(EndTurn("b")):
        conversation.append_event(event, complete_round=engine.state.complete_rounds)
    # Turn 3 (a, round 1): a's new action turn; roll then build a real request.
    conversation.start_turn(3)
    for event in engine.execute(RollDice("a")):
        conversation.append_event(event, complete_round=engine.state.complete_rounds)
    request = build_decision_request(engine, sequence=1)

    messages, warning = compose_prompt(conversation, request)
    text = "\n".join(message.content for message in messages)
    if "## 历史事件播报" not in text:
        raise AssertionError("Scenario D must populate the segment 4 history broadcast")
    if "抽得机会卡「" not in text:
        raise AssertionError(
            "Scenario D must show the player's own Chance-card name in segment 4 (self view)"
        )
    if "抽得一张机会卡" in text:
        raise AssertionError(
            "Scenario D must not show the generic observer form for the player's own card"
        )
    if "## 当前决策" not in text:
        raise AssertionError("Scenario D must carry the current decision in segment 5")
    _assert_no_court_artifacts(messages)
    _write_messages(buf, messages, warning)


def main() -> None:
    buf = StringIO()
    _write_confirmation_checklist(buf)
    with TemporaryDirectory() as directory:
        scenario_a(buf, directory)
        scenario_b(buf, directory)
        scenario_c(buf, directory)
        scenario_d(buf, directory)
    _REPORT_PATH.write_text(buf.getvalue(), encoding="utf-8")
    print(f"Wrote {_REPORT_PATH} ({len(buf.getvalue())} chars)")


if __name__ == "__main__":
    main()
