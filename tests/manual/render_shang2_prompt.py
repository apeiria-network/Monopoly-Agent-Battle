"""Render Shang2 court context-confirmation scenarios for human review.

Run from the repository root:
    .venv/Scripts/python.exe tests/manual/render_shang2_prompt.py

Writes the full Shang2 (redesigned Shang court) context-confirmation report
to ``tests/manual/render_shang2_prompt_report.txt`` (UTF-8) and echoes a short
summary to stdout. Unlike ``render_decision_prompt.py`` (Baseline/Qin
fixtures), this script drives a real ``Shang2CourtAgent`` with stub clients,
so every rendered message is produced by the production composer and the
production advice/oracle delivery paths.

The four scenarios requested by the project owner:

  A – 大臣视角，行动回合内第一次决策 (minister_2, first decision).
      Expected: [system, user]; no ``oracle`` field and no court history
      anywhere — parallel ministers cannot see each other's live replies; the
      one initial Chance card (swap card) is a listed legal candidate.
  B – 皇帝视角，行动回合内第一次决策 (emperor, first decision).
      Expected: three minister advices injected into the final user message,
      each with a system-assigned ``oracle``; minister_1 and minister_2 agree
      on end_turn and therefore share one omen.
  C – 大臣视角，行动回合内第二次决策 (minister_2, second decision).
      Expected: 段 5 replays the first decision — own advice as an
      ``assistant`` message, peers' advices and the emperor's final decision
      as user context — and none of them carries ``oracle``; the swap card
      stays usable with its swap-out target narrowed by the mortgage.
  D – 皇帝视角，行动回合内第二次决策 (emperor, second decision).
      Expected: the first decision's oracle-bearing advices replay with
      unchanged omens, the emperor's own first final decision appears as
      ``assistant``, then the current decision's three oracle-bearing
      advices follow; minister_1 and minister_3 now agree on end_turn and
      share one new omen.

The shared engine starts player a on position 5 with two vacant streets
(1 and 9), one initial swap Chance card, and an opponent-owned street (3)
within the card's range, so the card is a live candidate in all scenarios.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.agents.shang2 import Shang2CourtAgent
from monopoly_agent_battle.config.models import GameConfig, ModelProfile, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.prompts import render_decision_question
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import GameEvent, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import (
    LLMMessage,
    LLMRequest,
    LLMResponse,
    UsageMetrics,
)

_DIVIDER = "=" * 60
_REPORT_PATH = Path("tests/manual/render_shang2_prompt_report.txt")
_ROLES = ("minister_1", "minister_2", "minister_3", "emperor")


@dataclass(slots=True)
class _CapturingClient:
    """Stub LLM client with a fixed scripted reply that records every request."""

    scripted: list[str]
    requests: list[LLMRequest]

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        content = self.scripted.pop(0)
        return LLMResponse(
            content=content,
            usage=UsageMetrics(input_tokens=1, output_tokens=1),
            model=request.model,
        )


def _write_confirmation_checklist(buf: StringIO) -> None:
    """Write the Shang2 owner-review checklist before the concrete messages."""
    buf.write("商代 Agent v2（shang2_court）上下文确认清单（供项目负责人逐项人工审核）\n")
    buf.write(f"{'=' * 60}\n\n")
    buf.write(
        "审阅范围：以下四个场景的 messages 均由生产 compose_prompt() 生成，"
        "进言、去重、兆相附加与广播全部经由真实 Shang2CourtAgent 执行；"
        "仅 LLM 回复由脚本固定。\n\n"
    )
    buf.write("场景 A：大臣视角，行动回合内第一次决策。\n")
    buf.write("场景 B：皇帝视角，行动回合内第一次决策。\n")
    buf.write("场景 C：大臣视角，行动回合内第二次决策。\n")
    buf.write("场景 D：皇帝视角，行动回合内第二次决策。\n\n")
    buf.write("审核要点：\n")
    buf.write(
        "1. 兆相（oracle 字段）只出现在皇帝收到的进言副本中（场景 B/D）；"
        "大臣的任何消息（场景 A/C，含自身 assistant 历史、同僚进言重放）不得出现 oracle。\n"
    )
    buf.write(
        "2. 相同建议（option 与完整 target 均一致）共享同一个兆相"
        "（场景 B：大臣一与大臣二；场景 D 第二次决策：大臣一与大臣三）。\n"
    )
    buf.write(
        "3. 段 5 重放顺序：决策问题 → 进言 → 皇帝最终决策（assistant）→ "
        "决策造成的事件播报 → 下一次决策问题（场景 C/D）。\n"
    )
    buf.write(
        "4. decision_maker 与 content_type 由系统注入大臣进言 JSON；"
        "皇帝自己的最终决策在皇帝会话中为 assistant 消息，在大臣会话中为 user 内部消息。\n"
    )
    buf.write("5. 大臣并行进言：场景 A 中不可见其他大臣本次回复与皇帝本次最终决策。\n")
    buf.write(
        "6. 段 9「当前决策」与内部消息中的决策问题各出现一次；"
        "合法候选项与输出要求每次独立渲染（场景 C 中出现 redeem_mortgage 新候选）。\n"
    )
    buf.write(
        "7. 初始手牌含且仅含 1 张机会卡（换地卡）：四个场景的候选列表均包含该卡选项，"
        "双目标（换入/换出格子）折叠展示；场景 C/D 中第1格已抵押，"
        "换出目标退化为第9格，卡片仍可用。\n\n"
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
        )


def _event(event_type: str, **payload: object) -> GameEvent:
    return GameEvent(event_type=event_type, payload=payload)


def _make_engine(directory: str) -> GameEngine:
    """A minimal engine placed in ASSET_MANAGEMENT with a usable initial card.

    Player a sits on position 5, owns two vacant streets (1 and 9) and holds
    exactly one initial Chance card — the swap card.  The opponent owns
    position 3, a vacant street within the card's range of 5, so the card is a
    live candidate in every scenario; mortgaging position 1 between the two
    decisions only narrows its swap-out target to position 9.
    """
    config = GameConfig(
        game_id="shang2-prompt",
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
    engine.state.players["a"].position = 5
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    engine.state.properties[9].owner_id = "a"
    engine.state.players["a"].properties.add(9)
    engine.state.properties[3].owner_id = "b"
    engine.state.players["b"].properties.add(3)
    engine.state.players["a"].chance_cards.append("chance-swap-property")
    return engine


def _option_id(request: DecisionRequest, command_type: str) -> str:
    return next(
        option.option_id for option in request.options if option.command_type == command_type
    )


def _reply(option_id: str, target: object, reason: str) -> str:
    selected: dict[str, object] = {"option": option_id}
    if target is not None:
        selected["target"] = target
    return json.dumps({"selected_option": selected, "reason": reason}, ensure_ascii=False)


def _omen_before(text: str, marker: str, *, last: bool = False) -> str:
    """Return the ``oracle`` value rendered immediately before a role marker.

    ``_render_internal_decision`` re-serializes each advice compactly with the
    overlay appended, so one chunk reads ``...,"oracle":"大吉","decision_maker":
    "minister_1","content_type":"advice:oracle"`` — the omen of a role is the
    closest ``oracle`` field preceding its marker.
    """
    head = text.rpartition(marker)[0] if last else text.partition(marker)[0]
    parts = head.rsplit('"oracle":"', 1)
    if len(parts) != 2:
        raise AssertionError(f"no oracle rendered before marker {marker}")
    return parts[1].split('"', 1)[0]


def main() -> None:
    buf = StringIO()
    _write_confirmation_checklist(buf)

    with TemporaryDirectory() as directory:
        engine = _make_engine(directory)
        first = build_decision_request(engine, sequence=1)
        end_turn = _option_id(first, "end_turn")
        mortgage = _option_id(first, "mortgage")

        clients = {role: _CapturingClient(scripted=[], requests=[]) for role in _ROLES}
        conversations = {
            role: AgentConversation(agent_id=f"a.{role}", window_turns=1) for role in _ROLES
        }
        agent = Shang2CourtAgent(
            player_id="a",
            seed=engine.config.seed,
            minister_1_client=clients["minister_1"],
            minister_1_profile=ModelProfile(provider="mock", model="shang2-minister-v1"),
            minister_2_client=clients["minister_2"],
            minister_2_profile=ModelProfile(provider="mock", model="shang2-minister-v1"),
            minister_3_client=clients["minister_3"],
            minister_3_profile=ModelProfile(provider="mock", model="shang2-minister-v1"),
            emperor_client=clients["emperor"],
            emperor_profile=ModelProfile(provider="mock", model="shang2-emperor-v1"),
            conversations=conversations,
        )

        # The action turn opens with dice and movement before the first decision.
        for conversation in conversations.values():
            conversation.start_turn(1)
            for evt in (
                _event("dice_rolled", player_id="a", dice=(2, 3)),
                _event("player_moved", player_id="a", to=5),
            ):
                conversation.append_event(evt, complete_round=0)

        # Decision 1: ministers 1 and 2 agree on end_turn; minister 3 suggests
        # mortgaging position 1 (a different target-bearing suggestion that
        # deterministically draws a different omen under this seed).
        clients["minister_1"].scripted = [
            _reply(end_turn, None, "现金充足，暂时无需处置资产，先结束回合。")
        ]
        clients["minister_2"].scripted = [_reply(end_turn, None, "同意按兵不动，避免暴露意图。")]
        clients["minister_3"].scripted = [
            _reply(mortgage, 1, "抵押第1格地产可套取现金，为后续购地做准备。")
        ]
        clients["emperor"].scripted = [
            _reply(mortgage, 1, "采纳大臣三的意见，抵押第1格地产扩充现金。")
        ]
        first_reply = agent(first)

        # Scenario A: minister_2's first-decision prompt.
        _write_header(buf, "A", "大臣视角 — 行动回合内第一次决策（并行进言，无任何朝廷历史）")
        minister_first = clients["minister_2"].requests[0].messages
        if [msg.role for msg in minister_first] != ["system", "user"]:
            raise AssertionError("Scenario A must be exactly [system, user]")
        minister_first_text = "\n".join(msg.content for msg in minister_first)
        if '"oracle"' in minister_first_text:
            raise AssertionError("Scenario A must not expose any oracle to a minister")
        if '"decision_maker"' in minister_first_text:
            raise AssertionError("Scenario A must not replay court history before any decision")
        if "## 合法候选操作" not in minister_first_text:
            raise AssertionError("Scenario A must list legal candidates")
        swap_option = "use_chance_card-chance-swap-property"
        if swap_option not in minister_first_text:
            raise AssertionError("Scenario A must list the initial swap-card candidate")
        _write_messages(buf, minister_first, None)

        # Scenario B: the emperor's first-decision prompt.
        _write_header(buf, "B", "皇帝视角 — 行动回合内第一次决策（三份进言附系统兆相）")
        emperor_first = clients["emperor"].requests[0].messages
        emperor_first_text = "\n".join(msg.content for msg in emperor_first)
        if emperor_first_text.count('"oracle":') != 3:
            raise AssertionError("Scenario B must carry exactly three oracle fields")
        omen_m1 = _omen_before(emperor_first_text, '"decision_maker":"minister_1"')
        omen_m2 = _omen_before(emperor_first_text, '"decision_maker":"minister_2"')
        if omen_m1 != omen_m2:
            raise AssertionError("Scenario B equal suggestions must share one omen")
        omen_m3 = _omen_before(emperor_first_text, '"decision_maker":"minister_3"')
        if omen_m3 == omen_m1:
            raise AssertionError("Scenario B distinct suggestion must get an independent draw")
        if '"decision_maker":"emperor"' in emperor_first_text:
            raise AssertionError("Scenario B must not show an emperor final before it exists")
        _write_messages(buf, emperor_first, None)

        # The runner persists the emperor's engine-facing reply and then the
        # agent broadcasts it to the three ministers.
        conversations["emperor"].append_decision(
            decision_id=first.decision_id,
            question_summary=render_decision_question(first),
            assistant_reply=first_reply,
        )
        agent.record_final_decision(first, first_reply)
        # The executed mortgage produces a broadcastable event, and the board
        # state flips so the second decision gains a redeem_mortgage candidate.
        # Position 9 stays unmortgaged, so the swap card remains usable with
        # its swap-out target narrowed to position 9.
        for conversation in conversations.values():
            conversation.append_event(
                _event("property_mortgaged", player_id="a", position=1, amount=30),
                complete_round=0,
            )
        engine.state.properties[1].mortgaged = True
        engine.state.players["a"].cash += 30

        second = build_decision_request(engine, sequence=2)
        redeem = _option_id(second, "redeem_mortgage")
        # Decision 2: ministers 1 and 3 agree on end_turn; minister 2 alone
        # suggests redeeming the mortgage.
        clients["minister_1"].scripted = [
            _reply(end_turn, None, "抵押利息可控，保留现金，结束回合。")
        ]
        clients["minister_2"].scripted = [_reply(redeem, 1, "赎回第1格地产恢复租金收入。")]
        clients["minister_3"].scripted = [_reply(end_turn, None, "赎回成本较高，先结束回合更稳。")]
        clients["emperor"].scripted = [_reply(end_turn, None, "综合意见与兆相，本回合到此为止。")]
        agent(second)

        # Scenario C: minister_2's second-decision prompt.
        _write_header(buf, "C", "大臣视角 — 行动回合内第二次决策（重放无兆相的朝廷历史）")
        minister_second = clients["minister_2"].requests[1].messages
        minister_second_text = "\n".join(msg.content for msg in minister_second)
        if '"oracle"' in minister_second_text:
            raise AssertionError("Scenario C must never replay an oracle to a minister")
        assistant_messages = [msg for msg in minister_second if msg.role == "assistant"]
        if [msg.content for msg in assistant_messages] != [
            _reply(end_turn, None, "同意按兵不动，避免暴露意图。")
        ]:
            raise AssertionError("Scenario C must replay the minister's own first advice")
        for peer in ("minister_1", "minister_3"):
            if f'"decision_maker":"{peer}"' not in minister_second_text:
                raise AssertionError(f"Scenario C must replay {peer}'s first advice")
        if '"decision_maker":"emperor"' not in minister_second_text:
            raise AssertionError("Scenario C must replay the emperor's first final decision")
        if "抵押第1格" not in minister_second_text:
            raise AssertionError("Scenario C must broadcast the mortgage event")
        if redeem not in minister_second_text:
            raise AssertionError("Scenario C must list the new redeem_mortgage candidate")
        if "use_chance_card-chance-swap-property" not in minister_second_text:
            raise AssertionError("Scenario C must keep the swap card usable after the mortgage")
        _write_messages(buf, minister_second, None)

        # Scenario D: the emperor's second-decision prompt.
        _write_header(buf, "D", "皇帝视角 — 行动回合内第二次决策（历史兆相重放 + 新进言兆相）")
        emperor_second = clients["emperor"].requests[1].messages
        emperor_second_text = "\n".join(msg.content for msg in emperor_second)
        if emperor_second_text.count('"oracle":') != 6:
            raise AssertionError("Scenario D must replay three oracles and add three new ones")
        assistant_messages = [msg for msg in emperor_second if msg.role == "assistant"]
        if [msg.content for msg in assistant_messages] != [first_reply]:
            raise AssertionError(
                "Scenario D must replay the emperor's own first final as assistant"
            )
        replay_omen_m1 = _omen_before(emperor_second_text, '"decision_maker":"minister_1"')
        if replay_omen_m1 != omen_m1:
            raise AssertionError("Scenario D must keep the first decision's omen unchanged")
        omen2_m1 = _omen_before(emperor_second_text, '"decision_maker":"minister_1"', last=True)
        omen2_m3 = _omen_before(emperor_second_text, '"decision_maker":"minister_3"', last=True)
        if omen2_m1 != omen2_m3:
            raise AssertionError("Scenario D second-decision equal suggestions must share one omen")
        _write_messages(buf, emperor_second, None)

        # Private audit appendix: the oracle derivation material for review.
        _write_header(buf, "*", "附录 — court trace 中的兆相审计记录（不进入任何 Agent 上下文）")
        buf.write(json.dumps(agent.court_trace()["oracles"], ensure_ascii=False, indent=2))
        buf.write("\n")

    _REPORT_PATH.write_text(buf.getvalue(), encoding="utf-8")
    print(f"Wrote {_REPORT_PATH} ({len(buf.getvalue())} chars)")


if __name__ == "__main__":
    main()
