"""分析一：抽卡质量与对局胜负的关系（检验 1 + 检验 2）。

做了什么分析
------------
Other_analysis_field.md §5.2 的两个检验，回答「108 个 LLM 对局胜负是否和
抽卡质量有关、地板对局是否也如此」：

* 检验 1（运气→胜负）：构造每个座位的抽卡质量 Q = 非抢夺获得卡（开局发牌
  + 机会抽中 + 社区抽中）的 w_greedy 总和（w 来自 card_type_calibration.csv，
  即该卡型在 greedy 地板的平均可兑现 V）。在三个人群（llm 108 局 /
  greedy 800 局 / sane 800 局）内分别做局内去均值回归 Q → 积分（主口径）
  与 Q → 终局净资产（灵敏口径）：同局四席互比，剥离局级混叠。局 cluster
  bootstrap 10,000 次给斜率 CI 与最小可分辨效应（MDE = 1.96 × bootstrap SD）。
* 检验 2（运气→兑现）：逐次打出回归 executed_dv ~ max_target_dv（含截距，
  样本 = 机会卡打出，剔除抢夺卡与出狱卡——前者 ΔV 恒 0 走 w 口径、后者为
  社区卡；zero_dv 的 (0,0) 点保留），斜率 b = 兑现率。主判定：
  b_llm − b_greedy 的 CI 下限 > 0 → LLM 更大程度发挥了卡的价值；
  b_sane 为下限 sanity；伴随数：max 上四分位子样本的 executed 均值。
* 判定逻辑（§5.2 冻结）：地板任一斜率 > 0（CI 不含 0）而 llm 斜率 CI 含 0
  且斜率差 CI 不含 0 → decisions_override_luck；全部 CI 含 0 →
  all_slopes_zero_report_mde（检不出运气层，报 MDE）；llm 与地板斜率差
  不显著 → llm_same_as_floor_no_extra_absorption；其余 inconclusive。

怎么做的
--------
* Q 只加总、不平均（冻结口径：多抽多得）；w 缺失卡型按 0 计并计数报告。
* 局内去均值后斜率 = Σ_g Sxy_g / Σ_g Sxx_g（Sxy/Sxx 为局内中心化平方和，
  局可加），bootstrap 重采样局后重新加总——与逐局重算严格等价。
* 检验 2 的斜率按含截距 OLS 的正规方程由局级五和（n, Σx, Σy, Σxx, Σxy）
  聚合，bootstrap 同样局重采样。斜率差 = 两人群独立 bootstrap 相减。
* 结局取自 result.json：积分 = 名次映射 3/2/1/0，净资产复用
  stat/judge/ranking.py 的 load_outcomes（§6 同口径）。RNG 种子固定。

输出怎么解读
------------
data/analysis1_draw_quality.csv（中间文件，检验 1 输入，每行 = 一局中一座位）：
  population —— llm / greedy / sane；n_chance_acquired —— 非抢夺获得卡数；
  q_total —— 抽卡质量（货币量，越大手气越好）；points / rank /
  net_worth_final —— 结局三口径。
stat/card/analysis1_results.csv（顶层结果表，每行 = 论文要报的一个数）：
  test —— 检验名:人群（如 test1_slope:llm、test1_slope_diff:llm-greedy、
      test2_capture_rate:sane）；outcome —— points / net_worth / executed_dv；
  estimate + ci_lo/ci_hi —— 点估与 95% CI；n_units —— 局数（检验 1）或
      打出数（检验 2）；min_detectable_effect —— MDE；verdict —— 行级判读。
  test1_verdict / test2_verdict 两行给出冻结判定逻辑的最终结论。

目录约定（§5.4 冻结）：结果表放 stat/card/ 顶层（--out-results），
中间文件放 stat/card/data/（--out-quality）。

用法（小样本自测；正式全量需负责人批准）：
    .venv/Scripts/python.exe stat/card/analysis1.py \
        --instances stat/card/data/_dev_instances.csv \
        --calibration stat/card/data/_dev_calibration.csv \
        --plays stat/card/data/_dev_plays_backfilled.csv \
        --out-quality stat/card/data/_dev_draw_quality.csv \
        --out-results stat/card/_dev_analysis1.csv
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "judge"))

import evaluate as evaluate_module  # noqa: E402
from ranking import load_outcomes  # noqa: E402


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

ROOT = Path(__file__).resolve().parent.parent.parent

EXPERIMENTS = (
    "4-courts-battle",
    "court-vs-baseline",
    "fe-vs-baseline",
    "court-fe-battle",
    "greedy_script",
    "sane_random",
)
POPULATIONS = ("llm", "greedy", "sane")
ACQUIRE_SOURCES_IN_Q = ("initial_deal", "chance_draw", "community_draw")

QUALITY_COLUMNS = (
    "experiment",
    "game_index",
    "seat",
    "player_id",
    "controller",
    "population",
    "n_chance_acquired",
    "q_total",
    "points",
    "rank",
    "net_worth_final",
)
RESULT_COLUMNS = (
    "test",
    "outcome",
    "estimate",
    "ci_lo",
    "ci_hi",
    "n_units",
    "min_detectable_effect",
    "verdict",
)

BOOT_RESAMPLES = 10_000
RNG_SEED = 20260924
Z_975 = 1.959963984540054


def _load_w_greedy(calibration_path: Path, anomalies: dict[str, int]) -> dict[str, float]:
    w: dict[str, float] = {}
    with calibration_path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["population"] != "greedy":
                continue
            value = row["w_type"]
            if value == "":
                continue
            number = float(value)
            if np.isnan(number):
                anomalies["w_nan_skipped"] += 1
                continue
            w[row["card_id"]] = number
    return w


def _build_quality_rows(
    instances_path: Path,
    w: dict[str, float],
    anomalies: dict[str, int],
) -> list[dict[str, object]]:
    acquisitions: dict[tuple[str, int, str], dict[str, object]] = {}
    with instances_path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["acquire_source"] not in ACQUIRE_SOURCES_IN_Q:
                continue
            key = (row["experiment"], int(row["game_index"]), row["player_id"])
            entry = acquisitions.setdefault(
                key,
                {
                    "experiment": row["experiment"],
                    "game_index": int(row["game_index"]),
                    "seat": int(row["seat"]),
                    "player_id": row["player_id"],
                    "controller": row["controller"],
                    "population": POPULATION_OF[row["experiment"]],
                    "n_chance_acquired": 0,
                    "q_total": 0.0,
                },
            )
            entry["n_chance_acquired"] += 1
            weight = w.get(row["card_id"])
            if weight is None:
                anomalies["q_missing_w"] += 1
                weight = 0.0
            entry["q_total"] += weight

    names_by_experiment: dict[str, list[str]] = {}
    for experiment in EXPERIMENTS:
        names_by_experiment[experiment] = [
            directory.name for directory in evaluate_module.experiment_directories(experiment)
        ]

    rows: list[dict[str, object]] = []
    missing_outcome = 0
    for (experiment, game_index, _player_id), entry in sorted(acquisitions.items()):
        names = names_by_experiment.get(experiment, [])
        if game_index >= len(names):
            missing_outcome += 1
            continue
        entry["game_name"] = names[game_index]
        rows.append(entry)
    if missing_outcome:
        anomalies["quality_missing_game_name"] += missing_outcome

    outcomes_cache: dict[str, dict[str, dict[str, tuple[float, ...]]]] = {}
    for experiment in EXPERIMENTS:
        needed = sorted({row["game_name"] for row in rows if row["experiment"] == experiment})
        if needed:
            outcomes_cache[experiment] = load_outcomes(experiment, needed)

    final_rows: list[dict[str, object]] = []
    for row in rows:
        outcome = outcomes_cache[row["experiment"]].get(row["game_name"], {}).get(str(row["seat"]))
        if outcome is None:
            anomalies["quality_missing_outcome"] += 1
            continue
        points, rank, worth = outcome
        final_rows.append(
            {
                "experiment": row["experiment"],
                "game_index": row["game_index"],
                "seat": row["seat"],
                "player_id": row["player_id"],
                "controller": row["controller"],
                "population": row["population"],
                "n_chance_acquired": row["n_chance_acquired"],
                "q_total": row["q_total"],
                "points": points,
                "rank": rank,
                "net_worth_final": worth,
            }
        )
    return final_rows


def _demeaned_slope_test(
    rows: list[dict[str, object]], outcome_key: str, rng: np.random.Generator
) -> tuple[float, float, float, float, int]:
    """Within-game demeaned slope with game-cluster bootstrap CI and MDE."""
    by_game: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        by_game[(row["experiment"], int(row["game_index"]))].append(
            (float(row["q_total"]), float(row[outcome_key]))
        )
    sxy = np.zeros(len(by_game))
    sxx = np.zeros(len(by_game))
    for position, pairs in enumerate(by_game.values()):
        pairs_array = np.asarray(pairs)
        q_centered = pairs_array[:, 0] - pairs_array[:, 0].mean()
        y_centered = pairs_array[:, 1] - pairs_array[:, 1].mean()
        sxy[position] = float(q_centered @ y_centered)
        sxx[position] = float(q_centered @ q_centered)
    denominator = sxx.sum()
    if denominator <= 0:
        return (float("nan"),) * 4 + (len(by_game),)
    estimate = float(sxy.sum() / denominator)
    indices = rng.integers(0, len(by_game), size=(BOOT_RESAMPLES, len(by_game)))
    boot_sxy = sxy[indices].sum(axis=1)
    boot_sxx = sxx[indices].sum(axis=1)
    valid = boot_sxx > 0
    slopes = boot_sxy[valid] / boot_sxx[valid]
    ci_lo, ci_hi = np.percentile(slopes, [2.5, 97.5])
    mde = Z_975 * float(np.std(slopes, ddof=1))
    return estimate, float(ci_lo), float(ci_hi), mde, len(by_game)


def _capture_rate_test(
    plays: list[dict[str, str]], rng: np.random.Generator
) -> tuple[float, float, float, float, int]:
    """Per-play executed~max OLS (intercept) with game-cluster bootstrap."""
    by_game: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    for row in plays:
        by_game[(row["experiment"], int(row["game_index"]))].append(
            (float(row["max_target_dv"]), float(row["executed_dv"]))
        )
    n_games = len(by_game)
    counts = np.zeros(n_games)
    sx = np.zeros(n_games)
    sy = np.zeros(n_games)
    sxx = np.zeros(n_games)
    sxy = np.zeros(n_games)
    for position, pairs in enumerate(by_game.values()):
        pairs_array = np.asarray(pairs)
        counts[position] = len(pairs)
        sx[position] = pairs_array[:, 0].sum()
        sy[position] = pairs_array[:, 1].sum()
        sxx[position] = (pairs_array[:, 0] ** 2).sum()
        sxy[position] = (pairs_array[:, 0] * pairs_array[:, 1]).sum()

    def slope(sel: np.ndarray) -> float:
        n = counts[sel].sum()
        if n <= 1:
            return float("nan")
        x_sum, y_sum = sx[sel].sum(), sy[sel].sum()
        xx_sum, xy_sum = sxx[sel].sum(), sxy[sel].sum()
        var_x = xx_sum - x_sum * x_sum / n
        if var_x <= 0:
            return float("nan")
        return float((xy_sum - x_sum * y_sum / n) / var_x)

    all_indices = np.arange(n_games)
    estimate = slope(all_indices)
    boot_indices = rng.integers(0, n_games, size=(BOOT_RESAMPLES, n_games))
    boot_n = counts[boot_indices].sum(axis=1)
    boot_sx = sx[boot_indices].sum(axis=1)
    boot_sy = sy[boot_indices].sum(axis=1)
    boot_sxx = sxx[boot_indices].sum(axis=1)
    boot_sxy = sxy[boot_indices].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        boot_var_x = boot_sxx - boot_sx * boot_sx / boot_n
        boot_slopes = np.where(
            boot_var_x > 0,
            (boot_sxy - boot_sx * boot_sy / boot_n) / np.where(boot_var_x > 0, boot_var_x, 1.0),
            np.nan,
        )
    valid_slopes = boot_slopes[~np.isnan(boot_slopes)]
    ci_lo, ci_hi = np.percentile(valid_slopes, [2.5, 97.5])
    mde = Z_975 * float(np.std(valid_slopes, ddof=1))
    n_plays = int(counts.sum())
    return estimate, float(ci_lo), float(ci_hi), mde, n_plays


def _mean_ci(
    values_by_game: dict[tuple[str, int], list[float]], rng: np.random.Generator
) -> tuple[float, float, float, int]:
    """Cluster-bootstrap CI for a per-play mean aggregated by game."""
    games = list(values_by_game)
    counts = np.array([len(values_by_game[game]) for game in games], dtype=float)
    sums = np.array([sum(values_by_game[game]) for game in games], dtype=float)
    estimate = float(sums.sum() / counts.sum())
    boot_indices = rng.integers(0, len(games), size=(BOOT_RESAMPLES, len(games)))
    boot_counts = counts[boot_indices].sum(axis=1)
    boot_sums = sums[boot_indices].sum(axis=1)
    means = boot_sums / boot_counts
    ci_lo, ci_hi = np.percentile(means, [2.5, 97.5])
    return estimate, float(ci_lo), float(ci_hi), int(counts.sum())


def _verdict_of_slope(ci_lo: float, ci_hi: float, estimate: float) -> str:
    if ci_lo > 0:
        return "slope_positive_sig"
    if ci_hi < 0:
        return "slope_negative_sig"
    return "ci_includes_zero"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instances", required=True)
    parser.add_argument("--calibration", required=True)
    parser.add_argument("--plays", required=True, help="回填后的 card_plays.csv")
    parser.add_argument("--out-quality", required=True)
    parser.add_argument("--out-results", required=True)
    args = parser.parse_args()

    anomalies: dict[str, int] = defaultdict(int)
    rng = np.random.default_rng(RNG_SEED)

    w = _load_w_greedy(Path(args.calibration), anomalies)
    quality_rows = _build_quality_rows(Path(args.instances), w, anomalies)

    quality_path = Path(args.out_quality)
    quality_path.parent.mkdir(parents=True, exist_ok=True)
    with quality_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(QUALITY_COLUMNS))
        writer.writeheader()
        writer.writerows(quality_rows)

    results: list[dict[str, object]] = []
    slopes: dict[tuple[str, str], tuple[float, float, float]] = {}

    # ---- 检验 1：Q → 结局，三人群 × 两口径 ----
    for population in POPULATIONS:
        pop_rows = [row for row in quality_rows if row["population"] == population]
        for outcome_key, outcome_name in (("points", "points"), ("net_worth_final", "net_worth")):
            estimate, ci_lo, ci_hi, mde, n_games = _demeaned_slope_test(pop_rows, outcome_key, rng)
            slopes[(population, outcome_name)] = (estimate, ci_lo, ci_hi)
            results.append(
                {
                    "test": f"test1_slope:{population}",
                    "outcome": outcome_name,
                    "estimate": estimate,
                    "ci_lo": ci_lo,
                    "ci_hi": ci_hi,
                    "n_units": n_games,
                    "min_detectable_effect": mde,
                    "verdict": _verdict_of_slope(ci_lo, ci_hi, estimate),
                }
            )

    # ---- 检验 1：斜率差（独立 bootstrap 分布相减）----
    rng_diff = np.random.default_rng(RNG_SEED + 1)
    boot_slopes_cache: dict[tuple[str, str], np.ndarray] = {}
    for population in POPULATIONS:
        pop_rows = [row for row in quality_rows if row["population"] == population]
        by_game: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
        for row in pop_rows:
            by_game[(row["experiment"], int(row["game_index"]))].append(
                (float(row["q_total"]), float(row["points"]))
            )
        sxy = np.zeros(len(by_game))
        sxx = np.zeros(len(by_game))
        for position, pairs in enumerate(by_game.values()):
            pairs_array = np.asarray(pairs)
            q_centered = pairs_array[:, 0] - pairs_array[:, 0].mean()
            y_centered = pairs_array[:, 1] - pairs_array[:, 1].mean()
            sxy[position] = float(q_centered @ y_centered)
            sxx[position] = float(q_centered @ q_centered)
        indices = rng_diff.integers(0, len(by_game), size=(BOOT_RESAMPLES, len(by_game)))
        boot_sxx = sxx[indices].sum(axis=1)
        valid = boot_sxx > 0
        boot_slopes_cache[(population, "points")] = (sxy[indices].sum(axis=1))[valid] / boot_sxx[
            valid
        ]

    for floor in ("greedy", "sane"):
        diff = boot_slopes_cache[("llm", "points")] - boot_slopes_cache[(floor, "points")]
        ci_lo, ci_hi = np.percentile(diff, [2.5, 97.5])
        estimate = slopes[("llm", "points")][0] - slopes[(floor, "points")][0]
        results.append(
            {
                "test": f"test1_slope_diff:llm-{floor}",
                "outcome": "points",
                "estimate": estimate,
                "ci_lo": float(ci_lo),
                "ci_hi": float(ci_hi),
                "n_units": "",
                "min_detectable_effect": Z_975 * float(np.std(diff, ddof=1)),
                "verdict": "diff_sig" if (ci_lo > 0 or ci_hi < 0) else "diff_not_sig",
            }
        )

    # ---- 检验 1 总结判定（points 口径，§5.2 冻结逻辑）----
    floor_signal = any(slopes[(floor, "points")][1] > 0 for floor in ("greedy", "sane"))
    llm_zero = slopes[("llm", "points")][1] <= 0 <= slopes[("llm", "points")][2]
    diff_sig = any(
        row["verdict"] == "diff_sig"
        for row in results
        if str(row["test"]).startswith("test1_slope_diff")
    )
    all_zero = all(
        slopes[(population, "points")][1] <= 0 <= slopes[(population, "points")][2]
        for population in POPULATIONS
    )
    if floor_signal and llm_zero and diff_sig:
        test1_verdict = "decisions_override_luck"
    elif all_zero:
        test1_verdict = "all_slopes_zero_report_mde"
    elif not diff_sig:
        test1_verdict = "llm_same_as_floor_no_extra_absorption"
    else:
        test1_verdict = "inconclusive"
    results.append(
        {
            "test": "test1_verdict",
            "outcome": "points",
            "estimate": "",
            "ci_lo": "",
            "ci_hi": "",
            "n_units": "",
            "min_detectable_effect": "",
            "verdict": test1_verdict,
        }
    )

    # ---- 检验 2：executed ~ max 兑现率 ----
    play_rows: list[dict[str, str]] = []
    with Path(args.plays).open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["deck"] == "chance" and row["card_id"] != "chance-steal":
                play_rows.append(row)

    capture: dict[str, tuple[float, float, float]] = {}
    capture_boot: dict[str, np.ndarray] = {}
    for population in POPULATIONS:
        pop_plays = [row for row in play_rows if POPULATION_OF[row["experiment"]] == population]
        estimate, ci_lo, ci_hi, mde, n_plays = _capture_rate_test(pop_plays, rng)
        capture[population] = (estimate, ci_lo, ci_hi)
        results.append(
            {
                "test": f"test2_capture_rate:{population}",
                "outcome": "executed_dv",
                "estimate": estimate,
                "ci_lo": ci_lo,
                "ci_hi": ci_hi,
                "n_units": n_plays,
                "min_detectable_effect": mde,
                "verdict": _verdict_of_slope(ci_lo, ci_hi, estimate),
            }
        )

    # 斜率差（检验 2）：用各自 bootstrap 分布相减
    rng_diff2 = np.random.default_rng(RNG_SEED + 2)
    for population in POPULATIONS:
        pop_plays = [row for row in play_rows if POPULATION_OF[row["experiment"]] == population]
        by_game: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
        for row in pop_plays:
            by_game[(row["experiment"], int(row["game_index"]))].append(
                (float(row["max_target_dv"]), float(row["executed_dv"]))
            )
        games = list(by_game)
        counts = np.zeros(len(games))
        sx = np.zeros(len(games))
        sy = np.zeros(len(games))
        sxx = np.zeros(len(games))
        sxy = np.zeros(len(games))
        for position, game in enumerate(games):
            pairs_array = np.asarray(by_game[game])
            counts[position] = len(pairs_array)
            sx[position] = pairs_array[:, 0].sum()
            sy[position] = pairs_array[:, 1].sum()
            sxx[position] = (pairs_array[:, 0] ** 2).sum()
            sxy[position] = (pairs_array[:, 0] * pairs_array[:, 1]).sum()
        indices = rng_diff2.integers(0, len(games), size=(BOOT_RESAMPLES, len(games)))
        boot_n = counts[indices].sum(axis=1)
        boot_sx = sx[indices].sum(axis=1)
        boot_sy = sy[indices].sum(axis=1)
        boot_sxx = sxx[indices].sum(axis=1)
        boot_sxy = sxy[indices].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            boot_var_x = boot_sxx - boot_sx * boot_sx / boot_n
            boot = np.where(
                boot_var_x > 0,
                (boot_sxy - boot_sx * boot_sy / boot_n) / np.where(boot_var_x > 0, boot_var_x, 1.0),
                np.nan,
            )
        capture_boot[population] = boot[~np.isnan(boot)]

    diff = capture_boot["llm"] - capture_boot["greedy"]
    ci_lo, ci_hi = np.percentile(diff, [2.5, 97.5])
    primary_pass = ci_lo > 0
    results.append(
        {
            "test": "test2_capture_diff:llm-greedy",
            "outcome": "executed_dv",
            "estimate": capture["llm"][0] - capture["greedy"][0],
            "ci_lo": float(ci_lo),
            "ci_hi": float(ci_hi),
            "n_units": "",
            "min_detectable_effect": Z_975 * float(np.std(diff, ddof=1)),
            "verdict": "llm_captures_more" if primary_pass else "diff_ci_includes_zero",
        }
    )

    # 伴随数：max 上四分位子样本的 executed 均值
    for population in POPULATIONS:
        pop_plays = [row for row in play_rows if POPULATION_OF[row["experiment"]] == population]
        if not pop_plays:
            continue
        maxes = np.array([float(row["max_target_dv"]) for row in pop_plays])
        q3 = float(np.percentile(maxes, 75))
        by_game_top: dict[tuple[str, int], list[float]] = defaultdict(list)
        for row in pop_plays:
            if float(row["max_target_dv"]) >= q3:
                by_game_top[(row["experiment"], int(row["game_index"]))].append(
                    float(row["executed_dv"])
                )
        estimate, ci_lo, ci_hi, n_plays = _mean_ci(by_game_top, rng)
        results.append(
            {
                "test": f"test2_top_quartile_executed:{population}",
                "outcome": "executed_dv",
                "estimate": estimate,
                "ci_lo": ci_lo,
                "ci_hi": ci_hi,
                "n_units": n_plays,
                "min_detectable_effect": "",
                "verdict": "companion_stat",
            }
        )

    sane_estimate = capture.get("sane", (float("nan"),) * 3)[0]
    test2_verdict = "llm_captures_more_than_greedy" if primary_pass else "primary_not_passed"
    results.append(
        {
            "test": "test2_verdict",
            "outcome": "executed_dv",
            "estimate": "",
            "ci_lo": "",
            "ci_hi": "",
            "n_units": "",
            "min_detectable_effect": "",
            "verdict": f"{test2_verdict};sane_lower_bound_b={sane_estimate:.4f}",
        }
    )

    results_path = Path(args.out_results)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(RESULT_COLUMNS))
        writer.writeheader()
        writer.writerows(results)

    print(f"quality rows: {len(quality_rows)} -> {quality_path}", file=sys.stderr)
    print(f"result rows: {len(results)} -> {results_path}", file=sys.stderr)
    print(f"test1 verdict: {test1_verdict}", file=sys.stderr)
    print(f"test2 verdict: {test2_verdict}", file=sys.stderr)
    print(f"ANOMALIES: {dict(anomalies) if anomalies else 'none'}", file=sys.stderr)


if __name__ == "__main__":
    main()
