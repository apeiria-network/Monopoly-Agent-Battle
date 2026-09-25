"""校正轨：卡型权重 w、损耗分档、地板使用率，并回填抢夺卡指标列。

做了什么分析
------------
第五章两条分析的校正基座（Other_analysis_field.md §5.2「抽卡质量 Q」、
§5.3「指标」条）：
* 卡型权重 w_type —— 分析一的抽卡质量 Q = 非抢夺获得卡的 w 总和；w 取该卡型
  在地板对局（greedy 主估、sane 稳健性）打出时的 max_target_dv 均值，
  即「这张卡在只会机械执行的玩家手里平均能兑现多少 V」。
* 损耗分档 loss_p50 / loss_p90 —— 分析二的地板校正带：原始损耗是货币量，
  跨卡型比较须先对同卡型地板分位数归层。
* 地板使用率 usage_rate_floor —— 打出数 ÷ 可打决策数（§5.1 F4 分母口径：
  该卡在手且存在 ≥1 合法候选的决策；出狱卡分母 = 持有时的坐牢决策）。
* 抢夺卡指标回填 —— 抢夺卡打出时 ΔV 恒 0（V 无手牌项），其价值在抢夺选卡
  节点结算：用 w_greedy 给 card_plays.csv 的抢夺行回填 chosen_w / max_w /
  pick_loss / hit / target_pick_loss 五列（§5.1 抢夺卡特例，描述性指标）。

怎么做的
--------
* 地板重放一遍（greedy_script / sane_random）：每个资产管理决策点，对手中
  每种机会卡试出「是否有 ≥1 合法候选」（逐候选深拷贝执行、首个成功即止）；
  坐牢决策点若持有出狱卡则计入出狱卡可打决策；抢夺选卡决策点记录受害者手牌
  与三家手牌（w_steal 不动点与回填的原始数据）。
* w_steal 不动点（自指：受害者手里可能也有抢夺卡）：w⁰=0，
  w^{t+1} = 该人群全部抢夺选卡中 max_{c∈受害者手牌} w^t(c) 的均值，
  迭代至 |Δ| < 1e-12（单调有界必收敛）。
* w 查找回退链：greedy → sane → 0（回退与缺类计入运行摘要，不中断）。
* 分档：target_loss 的 P50/P90（numpy 线性插值），按卡型 × 人群。
* 回填：每局第 k 个 chance-steal 打出 ↔ 第 k 个抢夺选卡（顺序一一对应，
  校验打出行的 victim_id 与选卡受害者一致；不齐/不符计入摘要）。
* 全部表以 game_index + decision_index 关联；异常只计数不中断。

输出怎么解读
------------
目录约定（§5.4 冻结）：结果表放 stat/card/ 顶层，中间文件放 stat/card/data/。
本脚本：--out → stat/card/card_type_calibration.csv（顶层结果表）；
--backfill-out → stat/card/data/card_plays.csv（中间文件，覆盖式更新）。

card_type_calibration.csv（长表，每行 = 卡型 × 人群）：
  card_id / population —— 17 类卡 × greedy / sane
  n_plays —— 该人群打出次数（w 与分档的样本量；<30 时参考意义有限）
  w_type —— 卡型权重（货币量）：该卡在地板上平均可兑现的 V；
      chance-steal 为不动点估值；community-jail-free 为坐牢决策候选 max 均值
      （打出条件自选择，轻微高估其边际，口径已冻结）
  loss_p50 / loss_p90 —— 目标损耗分位带：LLM 某次打出损耗 > p90 即「落在
      地板最差 10%」，< p50 即「优于地板中位」
  usage_rate_floor —— 地板使用率，分析二 F4 的参照基线
回填版 card_plays.csv：schema 不变，chance-steal 行五列填好：
  chosen_w（选中卡权重）/ max_w（受害者手牌最优权重）/ pick_loss（选卡损耗）
  / hit（是否选中最优）/ target_pick_loss（盲选受害者损耗 =
  三家手牌最优中的最大值 − 实际受害者的 max_w；均为对手损失代理口径，
  147 个 LLM 样本只描述不检验）。

用法（小样本自测；正式全量需负责人批准）：
    .venv/Scripts/python.exe stat/card/calibrate.py --games 5 \
        --plays stat/card/data/_dev_plays.csv \
        --out stat/card/_dev_calibration.csv \
        --backfill-out stat/card/data/_dev_plays_backfilled.csv

多核（与 §6 evaluate.py 同模式的外部分片，N 个进程各扫 1/N 局后合并）：
    # 分片扫描（i = 0..N-1，各进程独立，落盘中间产物）：
    .venv/Scripts/python.exe stat/card/calibrate.py --skip i --shard N \
        --plays <plays.csv> --scan-out <part_i.json>
    # 合并聚合（单进程，读全部中间产物）：
    .venv/Scripts/python.exe stat/card/calibrate.py \
        --scan-in <part_0.json> <part_1.json> ... --plays <plays.csv> \
        --out stat/card/card_type_calibration.csv \
        --backfill-out stat/card/data/card_plays.csv
    score.py / targets.py 的 --skip/--shard 输出为互斥行集，CSV 去重表头后
    直接拼接即可。analysis1 纯统计、analysis2 扫描仅 LLM 108 局，无需分片。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "judge"))

import evaluate as evaluate_module  # noqa: E402
from replay_tools import ReplayDivergence, iter_decision_points  # noqa: E402

from monopoly_agent_battle.decision.requests import _candidate_commands  # noqa: E402
from monopoly_agent_battle.domain.commands import (  # noqa: E402
    SelectStolenChanceCard,
    UseChanceCard,
)
from monopoly_agent_battle.domain.models import JailStatus, TurnPhase  # noqa: E402
from monopoly_agent_battle.game.cards.classic_cards import CARDS_BY_ID  # noqa: E402
from monopoly_agent_battle.game.engine import GameRuleError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent

FLOOR_EXPERIMENTS = ("greedy_script", "sane_random")
EXPERIMENTS = (
    "4-courts-battle",
    "court-vs-baseline",
    "fe-vs-baseline",
    "court-fe-battle",
    "greedy_script",
    "sane_random",
)
POPULATION_OF = {
    "greedy_script": "greedy",
    "sane_random": "sane",
    "4-courts-battle": "llm",
    "court-vs-baseline": "llm",
    "fe-vs-baseline": "llm",
    "court-fe-battle": "llm",
}
JAIL_FREE_CARD = "community-jail-free"
STEAL_CARD = "chance-steal"

COLUMNS = (
    "card_id",
    "population",
    "n_plays",
    "w_type",
    "loss_p50",
    "loss_p90",
    "usage_rate_floor",
)


@dataclass(slots=True)
class TheftSelection:
    experiment: str
    game_index: int
    decision_index: int
    thief_id: str
    victim_id: str
    selected_card: str
    victim_hand: list[str] = field(default_factory=list)
    all_hands: dict[str, list[str]] = field(default_factory=dict)


def _dump_scan(
    path: Path,
    playable: dict[tuple[str, str], int],
    jailfree_playable: dict[str, int],
    selections: list[TheftSelection],
) -> None:
    """Persist scan intermediates so sharded scans can be merged later."""
    payload = {
        "playable": {
            f"{population}|{card_id}": count for (population, card_id), count in playable.items()
        },
        "jailfree_playable": dict(jailfree_playable),
        "selections": [asdict(selection) for selection in selections],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _load_scans(
    paths: list[str],
) -> tuple[dict[tuple[str, str], int], dict[str, int], list[TheftSelection]]:
    """Merge sharded scan intermediates (counts summed, selections concatenated)."""
    playable: dict[tuple[str, str], int] = defaultdict(int)
    jailfree_playable: dict[str, int] = defaultdict(int)
    selections: list[TheftSelection] = []
    for raw_path in paths:
        payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        for key, count in payload["playable"].items():
            population, card_id = key.split("|", 1)
            playable[(population, card_id)] += count
        for population, count in payload["jailfree_playable"].items():
            jailfree_playable[population] += count
        selections.extend(TheftSelection(**item) for item in payload["selections"])
    return playable, jailfree_playable, selections


def _playable_cards(engine, player_id: str) -> set[str]:
    """Held chance card types with at least one legal candidate at this decision."""
    candidates_by_card: dict[str, list] = defaultdict(list)
    for candidate in _candidate_commands(engine, player_id):
        if isinstance(candidate, UseChanceCard):
            candidates_by_card[candidate.card_id].append(candidate)
    playable: set[str] = set()
    for card_id, candidates in candidates_by_card.items():
        for candidate in candidates:
            clone = deepcopy(engine)
            try:
                clone.execute(candidate)
            except GameRuleError:
                continue
            playable.add(card_id)
            break
    return playable


def scan_game(
    directory,
    experiment: str,
    game_index: int,
    playable: dict[str, int],
    jailfree_playable: dict[str, int],
    selections: list[TheftSelection],
    anomalies: dict[str, int],
) -> None:
    """One replay pass: playable-decision counts + theft-selection records."""
    population = POPULATION_OF[experiment]
    try:
        for point in iter_decision_points(directory):
            engine = point.engine
            state = engine.state
            player_id = point.player_id
            if isinstance(point.command, SelectStolenChanceCard):
                thief = state.pending_theft_thief_id or player_id
                victim = state.pending_theft_target_id
                if victim is None:
                    anomalies["theft_selection_no_victim"] += 1
                    continue
                selections.append(
                    TheftSelection(
                        experiment=experiment,
                        game_index=game_index,
                        decision_index=point.index,
                        thief_id=thief,
                        victim_id=victim,
                        selected_card=point.command.card_id,
                        victim_hand=list(state.players[victim].chance_cards),
                        all_hands={
                            pid: list(player.chance_cards)
                            for pid, player in state.players.items()
                            if pid != thief
                        },
                    )
                )
            elif state.turn_phase is TurnPhase.ASSET_MANAGEMENT:
                held = set(state.players[player_id].chance_cards)
                for card_id in _playable_cards(engine, player_id) & held:
                    playable[(population, card_id)] += 1
            elif state.players[player_id].jail_status is JailStatus.ROLLING:
                if state.players[player_id].community_get_out_of_jail_cards:
                    jailfree_playable[population] += 1
    except ReplayDivergence:
        anomalies["replay_divergence"] += 1


def _solve_w_steal(
    selections: list[TheftSelection], w: dict[str, float], anomalies: dict[str, int]
) -> float:
    """Fixed-point w for chance-steal over one population's theft selections."""
    if not selections:
        return float("nan")
    w_steal = 0.0
    for _iteration in range(200):
        total = 0.0
        lookup = {**w, STEAL_CARD: w_steal}
        for selection in selections:
            if not selection.victim_hand:
                anomalies["theft_victim_empty_hand"] += 1
                continue
            total += max(lookup.get(card, 0.0) for card in selection.victim_hand)
        updated = total / len(selections)
        if abs(updated - w_steal) < 1e-12:
            return updated
        w_steal = updated
    anomalies["w_steal_not_converged"] += 1
    return w_steal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--experiments", nargs="*", default=list(EXPERIMENTS))
    parser.add_argument(
        "--games", type=int, default=None, help="每实验最多处理局数（小样本自测用）"
    )
    parser.add_argument("--plays", required=True, help="card_plays.csv 路径（输入）")
    parser.add_argument("--out", default=None, help="card_type_calibration.csv 输出路径")
    parser.add_argument("--backfill-out", default=None, help="回填后的 card_plays.csv 输出路径")
    parser.add_argument("--skip", type=int, default=0, help="分片跳过（正式跑批并行用）")
    parser.add_argument("--shard", type=int, default=1, help="分片数（正式跑批并行用）")
    parser.add_argument(
        "--scan-out",
        default=None,
        help="扫描中间产物落盘路径（分片扫描模式：落盘后退出，不聚合）",
    )
    parser.add_argument(
        "--scan-in",
        nargs="*",
        default=None,
        help="读取各分片扫描产物并聚合（跳过扫描，需搭配 --plays）",
    )
    args = parser.parse_args()
    if not args.scan_out and not (args.out and args.backfill_out):
        parser.error("--out 与 --backfill-out 在非 --scan-out 模式下必填")

    anomalies: dict[str, int] = defaultdict(int)

    play_rows: list[dict[str, str]] = []
    with Path(args.plays).open(encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        play_columns = reader.fieldnames or []
        play_rows = list(reader)

    if args.scan_in:
        playable, jailfree_playable, selections = _load_scans(args.scan_in)
        print(f"loaded {len(args.scan_in)} scan shards", file=sys.stderr)
    else:
        playable: dict[tuple[str, str], int] = defaultdict(int)
        jailfree_playable: dict[str, int] = defaultdict(int)
        selections: list[TheftSelection] = []
        for experiment in args.experiments:
            directories = evaluate_module.experiment_directories(experiment)
            if args.games is not None:
                directories = directories[: args.games]
            for game_index, directory in enumerate(directories):
                if game_index % args.shard != args.skip:
                    continue
                scan_game(
                    directory,
                    experiment,
                    game_index,
                    playable,
                    jailfree_playable,
                    selections,
                    anomalies,
                )
            print(f"{experiment}: {len(directories)} games scanned", file=sys.stderr)
        if args.scan_out:
            _dump_scan(Path(args.scan_out), playable, jailfree_playable, selections)
            print(f"scan shard -> {args.scan_out}", file=sys.stderr)
            return

    # ---- 聚合：w / 分档（地板两个人群）----
    by_type_pop: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in play_rows:
        population = POPULATION_OF[row["experiment"]]
        if population in ("greedy", "sane"):
            by_type_pop[(row["card_id"], population)].append(row)

    plays_count: dict[tuple[str, str], int] = defaultdict(int)
    for row in play_rows:
        population = POPULATION_OF[row["experiment"]]
        if population in ("greedy", "sane"):
            plays_count[(row["card_id"], population)] += 1

    w_table: dict[str, dict[str, float]] = {"greedy": {}, "sane": {}}
    loss_quantiles: dict[tuple[str, str], tuple[float, float]] = {}
    for (card_id, population), rows in sorted(by_type_pop.items()):
        if card_id == STEAL_CARD:
            continue  # 不动点单独估
        max_targets = [float(row["max_target_dv"]) for row in rows]
        losses = [float(row["target_loss"]) for row in rows]
        w_table[population][card_id] = float(np.mean(max_targets))
        loss_quantiles[(card_id, population)] = (
            float(np.percentile(losses, 50)),
            float(np.percentile(losses, 90)),
        )
    for population in ("greedy", "sane"):
        pop_selections = [s for s in selections if POPULATION_OF[s.experiment] == population]
        w_steal = _solve_w_steal(pop_selections, w_table[population], anomalies)
        w_table[population][STEAL_CARD] = w_steal
        steal_rows = by_type_pop.get((STEAL_CARD, population))
        if steal_rows:
            losses = [float(row["target_loss"]) for row in steal_rows]
            loss_quantiles[(STEAL_CARD, population)] = (
                float(np.percentile(losses, 50)),
                float(np.percentile(losses, 90)),
            )

    # ---- 校准表输出 ----
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for card_id in sorted(CARDS_BY_ID):
            for population in ("greedy", "sane"):
                n_plays = plays_count.get((card_id, population), 0)
                w_value = w_table[population].get(card_id)
                p50, p90 = loss_quantiles.get((card_id, population), ("", ""))
                if card_id == JAIL_FREE_CARD:
                    denominator = jailfree_playable.get(population, 0)
                else:
                    denominator = playable.get((population, card_id), 0)
                usage = n_plays / denominator if denominator else ""
                writer.writerow(
                    {
                        "card_id": card_id,
                        "population": population,
                        "n_plays": n_plays,
                        "w_type": "" if w_value is None else w_value,
                        "loss_p50": p50,
                        "loss_p90": p90,
                        "usage_rate_floor": usage,
                    }
                )

    # ---- 抢夺列回填（全实验，用 w_greedy，回退 sane → 0）----
    def w_lookup(card_id: str) -> float:
        value = w_table["greedy"].get(card_id)
        if value is None or (isinstance(value, float) and np.isnan(value)):
            value = w_table["sane"].get(card_id)
        if value is None or (isinstance(value, float) and np.isnan(value)):
            anomalies["w_lookup_fallback_zero"] += 1
            return 0.0
        return float(value)

    selections_by_game: dict[tuple[str, int], list[TheftSelection]] = defaultdict(list)
    for selection in selections:
        selections_by_game[(selection.experiment, selection.game_index)].append(selection)
    for game_selections in selections_by_game.values():
        game_selections.sort(key=lambda item: item.decision_index)

    steal_rows_by_game: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
    for row in play_rows:
        if row["card_id"] == STEAL_CARD:
            steal_rows_by_game[(row["experiment"], int(row["game_index"]))].append(row)
    for key, rows in steal_rows_by_game.items():
        rows.sort(key=lambda item: int(item["decision_index"]))
        game_selections = selections_by_game.get(key, [])
        if len(rows) != len(game_selections):
            anomalies["theft_play_selection_count_mismatch"] += abs(
                len(rows) - len(game_selections)
            )
        for row, selection in zip(rows, game_selections, strict=False):
            if row["victim_id"] != selection.victim_id:
                anomalies["theft_victim_mismatch"] += 1
            victim_weights = [(card, w_lookup(card)) for card in selection.victim_hand]
            chosen_w = w_lookup(selection.selected_card)
            max_w = max((weight for _card, weight in victim_weights), default=0.0)
            best_cards = (
                [card for card, weight in victim_weights if weight == max_w]
                if victim_weights
                else []
            )
            nonempty_hands = [
                max(w_lookup(card) for card in hand)
                for _pid, hand in selection.all_hands.items()
                if hand
            ]
            others_max = max(nonempty_hands) if nonempty_hands else max_w
            row["chosen_w"] = chosen_w
            row["max_w"] = max_w
            row["pick_loss"] = max_w - chosen_w
            row["hit"] = int(selection.selected_card in best_cards)
            row["target_pick_loss"] = others_max - max_w

    backfill_path = Path(args.backfill_out)
    backfill_path.parent.mkdir(parents=True, exist_ok=True)
    with backfill_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(play_columns))
        writer.writeheader()
        writer.writerows(play_rows)

    print(f"calibration -> {out_path}", file=sys.stderr)
    print(f"backfilled plays -> {backfill_path}", file=sys.stderr)
    print(f"ANOMALIES: {dict(anomalies) if anomalies else 'none'}", file=sys.stderr)


if __name__ == "__main__":
    main()
