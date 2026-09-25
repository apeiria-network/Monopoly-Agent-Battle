"""路线 B 靶向评分：每次打出卡的候选级 ΔV 与 F1 长期账分解。

做了什么分析
------------
第五章 F1（自身收益长期账）与 F3（多回合后收益 V 差）的基础数据层：
对每一次打出（机会卡 = UseChanceCard 决策；出狱卡 = 坐牢决策中使用
UseCommunityGetOutOfJailCard），重放到该决策点，枚举合法候选并逐一用
§6.2 完整 V 评分，产出候选级明细。后续 calibrate.py（卡类权重 w、损耗
分档）、analysis1.py（检验 2 的 executed~max 回归）、analysis2.py（效率
指标）全部以本表为准。

怎么做的
--------
口径见 Other_analysis_field.md §5.1「V 口径」「F1/F2 统一长期账」「F3」条：

* 路线 B：不扩展 evaluate.py；本模块用同一评分器（stat/judge/value.py 的
  evaluate）自包含地对含卡决策靶向评分。候选枚举与 §6 评分器完全一致：
  requests._candidate_commands 全量枚举，逐个深拷贝执行、GameRuleError
  即弃（合法候选 = 能无错执行的候选，与 agent 所见一致）。
* 每行 = 一次打出。executed_dv = 实际打出候选的 V 差（V(执行后) − V(执行前)）；
  max_target_dv / target_loss / n_targets：机会卡取该卡全部合法目标候选的
  最优/最优减实际/个数；出狱卡按 §5.1 取整个坐牢决策候选集（赌骰/付费/用卡，
  赌骰按 36 有序骰面平均，与 §6.4 一致）。
* noncard_max_dv = 同决策非卡候选的最优 V 差（「不打出」侧，与悔值同口径）。
* F1 长期账（执行候选 − 执行前，逐项）：d_m_assets（净资产，含卡的直接影响）、
  d_r_short / d_r_long（短/长期期望租金）、d_m_monopoly（垄断建房价值）、
  d_v_total（合计）；cover_rounds_short = short_turns ÷ 4，
  cover_rounds_long = long_laps × 5.71 ÷ 4（截断按实际，取自评分器返回）。
* zero_dv_flag：该卡全部合法候选 ΔV 恒 0 时置 1（抢夺卡；V 不含手牌项）。
* F4 局面特征：cash_pctile_ingame（自己现金在同局 4 人内的中点分位）、
  own_property_count、opp_building_levels（对手建筑层数和）。
* 持有期列（held_rounds_at_play / held_own_turns_at_play）直接取
  card_instances.csv 对应段（按 game_index + play_decision_index 关联）。
* 抢夺卡四个 w 指标列（chosen_w 等）由 calibrate.py 在抢夺选卡节点回填，
  本模块留空。异常（关联缺失、执行候选不在合法集等）只计数不中断。

输出怎么解读
------------
stat/card/data/card_plays.csv（中间文件），每行一次打出，关键列（全列清单见 §5.4）：
  executed_dv —— 这次打出实际兑现的 V（正=赚到）
  max_target_dv —— 这张卡当时能兑现的最优值（卡本身的潜力）
  target_loss —— max − executed，目标选择损耗（货币量，跨卡型比较须按
      卡型分档，见 §5.3 指标条）
  noncard_max_dv —— 不打这手卡的最优替代；executed_dv − noncard_max_dv
      是「打 vs 不打」的边际
  d_m_assets … d_v_total —— F1 长期账五项分解，合计 = executed_dv
  zero_dv_flag=1 —— 该行 ΔV 结构性为 0（抢夺卡），其价值在 calibrate.py
      的 w 指标里，不进任何 ΔV 汇总

用法（小样本自测；正式全量需负责人批准）：
    .venv/Scripts/python.exe stat/card/score.py --games 2 \
        --instances stat/card/data/_dev_instances.csv \
        --out stat/card/data/_dev_plays.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "judge"))

import evaluate as evaluate_module  # noqa: E402
import value as value_module  # noqa: E402
from replay_tools import ReplayDivergence, iter_decision_points  # noqa: E402

from monopoly_agent_battle.decision.requests import _candidate_commands  # noqa: E402
from monopoly_agent_battle.domain.commands import (  # noqa: E402
    GameCommand,
    RollDice,
    UseChanceCard,
    UseCommunityGetOutOfJailCard,
)
from monopoly_agent_battle.game.engine import GameEngine, GameRuleError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent

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
    "seat",
    "player_id",
    "controller",
    "decision_index",
    "round",
    "own_turn",
    "card_id",
    "deck",
    "held_rounds_at_play",
    "held_own_turns_at_play",
    "executed_dv",
    "max_target_dv",
    "target_loss",
    "noncard_max_dv",
    "n_targets",
    "zero_dv_flag",
    "d_m_assets",
    "d_r_short",
    "d_r_long",
    "d_m_monopoly",
    "d_v_total",
    "cover_rounds_short",
    "cover_rounds_long",
    "cash_pctile_ingame",
    "own_property_count",
    "opp_building_levels",
    "victim_id",
    "chosen_w",
    "max_w",
    "pick_loss",
    "hit",
    "target_pick_loss",
)

DICE_OUTCOMES = tuple((first, second) for first in range(1, 7) for second in range(1, 7))


def _fixed_dice(first: int, second: int):
    """Mirror evaluate.py: force one fixed 2d6 roll, then raise StopIteration."""
    rolls = iter((first, second))

    def _randint(_low: int, _high: int) -> int:
        return next(rolls)

    return _randint


def _score_candidate(
    engine: GameEngine, command: GameCommand, player_id: str
) -> value_module.ValueBreakdown | None:
    """Execute one candidate on a clone and return its full V breakdown.

    RollDice (jail decisions) is averaged over the 36 ordered dice outcomes,
    exactly as evaluate.py does; terms are averaged componentwise.
    """
    if isinstance(command, RollDice):
        totals = [0.0] * 5
        realised = 0
        for first, second in DICE_OUTCOMES:
            clone = deepcopy(engine)
            clone.random.randint = _fixed_dice(first, second)  # type: ignore[method-assign]
            try:
                clone.execute(command)
            except (GameRuleError, StopIteration):
                continue
            breakdown = value_module.evaluate(clone.state, player_id)
            totals[0] += breakdown.total
            totals[1] += breakdown.m_assets
            totals[2] += breakdown.r_short
            totals[3] += breakdown.r_long
            totals[4] += breakdown.m_monopoly
            realised += 1
        if realised == 0:
            return None
        return value_module.ValueBreakdown(
            totals[0] / realised,
            totals[1] / realised,
            totals[2] / realised,
            totals[3] / realised,
            totals[4] / realised,
            0,
            0.0,
        )

    clone = deepcopy(engine)
    try:
        clone.execute(command)
    except GameRuleError:
        return None
    return value_module.evaluate(clone.state, player_id)


def _cash_pctile(engine: GameEngine, player_id: str) -> float:
    """Mid-percentile of the player's cash among all four seats, in [0, 1]."""
    own = engine.state.players[player_id].cash
    others = [player.cash for pid, player in engine.state.players.items() if pid != player_id]
    less = sum(1 for cash in others if cash < own)
    ties = sum(1 for cash in others if cash == own)
    return (less + ties / 2) / max(1, len(others))


