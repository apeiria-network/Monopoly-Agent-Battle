"""Render flat-ensemble (``flat_ensemble``) context-confirmation scenarios.

Run from the repository root:
    .venv/Scripts/python.exe tests/manual/render_flat_ensemble_prompt.py

Writes the full flat-ensemble context-confirmation report to
``tests/manual/render_flat_ensemble_prompt_report.txt`` (UTF-8) and echoes a
short summary to stdout.

The ``flat_ensemble`` agent is the "organized structure removed" control: four
sessions, each running the **same** baseline-level ``compose_prompt`` and
context system as the single-LLM baseline, aggregated by a weighted vote.  The
scenarios below render one session's composed messages and assert that they
behave exactly like the baseline (Stage 4D report):

  A – First decision of the game with several held Chance cards (no completed
      turns; no in-turn history).  Expected: messages =
      [system(段 1+2+3+固定输出约定), user(段 6-10)], byte-identical to the
      baseline, with no court artifacts (oracle / decision_maker) leaking.
  B – Fresh action turn after a prior completed turn whose history contains a
      card_drawn event for this very player.  Expected: segment 3 renders the
      player's OWN chance-card name (本人可见自己的机会卡名称), not the generic
      observer form — because each session's conversation uses the player id as
      the viewer, exactly like the baseline.
  C – Same action turn, second decision.  Expected: segment 5 replays this
      session's OWN first reply as an assistant message; the system-authored
      weighted-vote summary never enters any session's history, and no peer
      session's reply is ever visible.

Scenarios A–C are the human-review evidence; the four-session independence,
weighted-vote weights, fallback participation and full-game replay are covered
by ``tests/unit/test_flat_ensemble_agent.py`` and
``tests/integration/test_flat_ensemble_runner.py``.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.prompts import render_decision_question
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import GameEvent, TurnPhase
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
        "玩家 id 作为 viewer，因此可见性与 Baseline 一致。四会话并行调用、加权投票、"
        "兜底参与投票与端到端回放由 test_flat_ensemble_agent.py / "
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
            "每个会话以玩家 id 为 viewer，因此本人可见自己的机会卡名称、旁观者只见泛称，"
            "与 Baseline 完全相同（不像朝廷官员那样只看泛称）。见 B。",
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


def _event(event_type: str, **payload: object) -> GameEvent:
    return GameEvent(event_type=event_type, payload=payload)


def _flat_member_conversation() -> AgentConversation:
    """A flat-ensemble session conversation: viewer = player id (like baseline)."""
    return AgentConversation(agent_id="a", window_turns=1)


def _assert_no_court_artifacts(messages: tuple[LLMMessage, ...]) -> None:
    for message in messages:
        if "oracle" in message.content or "decision_maker" in message.content:
            raise AssertionError("flat-ensemble session prompt must not contain court artifacts")


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
        "新一轮行动回合 — 段 3 历史含本人抽卡，可见机会卡名称（与 Baseline 一致）",
    )
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=5)

    conversation = _flat_member_conversation()
    conversation.start_turn(1)
    # Player a draws a Chance card in turn 1; viewer == "a" must see its name.
    conversation.append_event(
        _event("card_drawn", player_id="a", card_id="chance-swap-property", deck="chance"),
        complete_round=0,
    )
    conversation.append_event(_event("turn_ended", player_id="a"), complete_round=0)
    conversation.start_turn(2)  # turn 1 → completed; turn 2 is the new action turn

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
        "同回合第二次决策 — 段 5 仅回放本人首次回复，无投票结果、无他人回复",
    )
    engine = _make_engine(directory)
    first_request = build_decision_request(engine, sequence=1)
    second_request = build_decision_request(engine, sequence=2)

    own_reply = '{"selected_option":{"option":"end_turn"},"reason":"选择候选操作 end_turn。"}'
    conversation = _flat_member_conversation()
    conversation.start_turn(1)
    conversation.append_event(_event("dice_rolled", player_id="a", dice=(2, 3)), complete_round=0)
    conversation.append_event(_event("player_moved", player_id="a", to=5), complete_round=0)
    conversation.append_decision(
        decision_id=first_request.decision_id,
        question_summary=render_decision_question(first_request),
        assistant_reply=own_reply,
    )

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


def main() -> None:
    buf = StringIO()
    _write_confirmation_checklist(buf)
    with TemporaryDirectory() as directory:
        scenario_a(buf, directory)
        scenario_b(buf, directory)
        scenario_c(buf, directory)
    _REPORT_PATH.write_text(buf.getvalue(), encoding="utf-8")
    print(f"Wrote {_REPORT_PATH} ({len(buf.getvalue())} chars)")


if __name__ == "__main__":
    main()
