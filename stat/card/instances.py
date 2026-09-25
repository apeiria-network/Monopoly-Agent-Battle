"""F0 持卡段重建：每张卡每个实例的「获得 → 失去」持续段。

做了什么分析
------------
第五章 F0（累计持有时间）的基础数据层：把每局 events.jsonl 的卡事件流
重建为「持卡段」——每行 = 一名玩家持有一张卡实例的一段生命周期，从获得
到失去。后续 F0 倾向画像、F4 使用率分母、分析二的持有期检验都以本表为准。

怎么做的
--------
口径见 Other_analysis_field.md §5.1「F0 持有段」条（2026-09-24 与负责人对齐）：

* 获得四来源：开局发牌（用 GameEngine(config) 构造初始状态取初始手牌，
  起点第 0 轮、第 0 自家回合）；机会卡抽中（card_held 且卡型属 chance）；
  抢夺获得（chance_card_stolen：受害者段以 stolen 闭环、夺取者开新段）；
  出狱卡抽中（card_held 且卡型属 community-jail-free）。
* 终点五类：打出（机会卡看 chance_card_used；出狱卡看 card_discarded 且
  reason=played）、超限弃置与破产归还（card_discarded 的 hand_limit /
  bankruptcy）、被抢走（chance_card_stolen 的受害者侧）、局末 censored
  （仍持有的段计到第 50 轮末并标记）。
* 同类卡多张：按（玩家 × 卡型）FIFO 队列，获得入队、失去出队，与冻结口径一致。
* 计时：轮次按引擎 complete_rounds 规则逐事件模拟——round_player_ids
  快照集合被 turn_ended 全覆盖时 +1 并按存活玩家重快照（player_bankrupt
  事件维护存活集）；own_turn = 该玩家 turn_started 的累计序号（首张在
  第 1 回合）。censored 段的 held_own_turns 记实际局末值（无法外推）。
* 打出段与 card_plays.csv 的关联：command_executed 流里第 k 个
  UseChanceCard 命令与第 k 个 chance_card_used 事件一一对应（自然重放
  保证每条记录的命令都执行成功），decision_index = 该命令在命令流中的
  0 基序号（与 §6 replay_tools.DecisionPoint.index 同键）。出狱卡同理
  配对 UseCommunityGetOutOfJailCard 与 card_discarded(reason=played)。
* 异常（FIFO 下溢、配对不齐等）只计数报告，不中断（负责人裁定）。

输出怎么解读
------------
stat/card/data/card_instances.csv（中间文件），每行一段持卡段，列：
  experiment / game_index / game_name / seat / player_id / controller
      局与玩家身份；game_index 经 evaluate_module.experiment_directories
      按文件系统重建，与 §6 各表同键
  card_id / deck —— 卡型（17 类）与牌堆（chance / community_chest）
  acquire_source —— initial_deal / chance_draw / theft / community_draw
  acquire_round / acquire_own_turn —— 获得时的完整轮数 / 自家回合序
  end_reason —— played / hand_limit / bankruptcy / stolen / game_end
  end_round / end_own_turn —— 失去时计时（censored 段 end_round=50）
  held_rounds / held_own_turns —— 持有时长双口径（= end − acquire）
  censored —— 1 = 局末仍持有（右删失；聚合按 §5.1 含/不含双口径）
  play_decision_index —— 打出段关联 card_plays.csv 的 decision_index，否则空

用法（小样本自测；正式全量需负责人批准）：
    .venv/Scripts/python.exe stat/card/instances.py --games 5 \
        --out stat/card/data/_dev_instances.csv
    .venv/Scripts/python.exe stat/card/instances.py --out stat/card/data/card_instances.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "judge"))

import evaluate as evaluate_module  # noqa: E402
from replay_tools import load_commands  # noqa: E402

from monopoly_agent_battle.game.engine import GameEngine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
RUNS = ROOT / "runs"

EXPERIMENTS = (
    "4-courts-battle",
    "court-vs-baseline",
    "fe-vs-baseline",
    "court-fe-battle",
    "greedy_script",
    "sane_random",
)

COLUMNS = (
    "experiment",
    "game_index",
    "game_name",
    "seat",
    "player_id",
    "controller",
    "card_id",
    "deck",
    "acquire_source",
    "acquire_round",
    "acquire_own_turn",
    "end_reason",
    "end_round",
    "end_own_turn",
    "held_rounds",
    "held_own_turns",
    "censored",
    "play_decision_index",
)

CENSORED_ROUND = 50


def _deck_of(card_id: str) -> str:
    return "community_chest" if card_id.startswith("community-") else "chance"


def _draw_source_of(card_id: str) -> str:
    return "community_draw" if card_id.startswith("community-") else "chance_draw"


class _RoundClock:
    """Replicate the engine's complete_rounds rule over the event stream."""

    def __init__(self, player_ids_seat_order: list[str]) -> None:
        self.living = list(player_ids_seat_order)
        self.snapshot = set(player_ids_seat_order)
        self.completed: set[str] = set()
        self.complete_rounds = 0
        self.own_turn = defaultdict(int)

    def on_turn_started(self, player_id: str) -> None:
        self.own_turn[player_id] += 1

    def on_turn_ended(self, player_id: str) -> None:
        self.completed.add(player_id)
        if self.snapshot and self.snapshot <= self.completed:
            self.complete_rounds += 1
            self.snapshot = set(self.living)
            self.completed.clear()

    def on_bankrupt(self, player_id: str) -> None:
        if player_id in self.living:
            self.living.remove(player_id)