def _load_play_index(
    instances_path: Path,
) -> dict[tuple[str, int], dict[int, dict[str, str]]]:
    """Map (experiment, game_index) -> decision_index -> instances row."""
    play_index: dict[tuple[str, int], dict[int, dict[str, str]]] = defaultdict(dict)
    with instances_path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["end_reason"] != "played":
                continue
            key = (row["experiment"], int(row["game_index"]))
            play_index[key][int(row["play_decision_index"])] = row
    return play_index


def process_game(
    directory,
    experiment: str,
    game_index: int,
    plays: dict[int, dict[str, str]],
    anomalies: dict[str, int],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        points = iter_decision_points(directory)
        for point in points:
            command = point.command
            if not isinstance(command, (UseChanceCard, UseCommunityGetOutOfJailCard)):
                continue
            engine = point.engine
            player_id = point.player_id
            instance = plays.get(point.index)
            if instance is None:
                anomalies["play_missing_instance"] += 1
                continue

            baseline = value_module.evaluate(engine.state, player_id)
            legal: list[tuple[GameCommand, value_module.ValueBreakdown]] = []
            for candidate in _candidate_commands(engine, player_id):
                scored = _score_candidate(engine, candidate, player_id)
                if scored is not None:
                    legal.append((candidate, scored))
            if not legal:
                anomalies["no_legal_candidates"] += 1
                continue
            executed = next((item for item in legal if item[0] == command), None)
            if executed is None:
                anomalies["executed_not_in_legal"] += 1
                continue

            card_id = command.card_id
            if isinstance(command, UseChanceCard):
                target_set = [
                    item
                    for item in legal
                    if isinstance(item[0], UseChanceCard) and item[0].card_id == card_id
                ]
                noncard_set = [item for item in legal if not isinstance(item[0], UseChanceCard)]
            else:
                # 出狱卡：口径为整个坐牢决策候选集（§5.1 出狱卡条）。
                target_set = legal
                noncard_set = [
                    item for item in legal if not isinstance(item[0], UseCommunityGetOutOfJailCard)
                ]
            if not target_set:
                anomalies["empty_target_set"] += 1
                continue

            deltas = [item[1].total - baseline.total for item in target_set]
            max_target = max(deltas)
            executed_dv = executed[1].total - baseline.total
            noncard_max = (
                max(item[1].total - baseline.total for item in noncard_set) if noncard_set else ""
            )
            zero_dv = int(all(delta == 0 for delta in deltas))

            rows.append(
                {
                    "experiment": experiment,
                    "game_index": game_index,
                    "seat": instance["seat"],
                    "player_id": player_id,
                    "controller": instance["controller"],
                    "decision_index": point.index,
                    "round": point.complete_rounds,
                    "own_turn": engine.state.players[player_id].survived_turns + 1,
                    "card_id": card_id,
                    "deck": instance["deck"],
                    "held_rounds_at_play": instance["held_rounds"],
                    "held_own_turns_at_play": instance["held_own_turns"],
                    "executed_dv": executed_dv,
                    "max_target_dv": max_target,
                    "target_loss": max_target - executed_dv,
                    "noncard_max_dv": noncard_max,
                    "n_targets": len(target_set),
                    "zero_dv_flag": zero_dv,
                    "d_m_assets": executed[1].m_assets - baseline.m_assets,
                    "d_r_short": executed[1].r_short - baseline.r_short,
                    "d_r_long": executed[1].r_long - baseline.r_long,
                    "d_m_monopoly": executed[1].m_monopoly - baseline.m_monopoly,
                    "d_v_total": executed_dv,
                    "cover_rounds_short": baseline.short_turns / 4,
                    "cover_rounds_long": baseline.long_laps * 5.71 / 4,
                    "cash_pctile_ingame": _cash_pctile(engine, player_id),
                    "own_property_count": len(engine.state.players[player_id].properties),
                    "opp_building_levels": sum(
                        property_state.building_level
                        for property_state in engine.state.properties.values()
                        if property_state.owner_id is not None
                        and property_state.owner_id != player_id
                    ),
                    "victim_id": (
                        command.target_player_id
                        if isinstance(command, UseChanceCard)
                        and command.target_player_id is not None
                        else ""
                    ),
                    "chosen_w": "",
                    "max_w": "",
                    "pick_loss": "",
                    "hit": "",
                    "target_pick_loss": "",
                }
            )
    except ReplayDivergence:
        anomalies["replay_divergence"] += 1
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--experiments", nargs="*", default=list(EXPERIMENTS))
    parser.add_argument(
        "--games", type=int, default=None, help="每实验最多处理局数（小样本自测用）"
    )
    parser.add_argument("--skip", type=int, default=0, help="分片跳过（正式跑批并行用）")
    parser.add_argument("--shard", type=int, default=1, help="分片数（正式跑批并行用）")
    parser.add_argument("--instances", required=True, help="card_instances.csv 路径")
    parser.add_argument("--out", required=True, help="输出 CSV 路径")
    args = parser.parse_args()

    play_index = _load_play_index(Path(args.instances))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    anomalies: dict[str, int] = defaultdict(int)
    total_rows = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for experiment in args.experiments:
            directories = evaluate_module.experiment_directories(experiment)
            if args.games is not None:
                directories = directories[: args.games]
            for position, directory in enumerate(directories):
                if position % args.shard != args.skip:
                    continue
                key = (experiment, position)
                rows = process_game(
                    directory, experiment, position, play_index.get(key, {}), anomalies
                )
                writer.writerows(rows)
                total_rows += len(rows)
            print(f"{experiment}: {len(directories)} games", file=sys.stderr)

    print(f"rows: {total_rows} -> {out_path}", file=sys.stderr)
    print(f"ANOMALIES: {dict(anomalies) if anomalies else 'none'}", file=sys.stderr)


if __name__ == "__main__":
    main()
