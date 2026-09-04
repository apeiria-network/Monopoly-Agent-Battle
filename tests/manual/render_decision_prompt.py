"""Render Stage 4D Baseline context-confirmation scenarios for human review.

Run from the repository root:
    .venv/Scripts/python.exe tests/manual/render_decision_prompt.py

Writes the full Baseline context-confirmation report to
``tests/manual/render_decision_prompt_report.txt`` (UTF-8) and echoes a short
summary to stdout. The report begins with the review checklist, followed by
seven scenarios that exercise the actual Stage 4C composer used by
``BaselineAgent``:

  A – First decision of the game with several held Chance cards (no completed
      turns; no in-turn history). Shows that usable cards are independent
      candidates while a card's own targets remain folded. Expected: messages =
      [system(段 1+2+3+固定输出约定), user(段 6-10)].
  B – Fresh action turn after prior completed turns (window=1).
      Expected: segment 4 renders past events; segment 5 is still empty.
  C – Same action turn, second decision.
      Expected: segment 5 replays prior decision(s) with assistant + user interleaved.
  D – Segment 4 overflow triggers truncation and a warning.
      Expected: earliest events are dropped; ContextWarning emitted.
  E – Validation errors during a single decision → runner exhausts retries and
      falls back to ``end_turn``. The real fallback enters ``FORCED_DISCARD``
      after A has drawn a fifth Chance card, so the final message asks the
      still-current player A to choose one of those cards to discard.
  F – Private Court-AI message in the current action turn.
      Expected: the message is rendered as user context with system-trusted
      ``decision_maker`` and ``content_type`` metadata, never as public history.
  G – Emperor's same action turn with two decisions.
      Expected: the first Court-AI consultation and the Emperor's own reply are
      replayed before the second decision's private Court-AI messages.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.broadcast import render_event
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.context.token_guard import estimate_tokens
from monopoly_agent_battle.context.validation_feedback import build_feedback
from monopoly_agent_battle.decision.models import DecisionKind
from monopoly_agent_battle.decision.prompts import render_decision_question
from monopoly_agent_battle.decision.protocol import parse_and_validate
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.commands import EndTurn, RollDice
from monopoly_agent_battle.domain.models import GameEvent, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMMessage

_DIVIDER = "=" * 60
_REPORT_PATH = Path("tests/manual/render_decision_prompt_report.txt")


def _write_confirmation_checklist(buf: StringIO) -> None:
    """Write the Stage 4D owner-review checklist before the concrete messages.

    The items describe the production Baseline path. Scenario labels identify
    where the reviewer can inspect rendered evidence in this report; the
    companion integration test verifies the four-player runtime wiring.
    """
    buf.write("STAGE 4D BASELINE 上下文确认清单（供项目负责人逐项人工审核）\n")
    buf.write(f"{'=' * 60}\n\n")
    buf.write(
        "审阅范围：以下 messages 均由 compose_prompt() 生成；BaselineAgent 在实际 LLM "
        "调用中原样传入 LLMRequest.messages。四玩家端到端装配、审计与回放由 "
        "tests/integration/test_llm_runner.py 覆盖。\n\n"
    )
    items = (
        (
            "1. 10 段与消息角色",
            "段 1（角色与目标）+ 段 2（游戏规则）+ 段 3（固定 JSON 输出要求）仅在 "
            "system；段 4–10 属于动态 user；同回合既有模型回复严格作为 assistant。"
            "见 A、C、E、F、G。",
        ),
        (
            "2. 每次实际决策字段与候选格式",
            "动态末尾包含最新的你的状态、其他玩家状态、棋盘状态、当前决策和仅由引擎 "
            "给出的合法候选；每项候选保留 response_format。决策/运行审计 ID、牌堆顺序、"
            "RNG 与冻结命令参数不在 Prompt 中。见 A。",
        ),
        (
            "3. 私有会话与窗口",
            "每玩家一条独立 AgentConversation；window_turns=1 时，段 4 在本玩家新行动回合 "
            "开始一次性建立并在该回合内保持不变，段 5 只保留当前行动回合。见 B、C；"
            "四玩家独立实例见端到端集成测试。",
        ),
        (
            "4. 历史播报与可见性",
            "段 4 只使用 4B 白名单的 viewer-scoped 固定中文句式；段 5 的朝廷内部意见 "
            "仅由授权 CourtAgent 私有写入，以 user 上下文重放，不进入公开历史或其他玩家会话。"
            "本人可见自己的机会卡名称，旁观者对机会卡只见泛称；社区基金卡公开，"
            "抢夺选择是协议层单次受控例外。见 B、C、F；播报细节由 "
            "render_history_broadcast.py 已验收。",
        ),
        (
            "5. 500-token 历史上限",
            "仅段 4 独立严格限制为 500 估算 token；从最早完整播报事件开始删除，规则、"
            "当前状态、候选和段 5 均不截断。裁剪警告仅供 runtime 审计。见 D。",
        ),
        (
            "6. 校验反馈生命周期",
            "非法输出仅在出错 Agent 的当前行动回合内以 user(问题) → assistant(错误回复) → "
            "user(Error: …) 重放；下一行动回合的段 4 跳过 ErrorEntry。重试耗尽后，后续 "
            "上下文看到合成的默认候选 assistant 回复及其原因。见 E。",
        ),
        (
            "7. 运行时基础设施隔离",
            "断线、超时、重连次数、segment3_overflow 和回退机制记录到 runtime.jsonl / "
            "result.json，但绝不进入 Agent message；实际执行动作产生的游戏事件照常播报。"
            "见 E 与端到端集成测试。",
        ),
        (
            "8. 冻结与审计边界",
            "window_turns、sentence_template_version、validation_retries 和 ModelProfile 随 "
            "GameConfig/config_hash 冻结；每次调用的模型、用量、耗时和错误写入 "
            "llm_calls.jsonl，决策与回放产物可追溯。见 A–F 和端到端集成测试。",
        ),
    )
    for title, detail in items:
        buf.write(f"- {title}：{detail}\n")
    buf.write(
        "\n报告中的完整 messages 是上述清单的人工审阅证据；自动化断言仅防止已确认 "
        "语义发生回归，不能替代负责人确认。报告中单列的 ContextWarning 是私有审计/运行时证据，"
        "不是 system、user 或 assistant 消息；无需也不得在 Prompt 中再次实现其隔离。\n"
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
            "--- BaselineAgent 只向 LLMRequest.messages 传入上面的 system/user/assistant 消息 ---\n"
        )


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
    _write_header(buf, "A", "首次决策 — 持有多张机会卡，无任何历史（段 4、段 5 均省略）")
    engine = _make_engine(directory)
    player = engine.state.players["a"]
    player.chance_cards.extend(["chance-jail", "chance-build"])

    request = build_decision_request(engine, sequence=1)
    chance_options = [
        option for option in request.options if option.command_type == "use_chance_card"
    ]
    by_card_id = {cast(str, option.parameters["card_id"]): option for option in chance_options}
    if list(by_card_id) != ["chance-swap-property", "chance-jail", "chance-build"]:
        raise AssertionError("Scenario A must preserve held Chance-card candidate order")
    if by_card_id["chance-jail"].target is None:
        raise AssertionError("Scenario A jail card must expose its legal player target")
    if by_card_id["chance-build"].target is None or by_card_id[
        "chance-build"
    ].target.legal_values != ((1,),):
        raise AssertionError("Scenario A build card must fold its property targets")

    conversation = AgentConversation(agent_id="a", window_turns=1)
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
    _write_messages(buf, messages, warning)


def scenario_b(buf: StringIO, directory: str) -> None:
    _write_header(buf, "B", "新一轮行动回合刚开始 — 段 4 累积历史，段 5 为空")
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=5)

    conversation = AgentConversation(agent_id="a", window_turns=1)
    # 玩家 a 的第 1 个行动回合（complete_rounds=0）：投骰、移动、结束。
    conversation.start_turn(1)
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
    conversation.start_turn(2)

    messages, warning = compose_prompt(conversation, request)
    _write_messages(buf, messages, warning)


def scenario_c(buf: StringIO, directory: str) -> None:
    _write_header(buf, "C", "同回合多次决策 — 段 5 出现 assistant/user 交替")
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=2)

    conversation = AgentConversation(agent_id="a", window_turns=1)
    conversation.start_turn(1)
    # 假设 a 已经在本回合内做过一次决策；期间发生了几个事件（本回合为第 0 完整轮次）。
    conversation.append_event(_event("dice_rolled", player_id="a", dice=(2, 3)), complete_round=0)
    conversation.append_event(_event("player_moved", player_id="a", to=5), complete_round=0)
    conversation.append_decision(
        decision_id="prompt-inspection-c-1",
        question_summary=render_decision_question(request),
        assistant_reply=(
            '{"reason": "第一次先抵押第 1 格筹资。", '
            '"selected_option": {"option": "mortgage_property", "target": 1}}'
        ),
    )
    conversation.append_event(
        _event("property_mortgaged", player_id="a", position=1, amount=60), complete_round=0
    )

    messages, warning = compose_prompt(conversation, request)
    prior_user = messages[-3]
    first_event = "[第0轮] 玩家a掷出2+3=5点。"
    second_event = "[第0轮] 玩家a移动到第5格（Reading Railroad）。"
    if f"{first_event}\n{second_event}" not in prior_user.content:
        raise AssertionError("Scenario C adjacent event broadcasts must use one newline")
    if f"{first_event}\n\n{second_event}" in prior_user.content:
        raise AssertionError("Scenario C must not insert a blank line between adjacent events")
    if f"{second_event}\n\n## 决策" not in prior_user.content:
        raise AssertionError("Scenario C must preserve a semantic block break before the decision")
    _write_messages(buf, messages, warning)


def scenario_d(buf: StringIO, directory: str) -> None:
    _write_header(buf, "D", "段 4 溢出 — 真实回合历史触发裁剪与警告")
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=5)

    conversation = AgentConversation(agent_id="a", window_turns=1)
    conversation.start_turn(1)
    history_events: list[tuple[int, GameEvent]] = []

    def append_round(round_number: int, *events: GameEvent) -> None:
        for event in events:
            history_events.append((round_number, event))
            conversation.append_event(event, complete_round=round_number)

    # 这是供上下文人工审阅的手工历史夹具，而非从上面的最小引擎快照重放。
    # 它使用引擎真实会产生的白名单事件和 payload，保留两个玩家连续行动的因果顺序。
    append_round(
        0,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(1, 2)),
        _event("player_moved", player_id="a", to=3),
        _event("property_purchased", player_id="a", position=3, price=60),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(3, 4)),
        _event("player_moved", player_id="b", to=7),
        _event("card_drawn", player_id="b", card_id="chance-waiver", deck="chance"),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        1,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(1, 5)),
        _event("player_moved", player_id="a", to=9),
        _event("property_purchased", player_id="a", position=9, price=120),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(2, 3)),
        _event("player_moved", player_id="b", to=12),
        _event("property_purchased", player_id="b", position=12, price=140),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        2,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(1, 2)),
        _event("player_moved", player_id="a", to=12),
        _event(
            "payment_made",
            payer_id="a",
            recipient_id="b",
            amount=12,
            reason="rent",
        ),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(4, 5)),
        _event("player_moved", player_id="b", to=21),
        _event("property_purchased", player_id="b", position=21, price=220),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        3,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(5, 6)),
        _event("player_moved", player_id="a", to=23),
        _event("property_purchased", player_id="a", position=23, price=220),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(5, 6)),
        _event("player_moved", player_id="b", to=32),
        _event("property_purchased", player_id="b", position=32, price=300),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        4,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(4, 5)),
        _event("player_moved", player_id="a", to=32),
        _event("payment_made", payer_id="a", recipient_id="b", amount=26, reason="rent"),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(4, 5)),
        _event("player_moved", player_id="b", to=1),
        _event("go_salary_collected", player_id="b", amount=200),
        _event("property_purchased", player_id="b", position=1, price=60),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        5,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(3, 4)),
        _event("player_moved", player_id="a", to=39),
        _event("property_purchased", player_id="a", position=39, price=400),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(1, 3)),
        _event("player_moved", player_id="b", to=5),
        _event("property_purchased", player_id="b", position=5, price=200),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        6,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(1, 2)),
        _event("player_moved", player_id="a", to=2),
        _event("go_salary_collected", player_id="a", amount=200),
        _event("card_drawn", player_id="a", card_id="community-refund", deck="community_chest"),
        _event("cash_received", player_id="a", amount=20, reason="community-refund"),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(2, 3)),
        _event("player_moved", player_id="b", to=10),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        7,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(1, 2)),
        _event("player_moved", player_id="a", to=5),
        _event("payment_made", payer_id="a", recipient_id="b", amount=25, reason="rent"),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(3, 4)),
        _event("player_moved", player_id="b", to=17),
        _event("card_drawn", player_id="b", card_id="community-holiday", deck="community_chest"),
        _event("cash_received", player_id="b", amount=100, reason="community-holiday"),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        8,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(1, 3)),
        _event("player_moved", player_id="a", to=9),
        _event("building_added", player_id="a", position=9, cost=50),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(3, 4)),
        _event("player_moved", player_id="b", to=24),
        _event("property_purchased", player_id="b", position=24, price=240),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        9,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(1, 2)),
        _event("player_moved", player_id="a", to=12),
        _event(
            "payment_made",
            payer_id="a",
            recipient_id="b",
            amount=12,
            reason="rent",
        ),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(1, 2)),
        _event("player_moved", player_id="b", to=27),
        _event("property_purchased", player_id="b", position=27, price=260),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        10,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(2, 3)),
        _event("player_moved", player_id="a", to=17),
        _event("card_drawn", player_id="a", card_id="community-stock", deck="community_chest"),
        _event("cash_received", player_id="a", amount=50, reason="community-stock"),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(2, 3)),
        _event("player_moved", player_id="b", to=32),
        _event("building_added", player_id="b", position=32, cost=200),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        11,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(2, 3)),
        _event("player_moved", player_id="a", to=22),
        _event("card_drawn", player_id="a", card_id="chance-build", deck="chance"),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(3, 4)),
        _event("player_moved", player_id="b", to=39),
        _event("payment_made", payer_id="b", recipient_id="a", amount=50, reason="rent"),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        12,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(2, 3)),
        _event("player_moved", player_id="a", to=27),
        _event("payment_made", payer_id="a", recipient_id="b", amount=22, reason="rent"),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(3, 4)),
        _event("player_moved", player_id="b", to=6),
        _event("go_salary_collected", player_id="b", amount=200),
        _event("property_purchased", player_id="b", position=6, price=100),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        13,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(2, 3)),
        _event("player_moved", player_id="a", to=32),
        _event("payment_made", payer_id="a", recipient_id="b", amount=130, reason="rent"),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(1, 2)),
        _event("player_moved", player_id="b", to=9),
        _event("payment_made", payer_id="b", recipient_id="a", amount=40, reason="rent"),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        14,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(4, 5)),
        _event("player_moved", player_id="a", to=1),
        _event("go_salary_collected", player_id="a", amount=200),
        _event("payment_made", payer_id="a", recipient_id="b", amount=2, reason="rent"),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(2, 3)),
        _event("player_moved", player_id="b", to=14),
        _event("property_purchased", player_id="b", position=14, price=140),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        15,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(2, 3)),
        _event("player_moved", player_id="a", to=6),
        _event("payment_made", payer_id="a", recipient_id="b", amount=6, reason="rent"),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(4, 5)),
        _event("player_moved", player_id="b", to=23),
        _event("payment_made", payer_id="b", recipient_id="a", amount=18, reason="rent"),
        _event("turn_ended", player_id="b"),
    )
    append_round(
        16,
        _event("turn_started", player_id="a"),
        _event("dice_rolled", player_id="a", dice=(1, 2)),
        _event("player_moved", player_id="a", to=9),
        _event("building_added", player_id="a", position=9, cost=50),
        _event("turn_ended", player_id="a"),
        _event("turn_started", player_id="b"),
        _event("dice_rolled", player_id="b", dice=(2, 3)),
        _event("player_moved", player_id="b", to=28),
        _event("property_purchased", player_id="b", position=28, price=150),
        _event("turn_ended", player_id="b"),
    )

    full_history = tuple(
        f"[第{complete_round}轮] {sentence}"
        for complete_round, event in history_events
        if (sentence := render_event(event, conversation.agent_id)) is not None
    )
    if estimate_tokens("\n".join(full_history)) <= 500:
        raise AssertionError("Scenario D complete history must exceed the segment-4 token cap")

    # Start a new action turn to rebuild the capped history cache and emit its warning.
    conversation.start_turn(2)
    messages, warning = compose_prompt(conversation, request)
    retained_history = conversation.segment3_sentences
    if warning is None or warning.kind != "segment3_overflow":
        raise AssertionError("Scenario D must emit a segment-4 overflow warning")
    if estimate_tokens("\n".join(retained_history)) > 500:
        raise AssertionError("Scenario D segment 4 must stay within the fixed 500-token cap")
    if retained_history[0] == full_history[0]:
        raise AssertionError("Scenario D must drop earliest historical events")
    if retained_history[-1] != full_history[-1]:
        raise AssertionError("Scenario D must retain the latest historical event")
    if not any("玩家a" in sentence for sentence in retained_history) or not any(
        "玩家b" in sentence for sentence in retained_history
    ):
        raise AssertionError("Scenario D retained history must include both players")
    if not any("开始行动回合" in sentence for sentence in retained_history) or not any(
        "结束行动回合" in sentence for sentence in retained_history
    ):
        raise AssertionError("Scenario D retained history must preserve turn boundaries")
    _write_messages(buf, messages, warning)


def scenario_f(buf: StringIO, directory: str) -> None:
    _write_header(
        buf,
        "F",
        "朝廷内部意见 — 私有 user 上下文中的系统可信身份元数据",
    )
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=1)
    conversation = AgentConversation(agent_id="a", window_turns=1)
    conversation.start_turn(1)
    decision_id = "prompt-inspection-f-1"
    question_summary = render_decision_question(request)
    internal_messages = (
        (
            "prompt-inspection-f-1:chancellor:advice",
            "chancellor",
            "advice",
            '{"reason":"本回合采取行动无未来收益，宜按兵不动",'
            '"selected_option":{"option":"end_turn"},'
            '"decision_maker":"forged","content_type":"forged"}',
        ),
        (
            "prompt-inspection-f-1:grand_marshal:advice",
            "grand_marshal",
            "advice",
            '{"reason":"可以考虑执行换地，对方地产的租金收益更高",'
            '"selected_option":{"option":"use_chance_card-chance-swap-property",'
            '"target":{"swap_in_position":3,"swap_out_position":1}}}',
        ),
        (
            "prompt-inspection-f-1:imperial_counsellor:comment:agree",
            "imperial_counsellor",
            "comment",
            '{"reason":"我赞成丞相的意见",'
            '"selected_option":{"option":"agree","target":"chancellor"}}',
        ),
        (
            "prompt-inspection-f-1:imperial_counsellor:comment:disagree",
            "imperial_counsellor",
            "comment",
            '{"reason":"我不赞成太尉的意见，后面可能还有机会使用这张卡",'
            '"selected_option":{"option":"disagree","target":"grand_marshal"}}',
        ),
    )
    for internal_decision_id, decision_maker, content_type, raw_content in internal_messages:
        if not conversation.append_internal_decision(
            internal_decision_id=internal_decision_id,
            decision_id=decision_id,
            question_summary=question_summary,
            decision_maker=decision_maker,
            content_type=content_type,
            raw_content=raw_content,
        ):
            raise AssertionError("Scenario F must retain each distinct internal opinion")

    messages, warning = compose_prompt(conversation, request)
    roles = [message.role for message in messages]
    if roles != ["system", "user"]:
        raise AssertionError("Scenario F must render private opinions as user context")
    content = messages[-1].content
    history, _current_situation = content.split("\n\n## 当前局面", 1)
    replay_question = render_decision_question(request).replace("## 当前决策", "## 决策", 1)
    if not history.startswith(replay_question):
        raise AssertionError("Scenario F must replay the engine-rendered historical decision")
    if "## 当前决策" in history:
        raise AssertionError("Scenario F history must not call a past decision current")
    if "## 朝廷内部消息" in content:
        raise AssertionError("Scenario F must not add an internal-message heading")
    expected_metadata = (
        ('"decision_maker":"chancellor"', '"content_type":"advice"'),
        ('"decision_maker":"grand_marshal"', '"content_type":"advice"'),
        ('"decision_maker":"imperial_counsellor"', '"content_type":"comment"'),
    )
    if any(
        decision_maker not in content or content_type not in content
        for decision_maker, content_type in expected_metadata
    ):
        raise AssertionError("Scenario F must inject trusted role metadata for every opinion")
    if content.count('"decision_maker":"imperial_counsellor"') != 2:
        raise AssertionError("Scenario F must retain both same-role comments with distinct IDs")
    if '"decision_maker":"forged"' in content or '"content_type":"forged"' in content:
        raise AssertionError("Scenario F must override model-supplied metadata")
    if '"selected_option":{"option":"use_chance_card-chance-swap-property"' not in content:
        raise AssertionError(
            "Scenario F must identify the swap-card candidate by its real option ID"
        )
    if '"target":{"swap_in_position":3,"swap_out_position":1}' not in content:
        raise AssertionError("Scenario F must retain the swap-card target shape")
    if '"selected_option":{"option":"end_turn","target"' in content:
        raise AssertionError("Scenario F must not attach a target to end_turn")
    if '"result"' in content:
        raise AssertionError("Scenario F must use selected_option rather than obsolete result")
    if "历史事件播报" in content:
        raise AssertionError("Scenario F must not turn private opinions into public history")
    _write_messages(buf, messages, warning)


def scenario_g(buf: StringIO, directory: str) -> None:
    _write_header(
        buf,
        "G",
        "皇帝同一行动回合内两次决策 — 两轮朝廷意见与皇帝回复按顺序重放",
    )
    engine = _make_engine(directory)
    first_request = build_decision_request(engine, sequence=1)
    second_request = build_decision_request(engine, sequence=2)
    conversation = AgentConversation(agent_id="emperor", window_turns=1)
    conversation.start_turn(1)

    first_decision_id = "prompt-inspection-g-1"
    second_decision_id = "prompt-inspection-g-2"
    first_question = render_decision_question(first_request)
    second_question = render_decision_question(second_request)
    first_internal_messages = (
        (
            "prompt-inspection-g-1:chancellor:advice",
            "chancellor",
            "advice",
            '{"reason":"本回合采取行动无未来收益，宜按兵不动",'
            '"selected_option":{"option":"end_turn"},'
            '"decision_maker":"chancellor","content_type":"advice"}',
        ),
        (
            "prompt-inspection-g-1:grand_marshal:advice",
            "grand_marshal",
            "advice",
            '{"reason":"可以考虑执行换地，对方地产的租金收益更高",'
            '"selected_option":{"option":"use_chance_card-chance-swap-property",'
            '"target":{"swap_in_position":3,"swap_out_position":1}},'
            '"decision_maker":"grand_marshal","content_type":"advice"}',
        ),
        (
            "prompt-inspection-g-1:imperial_counsellor:comment:agree",
            "imperial_counsellor",
            "comment",
            '{"reason":"我赞成丞相的意见",'
            '"selected_option":{"option":"agree","target":"chancellor"},'
            '"decision_maker":"imperial_counsellor","content_type":"comment"}',
        ),
        (
            "prompt-inspection-g-1:imperial_counsellor:comment:disagree",
            "imperial_counsellor",
            "comment",
            '{"reason":"我不赞成太尉的意见，后面可能还有机会使用这张卡",'
            '"selected_option":{"option":"disagree","target":"grand_marshal"},'
            '"decision_maker":"imperial_counsellor","content_type":"comment"}',
        ),
    )
    for internal_decision_id, decision_maker, content_type, raw_content in first_internal_messages:
        if not conversation.append_internal_decision(
            internal_decision_id=internal_decision_id,
            decision_id=first_decision_id,
            question_summary=first_question,
            decision_maker=decision_maker,
            content_type=content_type,
            raw_content=raw_content,
        ):
            raise AssertionError("Scenario G must retain the first decision's court opinions")

    first_emperor_reply = (
        '{"reason":"我们应该在开局就拿下主动权。",'
        '"selected_option":{"option":"use_chance_card-chance-swap-property",'
        '"target":{"swap_in_position":3,"swap_out_position":1}}}'
    )
    conversation.append_decision(
        decision_id=first_decision_id,
        question_summary=first_question,
        assistant_reply=first_emperor_reply,
    )

    second_internal_messages = (
        (
            "prompt-inspection-g-2:chancellor:advice",
            "chancellor",
            "advice",
            '{"reason":"没有出售的必要",'
            '"selected_option":{"option":"end_turn"},'
            '"decision_maker":"chancellor","content_type":"advice"}',
        ),
        (
            "prompt-inspection-g-2:grand_marshal:advice",
            "grand_marshal",
            "advice",
            '{"reason":"没有出售的必要",'
            '"selected_option":{"option":"end_turn",'
            '"target":{"swap_in_position":3,"swap_out_position":1}},'
            '"decision_maker":"grand_marshal","content_type":"advice"}',
        ),
        (
            "prompt-inspection-g-2:imperial_counsellor:comment:agree-chancellor",
            "imperial_counsellor",
            "comment",
            '{"reason":"我赞成丞相的意见",'
            '"selected_option":{"option":"agree","target":"chancellor"},'
            '"decision_maker":"imperial_counsellor","content_type":"comment"}',
        ),
        (
            "prompt-inspection-g-2:imperial_counsellor:comment:agree-grand-marshal",
            "imperial_counsellor",
            "comment",
            '{"reason":"我赞成太尉的意见",'
            '"selected_option":{"option":"agree","target":"grand_marshal"},'
            '"decision_maker":"imperial_counsellor","content_type":"comment"}',
        ),
    )
    for internal_decision_id, decision_maker, content_type, raw_content in second_internal_messages:
        if not conversation.append_internal_decision(
            internal_decision_id=internal_decision_id,
            decision_id=second_decision_id,
            question_summary=second_question,
            decision_maker=decision_maker,
            content_type=content_type,
            raw_content=raw_content,
        ):
            raise AssertionError("Scenario G must retain the second decision's court opinions")

    messages, warning = compose_prompt(conversation, second_request)
    roles = [message.role for message in messages]
    if roles != ["system", "user", "assistant", "user"]:
        raise AssertionError("Scenario G must replay two decisions as user/assistant/user")
    first_history = messages[1].content
    second_history_and_current = messages[-1].content
    first_replay_question = first_question.replace("## 当前决策", "## 决策", 1)
    second_replay_question = second_question.replace("## 当前决策", "## 决策", 1)
    if not first_history.startswith(first_replay_question):
        raise AssertionError("Scenario G must begin with the first historical decision")
    if messages[2].content != first_emperor_reply:
        raise AssertionError("Scenario G must replay the Emperor's first reply as assistant")
    if not second_history_and_current.startswith(second_replay_question):
        raise AssertionError("Scenario G must begin its final user message with decision two")
    if "## 当前决策" not in second_history_and_current:
        raise AssertionError("Scenario G must end with the second current decision")
    history = first_history + "\n" + second_history_and_current
    if history.count('"decision_maker":"chancellor"') != 2:
        raise AssertionError("Scenario G must retain the Chancellor's advice twice")
    if history.count('"decision_maker":"grand_marshal"') != 2:
        raise AssertionError("Scenario G must retain the Grand Marshal's advice twice")
    if history.count('"decision_maker":"imperial_counsellor"') != 4:
        raise AssertionError("Scenario G must retain all four counsellor comments")
    if history.count('"selected_option":{"option":"use_chance_card-chance-swap-property"') != 1:
        raise AssertionError("Scenario G must retain the first swap-card recommendation")
    if (
        '"selected_option":{"option":"end_turn","target":{"swap_in_position":3,"swap_out_position":1}}'
        not in history
    ):
        raise AssertionError("Scenario G must retain the second Grand Marshal target example")
    _write_messages(buf, messages, warning)


def scenario_e(buf: StringIO, directory: str) -> None:
    _write_header(
        buf,
        "E",
        "校验失败后默认结束回合 — A 抽到第 4 张机会卡后必须弃置",
    )
    engine = _make_engine(directory)
    player = engine.state.players["a"]
    player.position = 3
    player.chance_cards = [
        "chance-build",
        "chance-buy",
        "chance-jail",
    ]
    engine.state.chance_draw_pile = ["chance-waiver"]
    engine.state.turn_phase = TurnPhase.ROLLING
    dice = iter((1, 3))
    engine.random.randint = lambda _low, _high: next(dice)  # type: ignore[method-assign]

    conversation = AgentConversation(agent_id="a", window_turns=1)
    conversation.start_turn(1)
    for event in engine.execute(RollDice("a")):
        conversation.append_event(event, complete_round=engine.state.complete_rounds)

    if len(player.chance_cards) != 4:
        raise AssertionError("Scenario E requires A to hold four chance cards after draw")
    request = build_decision_request(engine, sequence=1)

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

    fallback_reply = (
        '{"selected_option": {"option": "end_turn"}, '
        '"reason": "多次重试仍未给出合法回复，自动选择系统默认选项。"}'
    )
    conversation.append_decision(
        decision_id=request.decision_id,
        question_summary=render_decision_question(request),
        assistant_reply=fallback_reply,
    )
    for event in engine.execute(EndTurn("a")):
        conversation.append_event(event, complete_round=engine.state.complete_rounds)

    phase = cast(TurnPhase, engine.state.turn_phase)
    if engine.state.current_player_id != "a" or phase is not TurnPhase.FORCED_DISCARD:
        raise AssertionError("Scenario E fallback must leave A in forced discard")
    forced_discard_request = build_decision_request(engine, sequence=2)
    if forced_discard_request.kind is not DecisionKind.FORCED_DISCARD:
        raise AssertionError("Scenario E must render a forced-discard request")
    discard_options = [
        option
        for option in forced_discard_request.options
        if option.command_type == "discard_chance_card"
    ]
    expected_card_ids = tuple(player.chance_cards)
    if [option.parameters for option in discard_options] != [
        {"card_id": card_id} for card_id in expected_card_ids
    ]:
        raise AssertionError("Scenario E must list each held Chance card as a distinct candidate")
    if any(option.target is not None for option in discard_options):
        raise AssertionError("Scenario E card-specific discard candidates must not require targets")

    messages, warning = compose_prompt(conversation, forced_discard_request)
    roles = [message.role for message in messages]
    if roles != ["system", "user", "assistant", "user", "assistant", "user", "assistant", "user"]:
        raise AssertionError("Scenario E must preserve error-retry assistant/user replay ordering")
    if any("controller_connection_error" in message.content for message in messages):
        raise AssertionError(
            "Scenario E must not expose runtime infrastructure details to the Agent"
        )
    _write_messages(buf, messages, warning)


def main() -> None:
    buf = StringIO()
    _write_confirmation_checklist(buf)
    with TemporaryDirectory() as directory:
        scenario_a(buf, directory)
        scenario_b(buf, directory)
        scenario_c(buf, directory)
        scenario_d(buf, directory)
        scenario_e(buf, directory)
        scenario_f(buf, directory)
        scenario_g(buf, directory)
    _REPORT_PATH.write_text(buf.getvalue(), encoding="utf-8")
    print(f"Wrote {_REPORT_PATH} ({len(buf.getvalue())} chars)")


if __name__ == "__main__":
    main()
