"""分析二：不同架构/供应商 AI 的出牌倾向与效率（地板校正）。

做了什么分析
------------
Other_analysis_field.md §5.3：把出牌行为建成架构/供应商比较的证据维度——
倾向画像（F0 持有期、F4 使用率）与效率（F1/F2/F3 的 V 值与目标损耗），
地板对局（greedy / sane）作为校正值。三类对比结构（§5.3 冻结）：

* court-vs-baseline 64 局 = 唯一正式结论来源：局内配对差 Δ_g = 朝廷席指标 −
  三个 baseline 席均值，1,000 次标签置换（局内随机指定焦点席）给 p 值，
  局 cluster bootstrap 10,000 次给 CI，指标族（5 个指标）Holm 校正。
* 4-courts-battle / fe-vs-baseline / court-fe-battle 走同一管线只报数值
  （formal=0）；4-courts 同时提供零点定标带：同架构标签下各焦点席的
  |Δ| 上限（null_band_width），架构差须超出才算数。
* 供应商维度只落到 seat-game 表的 vendor 列（model_profile；朝廷等多模型
  组合回退 controller_type），供事后分组描述，不做检验。

三道自检（§5.3，2026-09-25 经负责人裁定修订）：① 零点定标带（见上）；
② 平衡：court-vs-baseline 的架构对比在抽卡质量 Q 上的差必须 ≈ 0
（balance_q_delta 列）；③ 指标族 Holm 校正。原 sane-vs-greedy 阳性对照
经负责人裁定删除：greedy_script 的卡目标选择是「顺时针最近合法目标」
启发式（agents/greedy_script.py:29），并非 V 最优，「sane 损耗必然更大」
的前提不成立；仪器正确性由 score.py 与 §6 评分器的位精确机器一致性保证。

怎么做的
--------
* 座位-局指标表：usage_rate（打出 ÷ 可打决策；分母由本模块对 LLM 四实验
  重放统计——该席手牌有 ≥1 合法候选的资产管理决策数 + 持有出狱卡时的坐牢
  决策数，与 calibrate.py 地板口径一致）、持有期均值（含/不含 censored
  双口径）、executed_dv_mean / target_loss_mean / d_v_total_mean
  （card_plays.csv）、opp_d_v_total_mean（card_targets.csv 该席打出对
  对手的平均影响）、n_plays / n_playable_points。
* 配对差：Δ_g = 焦点席 − 对照席均值（court-fe-battle 为朝廷席 − FE 席）；
  指标缺失（该席 0 打出）的局按指标剔除并计入 n_games_used。
* 置换：每次置换在局内均匀随机指定焦点席重算 Δ̄，p = |置换 Δ̄| ≥ |实际 Δ̄|
  的比例（RNG 种子固定）；court-fe-battle 二席时退化为符号翻转。
* 卡型画像表：experiment × controller × card_id 的打出/持有/效率均值与
  loss_share_above_p90 / loss_share_below_p50（对 greedy 地板 P50/P90
  分档，原始损耗只在同卡型内比较，§5.3 指标条）；地板参照行的使用率与
  分位取 card_type_calibration.csv。

输出怎么解读
------------
data/analysis2_seat_game_metrics.csv（中间文件）：每行 = 一局一座位的全部指标
  （配对检验的输入；vendor 列供供应商分组描述）。
stat/card/analysis2_card_type_summary.csv（顶层结果表）：每行 = 实验 × 控制器
  × 卡型的画像（含 greedy_script / sane_random 地板参照行）；
  loss_share_above_p90 高 = 该群体在此卡型上经常打出地板最差 10% 级别的目标。
stat/card/analysis2_pairwise.csv（顶层结果表）：每行 = 一个 实验 × 指标 ×
  对比 的数值：formal=1 仅 court-vs-baseline；holm_significant 只对 formal
  族有意义；null_band_width / balance_q_delta 为自检列，分别只出现在
  4-courts 行 / court-vs-baseline 行。

目录约定（§5.4 冻结）：结果表放 stat/card/ 顶层（--out-summary /
--out-pairwise），中间文件放 stat/card/data/（--out-seat）。

用法（小样本自测；正式全量需负责人批准）：
    .venv/Scripts/python.exe stat/card/analysis2.py --games 5 \
        --instances stat/card/data/_dev_instances.csv \
        --plays stat/card/data/_dev_plays_backfilled.csv \
        --targets stat/card/data/_dev_targets.csv \
        --calibration stat/card/data/_dev_calibration.csv \
        --quality stat/card/data/_dev_draw_quality.csv \
        --out-seat stat/card/data/_dev_seat_game_metrics.csv \
        --out-summary stat/card/_dev_card_type_summary.csv \
        --out-pairwise stat/card/_dev_pairwise.csv
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from collections import defaultdict
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "judge"))

import evaluate as evaluate_module  # noqa: E402
from replay_tools import ReplayDivergence, iter_decision_points  # noqa: E402

from monopoly_agent_battle.decision.requests import _candidate_commands  # noqa: E402
from monopoly_agent_battle.domain.commands import UseChanceCard  # noqa: E402
from monopoly_agent_battle.domain.models import JailStatus, TurnPhase  # noqa: E402
from monopoly_agent_battle.game.engine import GameRuleError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent

LLM_EXPERIMENTS = (
    "4-courts-battle",
    "court-vs-baseline",
    "fe-vs-baseline",
    "court-fe-battle",
)
FLOOR_EXPERIMENTS = ("greedy_script", "sane_random")
EXPERIMENTS = LLM_EXPERIMENTS + FLOOR_EXPERIMENTS

METRICS = (
    "usage_rate",
    "held_rounds_mean_excl_censored",
    "executed_dv_mean",
    "target_loss_mean",
    "opp_d_v_total_mean",
)

SEAT_COLUMNS = (
    "experiment",
    "game_index",
    "seat",
    "player_id",
    "controller",
    "vendor",
    "usage_rate",
    "held_rounds_mean_excl_censored",
    "held_rounds_mean_incl_censored",
    "executed_dv_mean",
    "target_loss_mean",
    "d_v_total_mean",
    "opp_d_v_total_mean",
    "n_plays",
    "n_playable_points",
)
SUMMARY_COLUMNS = (
    "experiment",
    "controller",
    "card_id",
    "n_plays",
    "n_instances",
    "usage_rate",
    "held_rounds_median",
    "held_rounds_iqr",
    "play_round_median",
    "executed_dv_mean",
    "max_target_dv_mean",
    "target_loss_mean",
    "loss_share_above_p90",
    "loss_share_below_p50",
    "d_v_total_mean",
    "opp_d_v_total_mean",
)
PAIRWISE_COLUMNS = (
    "experiment",
    "metric",
    "contrast",
    "n_games_used",
    "delta",
    "ci_lo",
    "ci_hi",
    "p_permutation",
    "holm_significant",
    "formal",
    "null_band_width",
    "balance_q_delta",
)

BOOT_RESAMPLES = 10_000
PERMUTATIONS = 1_000
RNG_SEED = 20260924


def _load_card_calibrate():
    """Load stat/card/calibrate.py explicitly (stat/judge has a namesake)."""
    spec = importlib.util.spec_from_file_location(
        "card_calibrate", Path(__file__).resolve().parent / "calibrate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass(slots=True) 需要模块已注册
    spec.loader.exec_module(module)
    return module


_card_calibrate = _load_card_calibrate()
POPULATION_OF = _card_calibrate.POPULATION_OF


def _playable_counts_scan(
    experiments: tuple[str, ...], games_limit: int | None, anomalies: dict[str, int]
) -> tuple[
    dict[tuple[str, int, int], int],
    dict[tuple[str, int, int, str], int],
]:
    """Playable-decision counts via replay: per seat-game (any card) + per card."""
    seat_counts: dict[tuple[str, int, int], int] = defaultdict(int)
    card_counts: dict[tuple[str, int, int, str], int] = defaultdict(int)
    for experiment in experiments:
        directories = evaluate_module.experiment_directories(experiment)
        if games_limit is not None:
            directories = directories[:games_limit]
        for game_index, directory in enumerate(directories):
            try:
                for point in iter_decision_points(directory):
                    engine = point.engine
                    state = engine.state
                    player = state.players[point.player_id]
                    if state.turn_phase is TurnPhase.ASSET_MANAGEMENT:
                        held = set(player.chance_cards)
                        if not held:
                            continue
                        candidates_by_card: dict[str, list] = defaultdict(list)
                        for candidate in _candidate_commands(engine, point.player_id):
                            if isinstance(candidate, UseChanceCard):
                                candidates_by_card[candidate.card_id].append(candidate)
                        any_playable = False
                        for card_id, candidates in candidates_by_card.items():
                            if card_id not in held:
                                continue
                            for candidate in candidates:
                                clone = deepcopy(engine)
                                try:
                                    clone.execute(candidate)
                                except GameRuleError:
                                    continue
                                card_counts[(experiment, game_index, player.seat, card_id)] += 1
                                any_playable = True
                                break
                        if any_playable:
                            seat_counts[(experiment, game_index, player.seat)] += 1
                    elif player.jail_status is JailStatus.ROLLING:
                        if player.community_get_out_of_jail_cards:
                            seat_counts[(experiment, game_index, player.seat)] += 1
                            card_counts[
                                (experiment, game_index, player.seat, "community-jail-free")
                            ] += 1
            except ReplayDivergence:
                anomalies["replay_divergence"] += 1
        print(f"{experiment}: {len(directories)} games scanned", file=sys.stderr)
    return seat_counts, card_counts


def _vendor_of(players_cfg: list[dict], player_id: str, controller: str) -> str:
    for player_cfg in players_cfg:
        if str(player_cfg.get("player_id")) == player_id:
            profile = player_cfg.get("model_profile")
            return str(profile) if profile else controller
    return controller


def _mean(values: list[float]) -> float:
    return float(np.mean(values)) if values else float("nan")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--games", type=int, default=None, help="每实验最多处理局数（小样本自测用）"
    )
    parser.add_argument("--instances", required=True)
    parser.add_argument("--plays", required=True, help="回填后的 card_plays.csv")
    parser.add_argument("--targets", required=True)
    parser.add_argument("--calibration", required=True)
    parser.add_argument("--quality", required=True, help="analysis1_draw_quality.csv")
    parser.add_argument("--out-seat", required=True)
    parser.add_argument("--out-summary", required=True)
    parser.add_argument("--out-pairwise", required=True)
    args = parser.parse_args()

    anomalies: dict[str, int] = defaultdict(int)
    rng = np.random.default_rng(RNG_SEED)

    with open(args.instances, encoding="utf-8-sig") as handle:
        instance_rows = list(csv.DictReader(handle))
    with open(args.plays, encoding="utf-8-sig") as handle:
        play_rows = list(csv.DictReader(handle))
    with open(args.targets, encoding="utf-8-sig") as handle:
        target_rows = list(csv.DictReader(handle))
    with open(args.calibration, encoding="utf-8-sig") as handle:
        calibration_rows = list(csv.DictReader(handle))
    with open(args.quality, encoding="utf-8-sig") as handle:
        quality_rows = list(csv.DictReader(handle))

    seat_playable, card_playable = _playable_counts_scan(LLM_EXPERIMENTS, args.games, anomalies)

    # ---- 座位-局骨架（config 给身份与 vendor）----
    seat_rows: list[dict[str, object]] = []
    seat_index: dict[tuple[str, int, int], dict[str, object]] = {}
    for experiment in EXPERIMENTS:
        directories = evaluate_module.experiment_directories(experiment)
        limit = args.games if args.games is not None else len(directories)
        for game_index, directory in enumerate(directories[:limit]):
            config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
            players_cfg = config["config"]["players"]
            for player_cfg in players_cfg:
                player_id = str(player_cfg["player_id"])
                seat = int(player_cfg["seat"])
                controller = str(player_cfg["controller_type"])
                row: dict[str, object] = {
                    "experiment": experiment,
                    "game_index": game_index,
                    "seat": seat,
                    "player_id": player_id,
                    "controller": controller,
                    "vendor": _vendor_of(players_cfg, player_id, controller),
                }
                seat_rows.append(row)
                seat_index[(experiment, game_index, seat)] = row

    seat_of_pid: dict[tuple[str, int, str], int] = {
        (key[0], key[1], str(row["player_id"])): key[2] for key, row in seat_index.items()
    }

    held_by_seat: dict[tuple[str, int, int], dict[str, list[float]]] = defaultdict(
        lambda: {"incl": [], "excl": []}
    )
    for row in instance_rows:
        key = (row["experiment"], int(row["game_index"]), int(row["seat"]))
        if key not in seat_index:
            continue
        held_by_seat[key]["incl"].append(float(row["held_rounds"]))
        if row["censored"] != "1":
            held_by_seat[key]["excl"].append(float(row["held_rounds"]))

    plays_by_seat: dict[tuple[str, int, int], list[dict[str, str]]] = defaultdict(list)
    for row in play_rows:
        key = (row["experiment"], int(row["game_index"]), int(row["seat"]))
        plays_by_seat[key].append(row)

    opp_by_seat: dict[tuple[str, int, int], list[float]] = defaultdict(list)
    for row in target_rows:
        seat = seat_of_pid.get((row["experiment"], int(row["game_index"]), row["player_id"]))
        if seat is not None:
            opp_by_seat[(row["experiment"], int(row["game_index"]), seat)].append(
                float(row["d_v_total"])
            )

    for key, row in seat_index.items():
        held = held_by_seat.get(key, {"incl": [], "excl": []})
        row["held_rounds_mean_excl_censored"] = _mean(held["excl"])
        row["held_rounds_mean_incl_censored"] = _mean(held["incl"])
        plays = plays_by_seat.get(key, [])
        row["n_plays"] = len(plays)
        row["n_playable_points"] = seat_playable.get(key, 0)
        row["executed_dv_mean"] = _mean([float(p["executed_dv"]) for p in plays])
        row["target_loss_mean"] = _mean([float(p["target_loss"]) for p in plays])
        row["d_v_total_mean"] = _mean([float(p["d_v_total"]) for p in plays])
        row["opp_d_v_total_mean"] = _mean(opp_by_seat.get(key, []))
        playable_points = float(row["n_playable_points"])
        row["usage_rate"] = len(plays) / playable_points if playable_points > 0 else float("nan")

    seat_path = Path(args.out_seat)
    seat_path.parent.mkdir(parents=True, exist_ok=True)
    with seat_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SEAT_COLUMNS))
        writer.writeheader()
        writer.writerows(seat_rows)

    # ---- 配对检验框架 ----
    def paired_rows(
        experiment: str,
        focal_of: Callable[[dict[int, dict]], tuple[int | None, list[int]]],
    ) -> dict[str, list[tuple[float, float]]]:
        per_metric: dict[str, list[tuple[float, float]]] = {metric: [] for metric in METRICS}
        game_indices = sorted({key[1] for key in seat_index if key[0] == experiment})
        for game_index in game_indices:
            seats = {
                seat: seat_index[(experiment, game_index, seat)]
                for seat in range(1, 5)
                if (experiment, game_index, seat) in seat_index
            }
            focal_seat, control_seats = focal_of(seats)
            if focal_seat is None or focal_seat not in seats or not control_seats:
                continue
            for metric in METRICS:
                focal_value = float(seats[focal_seat].get(metric, float("nan")))
                control_values = [
                    float(seats[seat][metric])
                    for seat in control_seats
                    if seat in seats and not np.isnan(float(seats[seat].get(metric, float("nan"))))
                ]
                if np.isnan(focal_value) or not control_values:
                    continue
                per_metric[metric].append((focal_value, _mean(control_values)))
        return per_metric

    def test_contrast(
        experiment: str,
        contrast: str,
        per_metric: dict[str, list[tuple[float, float]]],
        formal: int,
        extra: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        game_indices = sorted({key[1] for key in seat_index if key[0] == experiment})
        for metric in METRICS:
            pairs = per_metric[metric]
            if not pairs:
                continue
            deltas = np.array([focal - control for focal, control in pairs])
            estimate = float(deltas.mean())
            n_games = len(deltas)
            boot_indices = rng.integers(0, n_games, size=(BOOT_RESAMPLES, n_games))
            boot_means = deltas[boot_indices].mean(axis=1)
            ci_lo, ci_hi = np.percentile(boot_means, [2.5, 97.5])
            seat_values: list[list[float]] = []
            for game_index in game_indices:
                values = [
                    float(seat_index[(experiment, game_index, seat)][metric])
                    for seat in range(1, 5)
                    if (experiment, game_index, seat) in seat_index
                    and not np.isnan(
                        float(seat_index[(experiment, game_index, seat)].get(metric, float("nan")))
                    )
                ]
                if len(values) == 4:
                    seat_values.append(values)
            if seat_values:
                values_array = np.array(seat_values)
                focal_choices = rng.integers(0, 4, size=(PERMUTATIONS, len(seat_values)))
                permuted = np.zeros(PERMUTATIONS)
                for permutation in range(PERMUTATIONS):
                    choices = focal_choices[permutation]
                    focal_vals = values_array[np.arange(len(seat_values)), choices]
                    others_sum = values_array.sum(axis=1) - focal_vals
                    permuted[permutation] = float(np.mean(focal_vals - others_sum / 3))
                p_value = float(
                    (np.sum(np.abs(permuted) >= abs(estimate)) + 1) / (PERMUTATIONS + 1)
                )
            else:
                p_value = float("nan")
            row: dict[str, object] = {
                "experiment": experiment,
                "metric": metric,
                "contrast": contrast,
                "n_games_used": n_games,
                "delta": estimate,
                "ci_lo": float(ci_lo),
                "ci_hi": float(ci_hi),
                "p_permutation": p_value,
                "holm_significant": "",
                "formal": formal,
                "null_band_width": "",
                "balance_q_delta": "",
            }
            if extra:
                row.update(extra)
            rows.append(row)
        return rows

    pairwise: list[dict[str, object]] = []

    def cvb_focal(seats: dict[int, dict]) -> tuple[int | None, list[int]]:
        for seat, row in seats.items():
            if row["controller"] != "llm_baseline":
                return seat, [s for s in seats if s != seat]
        return None, []

    # 平衡自检（court-vs-baseline 的 Q 对比）
    q_by_seat_game: dict[tuple[str, int, int], float] = {
        (row["experiment"], int(row["game_index"]), int(row["seat"])): float(row["q_total"])
        for row in quality_rows
    }
    balance_deltas: list[float] = []
    for (experiment, game_index, seat), row in seat_index.items():
        if experiment != "court-vs-baseline" or row["controller"] == "llm_baseline":
            continue
        court_q = q_by_seat_game.get((experiment, game_index, seat))
        others = [
            q_by_seat_game[(experiment, game_index, other)]
            for other in range(1, 5)
            if other != seat and (experiment, game_index, other) in q_by_seat_game
        ]
        if court_q is not None and len(others) == 3:
            balance_deltas.append(court_q - _mean(others))
    balance_q_delta: object = _mean(balance_deltas) if balance_deltas else ""

    pairwise += test_contrast(
        "court-vs-baseline",
        "court-mean3baseline",
        paired_rows("court-vs-baseline", cvb_focal),
        formal=1,
        extra={"balance_q_delta": balance_q_delta},
    )

    def fevb_focal(seats: dict[int, dict]) -> tuple[int | None, list[int]]:
        for seat, row in seats.items():
            if row["controller"] == "flat_ensemble":
                return seat, [s for s in seats if s != seat]
        return None, []

    pairwise += test_contrast(
        "fe-vs-baseline",
        "fe-mean3baseline",
        paired_rows("fe-vs-baseline", fevb_focal),
        formal=0,
    )

    def cfb_focal(seats: dict[int, dict]) -> tuple[int | None, list[int]]:
        court = next(
            (
                seat
                for seat, row in seats.items()
                if row["controller"] not in ("flat_ensemble", "llm_baseline")
            ),
            None,
        )
        fe = next(
            (seat for seat, row in seats.items() if row["controller"] == "flat_ensemble"),
            None,
        )
        if court is None or fe is None:
            return None, []
        return court, [fe]

    pairwise += test_contrast(
        "court-fe-battle",
        "court-fe",
        paired_rows("court-fe-battle", cfb_focal),
        formal=0,
    )

    courts = sorted(
        {str(row["controller"]) for key, row in seat_index.items() if key[0] == "4-courts-battle"}
    )
    per_court_abs: dict[str, dict[str, float]] = defaultdict(dict)
    four_courts_rows: list[dict[str, object]] = []
    for court in courts:

        def make_focal(target_court: str):
            def focal(seats: dict[int, dict]) -> tuple[int | None, list[int]]:
                for seat, row in seats.items():
                    if row["controller"] == target_court:
                        return seat, [s for s in seats if s != seat]
                return None, []

            return focal

        rows = test_contrast(
            "4-courts-battle",
            f"focal:{court}",
            paired_rows("4-courts-battle", make_focal(court)),
            formal=0,
        )
        for row in rows:
            per_court_abs[court][str(row["metric"])] = abs(float(row["delta"]))
        four_courts_rows += rows
    for row in four_courts_rows:
        metric = str(row["metric"])
        band = max((per_court_abs[court].get(metric, 0.0) for court in courts), default=0.0)
        row["null_band_width"] = band
    pairwise += four_courts_rows

    # 自检③：Holm（court-vs-baseline 正式族）
    cvb_rows = [row for row in pairwise if row["experiment"] == "court-vs-baseline"]

    def _p_key(index: int) -> float:
        p_value = float(cvb_rows[index]["p_permutation"])
        return p_value if not np.isnan(p_value) else 1.0

    order = sorted(range(len(cvb_rows)), key=_p_key)
    m = len(order)
    for rank, index in enumerate(order, start=1):
        p_value = _p_key(index)
        cvb_rows[index]["holm_significant"] = int(p_value <= 0.05 / (m - rank + 1))
        if not cvb_rows[index]["holm_significant"]:
            for rest in order[rank:]:
                cvb_rows[rest]["holm_significant"] = 0
            break

    pairwise_path = Path(args.out_pairwise)
    pairwise_path.parent.mkdir(parents=True, exist_ok=True)
    with pairwise_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(PAIRWISE_COLUMNS))
        writer.writeheader()
        writer.writerows(pairwise)

    # ---- 卡型画像 ----
    tiers: dict[tuple[str, str], tuple[float, float, float, int]] = {}
    for row in calibration_rows:
        tiers[(row["card_id"], row["population"])] = (
            float(row["loss_p50"]) if row["loss_p50"] != "" else float("nan"),
            float(row["loss_p90"]) if row["loss_p90"] != "" else float("nan"),
            float(row["usage_rate_floor"]) if row["usage_rate_floor"] != "" else float("nan"),
            int(row["n_plays"]),
        )

    groups: dict[tuple[str, str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in play_rows:
        key = (row["experiment"], row["controller"], row["card_id"])
        groups[key]["executed"].append(float(row["executed_dv"]))
        groups[key]["max_target"].append(float(row["max_target_dv"]))
        groups[key]["loss"].append(float(row["target_loss"]))
        groups[key]["d_v"].append(float(row["d_v_total"]))
        groups[key]["round"].append(float(row["round"]))

    instances_count: dict[tuple[str, str, str], int] = defaultdict(int)
    held_values: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in instance_rows:
        key = (row["experiment"], row["controller"], row["card_id"])
        instances_count[key] += 1
        held_values[key].append(float(row["held_rounds"]))

    controller_of_pid: dict[tuple[str, int, str], str] = {
        (key[0], key[1], str(row["player_id"])): str(row["controller"])
        for key, row in seat_index.items()
    }
    opp_by_group: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in target_rows:
        controller = controller_of_pid.get(
            (row["experiment"], int(row["game_index"]), row["player_id"])
        )
        if controller is not None:
            opp_by_group[(row["experiment"], controller, row["card_id"])].append(
                float(row["d_v_total"])
            )

    # 每控制器×卡型的可打决策数（LLM 实验来自扫描；地板来自校准表使用率反推无用，
    # 地板行的 usage_rate 直接取 usage_rate_floor）
    playable_by_group: dict[tuple[str, str, str], int] = defaultdict(int)
    for (experiment, game_index, seat, card_id), count in card_playable.items():
        row = seat_index.get((experiment, game_index, seat))
        if row is not None:
            playable_by_group[(experiment, str(row["controller"]), card_id)] += count

    summary_path = Path(args.out_summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SUMMARY_COLUMNS))
        writer.writeheader()
        for (experiment, controller, card_id), values in sorted(groups.items()):
            losses = np.array(values["loss"])
            population = POPULATION_OF[experiment]
            p50, p90, floor_usage, _n = tiers.get((card_id, population), (float("nan"),) * 4)
            held = held_values.get((experiment, controller, card_id), [])
            held_arr = np.array(held) if held else np.array([float("nan")])
            n_plays = len(values["executed"])
            if population == "llm":
                denominator = playable_by_group.get((experiment, controller, card_id), 0)
                usage = n_plays / denominator if denominator else ""
            else:
                usage = "" if np.isnan(floor_usage) else floor_usage
            writer.writerow(
                {
                    "experiment": experiment,
                    "controller": controller,
                    "card_id": card_id,
                    "n_plays": n_plays,
                    "n_instances": instances_count.get((experiment, controller, card_id), 0),
                    "usage_rate": usage,
                    "held_rounds_median": float(np.median(held_arr)),
                    "held_rounds_iqr": float(
                        np.percentile(held_arr, 75) - np.percentile(held_arr, 25)
                    ),
                    "play_round_median": float(np.median(values["round"])),
                    "executed_dv_mean": _mean(values["executed"]),
                    "max_target_dv_mean": _mean(values["max_target"]),
                    "target_loss_mean": _mean(values["loss"]),
                    "loss_share_above_p90": (
                        float(np.mean(losses > p90)) if not np.isnan(p90) else ""
                    ),
                    "loss_share_below_p50": (
                        float(np.mean(losses <= p50)) if not np.isnan(p50) else ""
                    ),
                    "d_v_total_mean": _mean(values["d_v"]),
                    "opp_d_v_total_mean": _mean(
                        opp_by_group.get((experiment, controller, card_id), [])
                    ),
                }
            )

    print(f"seat rows: {len(seat_rows)} -> {seat_path}", file=sys.stderr)
    print(f"pairwise rows: {len(pairwise)} -> {pairwise_path}", file=sys.stderr)
    print(f"summary -> {summary_path}", file=sys.stderr)
    print(f"ANOMALIES: {dict(anomalies) if anomalies else 'none'}", file=sys.stderr)


if __name__ == "__main__":
    main()