def _open_spell(
    spells: list[dict[str, object]],
    queues: dict[tuple[str, str], list[int]],
    base: dict[str, object],
    card_id: str,
    source: str,
    round_at: int,
    own_turn_at: int,
) -> None:
    spell = {
        **base,
        "card_id": card_id,
        "deck": _deck_of(card_id),
        "acquire_source": source,
        "acquire_round": round_at,
        "acquire_own_turn": own_turn_at,
        "end_reason": "",
        "end_round": "",
        "end_own_turn": "",
        "held_rounds": "",
        "held_own_turns": "",
        "censored": 0,
        "play_decision_index": "",
    }
    queues[(base["player_id"], card_id)].append(len(spells))
    spells.append(spell)


def _close_spell(
    spells: list[dict[str, object]],
    queues: dict[tuple[str, str], list[int]],
    player_id: str,
    card_id: str,
    reason: str,
    round_at: int,
    own_turn_at: int,
    anomalies: dict[str, int],
    play_decision_index: object = "",
) -> None:
    queue = queues.get((player_id, card_id))
    if not queue:
        anomalies[f"fifo_underflow:{reason}"] += 1
        return
    spell = spells[queue.pop(0)]
    spell["end_reason"] = reason
    spell["end_round"] = round_at
    spell["end_own_turn"] = own_turn_at
    spell["held_rounds"] = round_at - int(spell["acquire_round"])
    spell["held_own_turns"] = own_turn_at - int(spell["acquire_own_turn"])
    if play_decision_index != "":
        spell["play_decision_index"] = play_decision_index


