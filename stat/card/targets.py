"""F2 对手长期账：每次打出对每个受影响对手的 V 分解影响。

做了什么分析
------------
第五章 F2（对手损失长期账）的基础数据层：对 card_plays.csv 的每一次打出，
在打出前后的引擎状态上，对三个对手逐一计算完整 V 分解差值，只保留「受影响」
（任一项非零）的对手行。后续 calibrate.py（w 的受害者侧）、analysis2.py
（效率指标 F2）以本表为准。

怎么做的
--------
口径见 Other_analysis_field.md §5.1「F1/F2 统一长期账」「F2 监狱暴露」条：

* 与 F1 同一本长期账：对每个对手，d_x = V项(打出后状态) − V项(打出前状态)，
  五项（净资产/短期租金/长期租金/垄断建房/合计）。打出前 = 决策点引擎状态，
  打出后 = 深拷贝执行实际打出命令后的状态（与 score.py 同一状态对）。
* 行规则：仅当五项任一项非零时输出该对手行（§5.4 的「受影响」定义）。
  抢夺卡跳过（打出点对双方 V 均无影响；受害者损失 −chosen_w 由 calibrate.py
  在抢夺选卡节点以 w 口径回填到 card_plays.csv）。
* 监狱暴露（d_jail_turns_expected，一阶近似，文档已注明）：
  - 已在狱中（attempts=a）：未来 5 个自家回合预计蹲监回合
    E = Σ_{j=0}^{min(5, 3−a)−1} (30/36)^j（假设总选摇骰、双六 6/36 出狱、
    第三次强制付费出狱；a=0 时 E ≈ 2.19）。
  - 自由身：E ≈ P_下一走入狱 × 2.19；P_下一走入狱 = 骰子点数和恰好落 30 的
    概率 + 三连双 1/216 泄漏（与 stat/judge/landing.py 的链同口径）。
    忽略出狱后窗口内再入狱（一阶）。
  - jail_forced：本次打出为陷害卡且该对手是目标时记 1（确定事件）。
* mortgage_delta：该对手按揭地产数 打出后 − 打出前。按 §5.1 预期恒 0
  （卡无按揭副作用）；非 0 时只计数标记，不中断（负责人裁定）。

输出怎么解读
------------
stat/card/data/card_targets.csv（中间文件），每行 = 一次打出 × 一个受影响对手：
  experiment / game_index / decision_index —— 关联键（与 card_plays.csv 同
      decision_index）
  player_id —— 打出者；card_id —— 卡型
  target_seat / target_id / target_controller —— 受影响对手身份
  d_m_assets … d_v_total —— 对该对手的长期账五项影响（负=被打伤）
  jail_forced —— 1 = 被陷害卡直送监狱
  d_jail_turns_expected —— 未来 5 自家回合预计蹲监回合的变化（陷害卡 ≈ +2）
  mortgage_delta —— 按揭地产数变化，预期恒 0；非 0 见运行摘要计数

用法（小样本自测；正式全量需负责人批准）：
    .venv/Scripts/python.exe stat/card/targets.py --games 2 \
        --plays stat/card/data/_dev_plays.csv --out stat/card/data/_dev_targets.csv
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
from replay_tools import ReplayDivergence, iter_decision_points, load_commands  # noqa: E402

from monopoly_agent_battle.domain.commands import (  # noqa: E402
    UseChanceCard,
    UseCommunityGetOutOfJailCard,
)
from monopoly_agent_battle.domain.models import JailStatus  # noqa: E402
from monopoly_agent_battle.game.engine import GameRuleError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent

# 棋盘常量（引擎内为字面量，见 engine.py:365 / board_data/classic_us_40.py:43）
GO_TO_JAIL_SPACE = 30
JAIL_SPACE = 10

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
    "decision_index",
    "player_id",
    "card_id",
    "target_seat",
    "target_id",
    "target_controller",
    "d_m_assets",
    "d_r_short",
    "d_r_long",
    "d_m_monopoly",
    "d_v_total",
    "jail_forced",
    "d_jail_turns_expected",
    "mortgage_delta",
)

JAIL_STAY_PROB = 30 / 36  # 每自家回合摇骰不出双六而留在狱中的概率
TRIPLE_DOUBLES_LEAK = 1 / 216
JAIL_SPACE = 10
K_OWN_TURNS = 5


def _dice_sum_probabilities() -> dict[int, float]:
    probs = {total: 0 for total in range(2, 13)}
    for first in range(1, 7):
        for second in range(1, 7):
            probs[first + second] += 1
    return {total: count / 36 for total, count in probs.items()}


DICE_PROBS = _dice_sum_probabilities()


def _expected_jail_turns_if_jailed(attempts: int) -> float:
    """E[蹲监回合] for a jailed player over the k-window (roll, doubles out)."""
    remaining = max(0, min(3 - attempts, K_OWN_TURNS))
    return sum(JAIL_STAY_PROB**j for j in range(remaining))


def _jail_entry_probability(position: int) -> float:
    """P(下一次移动被送进监狱) = 落 30 的骰面概率 + 三连双泄漏（一阶）。"""
    distance = (GO_TO_JAIL_SPACE - position) % 40
    direct = DICE_PROBS.get(distance, 0.0)
    return direct + TRIPLE_DOUBLES_LEAK


def _expected_jail_turns(state, player_id: str) -> float:
    player = state.players[player_id]
    if player.jail_status is not JailStatus.FREE:
        return _expected_jail_turns_if_jailed(player.jail_roll_attempts)
    if player.bankrupt:
        return 0.0
    return _jail_entry_probability(player.position) * _expected_jail_turns_if_jailed(0)


def _mortgage_count(state, player_id: str) -> int:
    return sum(
        1
        for property_state in state.properties.values()
        if property_state.owner_id == player_id and property_state.mortgaged
    )


def _load_play_index(plays_path: Path) -> dict[tuple[str, int], dict[int, dict[str, str]]]:
    play_index: dict[tuple[str, int], dict[int, dict[str, str]]] = defaultdict(dict)
    with plays_path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            key = (row["experiment"], int(row["game_index"]))
            play_index[key][int(row["decision_index"])] = row
    return play_index


def process_game(
    directory,
    experiment: str,
    game_index: int,
    plays: dict[int, dict[str, str]],
    anomalies: dict[str, int],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    config, _commands = load_commands(directory)
    controller_of = {player.player_id: player.controller_type for player in config.players}
    try:
        for point in iter_decision_points(directory):
            command = point.command
            if not isinstance(command, (UseChanceCard, UseCommunityGetOutOfJailCard)):
                continue
            play_row = plays.get(point.index)
            if play_row is None:
                anomalies["play_missing_in_plays_csv"] += 1
                continue
            if command.card_id == "chance-steal":
                continue  # 抢夺卡：打出点无 V 影响，w 口径由 calibrate.py 处理

            engine = point.engine
            actor_id = point.player_id
            opponents = [pid for pid in engine.state.players if pid != actor_id]
            before = {pid: value_module.evaluate(engine.state, pid) for pid in opponents}
            before_jail = {pid: _expected_jail_turns(engine.state, pid) for pid in opponents}
            before_mortgage = {pid: _mortgage_count(engine.state, pid) for pid in opponents}

            clone = deepcopy(engine)
            try:
                clone.execute(command)
            except GameRuleError:
                anomalies["executed_command_failed"] += 1
                continue

            target_id = command.target_player_id if isinstance(command, UseChanceCard) else None
            for pid in opponents:
                after = value_module.evaluate(clone.state, pid)
                base = before[pid]
                d_m = after.m_assets - base.m_assets
                d_rs = after.r_short - base.r_short
                d_rl = after.r_long - base.r_long
                d_mm = after.m_monopoly - base.m_monopoly
                d_v = after.total - base.total
                if max(abs(d_m), abs(d_rs), abs(d_rl), abs(d_mm), abs(d_v)) < 1e-9:
                    if target_id == pid:
                        anomalies["explicit_target_zero_effect"] += 1
                    continue
                jail_after = _expected_jail_turns(clone.state, pid)
                mortgage_after = _mortgage_count(clone.state, pid)
                jail_forced = int(
                    isinstance(command, UseChanceCard)
                    and command.card_id == "chance-jail"
                    and target_id == pid
                )
                mortgage_delta = mortgage_after - before_mortgage[pid]
                if mortgage_delta != 0:
                    anomalies["mortgage_delta_nonzero"] += 1
                rows.append(
                    {
                        "experiment": experiment,
                        "game_index": game_index,
                        "decision_index": point.index,
                        "player_id": actor_id,
                        "card_id": command.card_id,
                        "target_seat": clone.state.players[pid].seat,
                        "target_id": pid,
                        "target_controller": controller_of.get(pid, ""),
                        "d_m_assets": d_m,
                        "d_r_short": d_rs,
                        "d_r_long": d_rl,
                        "d_m_monopoly": d_mm,
                        "d_v_total": d_v,
                        "jail_forced": jail_forced,
                        "d_jail_turns_expected": jail_after - before_jail[pid],
                        "mortgage_delta": mortgage_delta,
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
    parser.add_argument("--plays", required=True, help="card_plays.csv 路径")
    parser.add_argument("--out", required=True, help="输出 CSV 路径")
    args = parser.parse_args()

    play_index = _load_play_index(Path(args.plays))
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
                rows = process_game(
                    directory,
                    experiment,
                    position,
                    play_index.get((experiment, position), {}),
                    anomalies,
                )
                writer.writerows(rows)
                total_rows += len(rows)
            print(f"{experiment}: {len(directories)} games", file=sys.stderr)

    print(f"rows: {total_rows} -> {out_path}", file=sys.stderr)
    print(f"ANOMALIES: {dict(anomalies) if anomalies else 'none'}", file=sys.stderr)


if __name__ == "__main__":
    main()