def process_game(
    directory: Path, experiment: str, game_index: int
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Rebuild all card spells of one game; returns rows and anomaly counters."""
    config, _commands = load_commands(directory)
    engine = GameEngine(config)
    players = engine.state.players
    controller_of = {player.player_id: player.controller_type for player in config.players}

    anomalies: dict[str, int] = defaultdict(int)
    spells: list[dict[str, object]] = []
    queues: dict[tuple[str, str], list[int]] = defaultdict(list)

    seat_order = [player.player_id for player in sorted(players.values(), key=lambda p: p.seat)]
    clock = _RoundClock(seat_order)

    base_of = {
        player_id: {
            "experiment": experiment,
            "game_index": game_index,
            "game_name": directory.name,
            "seat": player.seat,
            "player_id": player_id,
            "controller": controller_of.get(player_id, ""),
        }
        for player_id, player in players.items()
    }

    # Initial deal: no events in the stream, taken from the constructed engine.
    for player_id in seat_order:
        for card_id in players[player_id].chance_cards:
            _open_spell(spells, queues, base_of[player_id], card_id, "initial_deal", 0, 0)

    use_chance_indices: list[int] = []
    use_jailfree_indices: list[int] = []
    command_index = -1

    for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        record = json.loads(line)
        event_type = record.get("event_type")
        payload = record.get("payload", {})

        if event_type == "command_executed":
            command_index += 1
            command_type = payload.get("command_type")
            if command_type == "UseChanceCard":
                use_chance_indices.append(command_index)
            elif command_type == "UseCommunityGetOutOfJailCard":
                use_jailfree_indices.append(command_index)
        elif event_type == "turn_started":
            clock.on_turn_started(payload["player_id"])
        elif event_type == "turn_ended":
            clock.on_turn_ended(payload["player_id"])
        elif event_type == "player_bankrupt":
            clock.on_bankrupt(payload["player_id"])
        elif event_type == "card_held":
            player_id, card_id = payload["player_id"], payload["card_id"]
            _open_spell(
                spells,
                queues,
                base_of[player_id],
                card_id,
                _draw_source_of(card_id),
                clock.complete_rounds,
                clock.own_turn[player_id],
            )
        elif event_type == "chance_card_stolen":
            thief_id, victim_id = payload["player_id"], payload["target_player_id"]
            card_id = payload["card_id"]
            _close_spell(
                spells,
                queues,
                victim_id,
                card_id,
                "stolen",
                clock.complete_rounds,
                clock.own_turn[victim_id],
                anomalies,
            )
            _open_spell(
                spells,
                queues,
                base_of[thief_id],
                card_id,
                "theft",
                clock.complete_rounds,
                clock.own_turn[thief_id],
            )
        elif event_type == "chance_card_used":
            player_id, card_id = payload["player_id"], payload["card_id"]
            if not use_chance_indices:
                anomalies["play_pairing_underflow"] += 1
                decision_index = ""
            else:
                decision_index = use_chance_indices.pop(0)
            _close_spell(
                spells,
                queues,
                player_id,
                card_id,
                "played",
                clock.complete_rounds,
                clock.own_turn[player_id],
                anomalies,
                decision_index,
            )
        elif event_type == "card_discarded":
            player_id, card_id = payload["player_id"], payload["card_id"]
            if _deck_of(card_id) == "community_chest" and card_id != "community-jail-free":
                continue  # 即时社区卡抽到即结算，从不进入手牌段
            reason = payload.get("reason", "")
            if reason == "played" and _deck_of(card_id) == "chance":
                continue  # chance plays are closed by chance_card_used above
            if reason == "played":
                if not use_jailfree_indices:
                    anomalies["jailfree_pairing_underflow"] += 1
                    decision_index = ""
                else:
                    decision_index = use_jailfree_indices.pop(0)
                _close_spell(
                    spells,
                    queues,
                    player_id,
                    card_id,
                    "played",
                    clock.complete_rounds,
                    clock.own_turn[player_id],
                    anomalies,
                    decision_index,
                )
            elif reason in ("hand_limit", "bankruptcy"):
                _close_spell(
                    spells,
                    queues,
                    player_id,
                    card_id,
                    reason,
                    clock.complete_rounds,
                    clock.own_turn[player_id],
                    anomalies,
                )
            else:
                anomalies[f"unknown_discard_reason:{reason}"] += 1

    if use_chance_indices:
        anomalies["play_pairing_leftover"] += len(use_chance_indices)
    if use_jailfree_indices:
        anomalies["jailfree_pairing_leftover"] += len(use_jailfree_indices)

    for queue in queues.values():
        for spell_index in queue:
            spell = spells[spell_index]
            spell["end_reason"] = "game_end"
            spell["end_round"] = CENSORED_ROUND
            player_id = spell["player_id"]
            spell["end_own_turn"] = clock.own_turn[player_id]
            spell["held_rounds"] = CENSORED_ROUND - int(spell["acquire_round"])
            spell["held_own_turns"] = clock.own_turn[player_id] - int(spell["acquire_own_turn"])
            spell["censored"] = 1

    return spells, anomalies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--experiments", nargs="*", default=list(EXPERIMENTS))
    parser.add_argument(
        "--games", type=int, default=None, help="每实验最多处理局数（小样本自测用）"
    )
    parser.add_argument("--out", required=True, help="输出 CSV 路径")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    total_anomalies: dict[str, int] = defaultdict(int)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for experiment in args.experiments:
            directories = evaluate_module.experiment_directories(experiment)
            if args.games is not None:
                directories = directories[: args.games]
            for game_index, directory in enumerate(directories):
                spells, anomalies = process_game(directory, experiment, game_index)
                for key, value in anomalies.items():
                    total_anomalies[key] += value
                writer.writerows(spells)
                total_rows += len(spells)
            print(f"{experiment}: {len(directories)} games", file=sys.stderr)

    print(f"rows: {total_rows} -> {out_path}", file=sys.stderr)
    if total_anomalies:
        print(f"ANOMALIES: {dict(total_anomalies)}", file=sys.stderr)
    else:
        print("ANOMALIES: none", file=sys.stderr)


if __name__ == "__main__":
    main()
