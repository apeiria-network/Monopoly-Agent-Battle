"""Section 6.9 job 4 -- score the 108 LLM games against the floor.

WHAT THIS COMPUTES
------------------
Reads the per-decision delta-V tables of the four LLM experiments and does
three things:

1. TIERS. Every valid decision is labelled with the FLOOR quantile thresholds
   from policy.py (section 6.3: per decision kind, regret <= P50 is
   "reasonable", P50-P90 "intermediate", > P90 "suboptimal"). Aggregation
   follows section 6.3: per-game mean first, then the plain mean across games
   (macro); weighted mean, game-cluster bootstrap CI and the suboptimal share
   are reported alongside.

2. FLOOR DEVIATIONS (section 6.6 item 2), PER PLAYER TYPE. An experiment can
   mix architectures (courts vs llm_baseline, ming vs FE), so each
   controller_type's macro mean delta-V and macro mean regret are expressed
   separately as deviations from the two evenly matched floors (sane_random,
   greedy_script). The deviation CI combines the experiment's game-cluster
   bootstrap with the floor's own standard error in quadrature. Regret is the
   primary currency (it nets out opportunity); delta-V is reported alongside.
   These rows go to validation_results.csv keyed analysis=地板偏离.

3. IN-GAME CONTRAST (court-vs-baseline, fe-vs-baseline only). In those games
   an LLM-baseline seat sits at the same table, so per game the architecture
   seats' mean regret is compared with the baseline seats' mean regret
   (paired by game, cluster bootstrap). This is the cleanest quality contrast
   available: same dice, same deck, same opponents. Reported for delta-V too,
   keyed analysis=局内对照.

INTERPRETATION (read before quoting any number)
-----------------------------------------------
* The tiers are FLOOR quantiles: "suboptimal" means "worse than the top
  decile of scripted-floor play", an operational label, not a mistake.
* Section 6.6 test 3 (ranking.py) is the gate: until it passes, regret and
  its tiers may only be reported as "deviation from the public heuristic V",
  never as decision correctness.
* The floors come from SCRIPTED evenly matched games; LLM experiments are
  LLM-vs-LLM ecologies. A floor deviation spans both decision quality and
  opponent context -- quote it as "deviation from the scripted floor", not
  "lead over the actual opponents". The in-game contrast carries no such
  caveat.
* Decisions flagged executed_pruned=1 are clamped to regret 0 by owner
  decision (section 6.3); they are counted separately, not as clean
  "reasonable" choices.
* Cross-experiment comparison of mean regret is a description, not a ranking
  of architectures: each architecture faces the states its own play produced.

HOW TO READ THE OUTPUT
----------------------
stat/judge/scores.csv: one row per decision (run, game_id, decision_index,
    seat, kind, complete_rounds, candidate_count, delta_v_executed,
    delta_v_best, regret, tier, flag).
stat/judge/scores_summary.csv: one row per experiment (run, games, decisions,
    mean_regret_macro, mean_regret_weighted, ci_low, ci_high, share_high).
stat/judge/validation_results.csv: appended rows keyed 地板偏离 / 局内对照.

Prerequisites: evaluate.py scoring passes for the four LLM experiments, and
policy.py floors + thresholds in validation_results.csv. This script never
touches the engine.

Usage from the repository root:
    .venv/Scripts/python.exe stat/judge/score.py [experiment ...]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate as evaluate_module

ROOT = Path(__file__).resolve().parent.parent.parent
RUNS = ROOT / "runs"
DATA_DIR = Path(__file__).resolve().parent / "data"
OUTPUT_DIR = Path(__file__).resolve().parent

LLM_EXPERIMENTS = ("4-courts-battle", "court-vs-baseline", "fe-vs-baseline", "court-fe-battle")
BOOT_RESAMPLES = 10_000
RNG_SEED = 20260924

KIND_NAMES = {
    0: "asset_management",
    1: "payment_resolution",
    2: "jail",
    3: "forced_discard",
    4: "theft_card_selection",
}
ZERO_DELTA_KINDS = {3, 4}  # V has no hand term: these deltas are identically 0


def _load_thresholds() -> dict[str, tuple[float, float]]:
    path = OUTPUT_DIR / "validation_results.csv"
    if not path.exists():
        raise SystemExit("missing validation_results.csv -- run policy.py first")
    thresholds: dict[str, dict[str, float]] = {}
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["analysis"] != "分档阈值":
                continue
            thresholds.setdefault(row["arm_or_scope"], {})[row["checkpoint"]] = float(
                row["estimate"]
            )
    out: dict[str, tuple[float, float]] = {}
    for kind, values in thresholds.items():
        if "P50" in values and "P90" in values:
            out[kind] = (values["P50"], values["P90"])
    if not out:
        raise SystemExit("no 分档阈值 rows in validation_results.csv -- run policy.py first")
    return out


def _load_floors() -> dict[str, dict[str, tuple[float, float]]]:
    """Parse 地板基准 rows: {policy: {metric: (mean, se)}} from validation_results."""
    path = OUTPUT_DIR / "validation_results.csv"
    floors: dict[str, dict[str, list[float]]] = {}
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["analysis"] != "地板基准" or "mean_" not in row["arm_or_scope"]:
                continue
            policy, metric = row["arm_or_scope"].rsplit(" mean_", 1)
            mean = float(row["estimate"])
            se = (float(row["ci_high"]) - float(row["ci_low"])) / (2 * 1.96)
            floors.setdefault(policy, {})[metric] = [mean, se]
    out: dict[str, dict[str, tuple[float, float]]] = {}
    for policy, metrics in floors.items():
        out[policy] = {m: (v[0], v[1]) for m, v in metrics.items()}
    if not out:
        raise SystemExit("no 地板基准 rows in validation_results.csv -- run policy.py first")
    return out


def _tier(regret: float, kind: int, thresholds: dict[str, tuple[float, float]]) -> str:
    name = KIND_NAMES.get(kind)
    if name is None or name not in thresholds:
        return "unclassified"
    p50, p90 = thresholds[name]
    if regret <= p50:
        return "reasonable"
    if regret <= p90:
        return "intermediate"
    return "suboptimal"


def _controller_types(experiment: str, game_names: list[str]) -> dict[tuple[str, int], str]:
    mapping: dict[tuple[str, int], str] = {}
    for name in game_names:
        config = json.loads((RUNS / experiment / name / "config.json").read_text(encoding="utf-8"))
        for player in config["config"]["players"]:
            mapping[(name, int(player["seat"]))] = str(player["controller_type"])
    return mapping


def _boot_mean_ci(values: np.ndarray, rng: np.random.Generator) -> tuple[float, float, float]:
    point = float(values.mean())
    n = len(values)
    boot = [float(values[rng.integers(0, n, size=n)].mean()) for _ in range(BOOT_RESAMPLES)]
    return point, float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))


def score_experiment(
    experiment: str,
    thresholds: dict[str, tuple[float, float]],
    floors: dict[str, dict[str, tuple[float, float]]],
    rng: np.random.Generator,
) -> tuple[list[tuple], tuple, list[tuple], list[tuple]]:
    """Score one experiment.

    Returns (decision rows, summary row, floor-deviation rows, contrast rows).
    """
    path = DATA_DIR / f"decisions_{experiment}.npz"
    if not path.exists():
        raise SystemExit(f"missing {path} -- run evaluate.py for {experiment} first")
    with np.load(path, allow_pickle=False) as archive:
        table = archive["table"]
        columns = {str(name): index for index, name in enumerate(archive["columns"])}

    index_to_name = {
        index: d.name for index, d in enumerate(evaluate_module.experiment_directories(experiment))
    }
    types = _controller_types(experiment, list(index_to_name.values()))

    def get(name: str) -> np.ndarray:
        return table[:, columns[name]]

    game_index = get("game_index").astype(int)
    seat = get("seat").astype(int)
    kind = get("kind_code").astype(int)
    candidates = get("candidate_count")
    regret = get("regret")
    delta_v = get("delta_v_executed")
    executed_pruned = get("executed_pruned").astype(int)
    pruned_all = get("pruned_all").astype(int)

    rows: list[tuple] = []
    valid_mask = candidates >= 2
    per_game_regret: dict[int, list[float]] = {}
    per_game_seat: dict[int, dict[int, list[tuple[float, float]]]] = {}
    high_counts = 0
    valid_count = 0
    for i in range(table.shape[0]):
        if not valid_mask[i]:
            continue
        valid_count += 1
        tier = _tier(float(regret[i]), int(kind[i]), thresholds)
        flags = []
        if executed_pruned[i]:
            flags.append("executed_pruned")
        if pruned_all[i]:
            flags.append("pruned_all")
        if int(kind[i]) in ZERO_DELTA_KINDS:
            flags.append("zero_delta")
        if tier == "suboptimal":
            high_counts += 1
        g = int(game_index[i])
        s = int(seat[i])
        per_game_regret.setdefault(g, []).append(float(regret[i]))
        per_game_seat.setdefault(g, {}).setdefault(s, []).append(
            (float(delta_v[i]), float(regret[i]))
        )
        rows.append(
            (
                experiment,
                index_to_name.get(g, g),
                int(get("decision_index")[i]),
                s,
                KIND_NAMES.get(int(kind[i]), kind[i]),
                int(get("complete_rounds")[i]),
                int(candidates[i]),
                round(float(delta_v[i]), 4),
                round(float(get("delta_v_best")[i]), 4),
                round(float(regret[i]), 4),
                tier,
                ";".join(flags),
            )
        )

    game_means = np.array([float(np.mean(v)) for v in per_game_regret.values()])
    macro = float(game_means.mean()) if len(game_means) else float("nan")
    weighted = float(
        np.average(
            [float(np.mean(v)) for v in per_game_regret.values()],
            weights=[len(v) for v in per_game_regret.values()],
        )
    )
    n = len(game_means)
    boot = [float(game_means[rng.integers(0, n, size=n)].mean()) for _ in range(BOOT_RESAMPLES)]
    ci_low, ci_high = float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
    share_high = high_counts / valid_count if valid_count else float("nan")
    summary = (
        experiment,
        len(per_game_regret),
        valid_count,
        round(macro, 4),
        round(weighted, 4),
        round(ci_low, 4),
        round(ci_high, 4),
        round(share_high, 4),
    )

    # Floor deviations, per (experiment, controller_type): an experiment can
    # mix architectures, so deviations are computed per player type -- pooling
    # all seats would let the numerically dominant controller (e.g. three
    # llm_baseline seats) mask the architecture's own level.
    deviation_rows: list[tuple] = []
    per_controller_games: dict[str, dict[int, list[tuple[float, float]]]] = {}
    for g, seats in per_game_seat.items():
        name = index_to_name.get(g)
        if name is None:
            continue
        for s, values in seats.items():
            controller = types.get((name, s), "")
            if not controller:
                continue
            seat_mean = (
                float(np.mean([v[0] for v in values])),
                float(np.mean([v[1] for v in values])),
            )
            per_controller_games.setdefault(controller, {}).setdefault(g, []).append(seat_mean)
    metric_index = {"dv": 0, "regret": 1}
    for controller in sorted(per_controller_games):
        games_map = per_controller_games[controller]
        for metric, index in metric_index.items():
            means = np.array(
                [
                    float(np.mean([v[index] for v in seat_means]))
                    for seat_means in games_map.values()
                ]
            )
            if len(means) == 0:
                continue
            point, low, high = _boot_mean_ci(means, rng)
            se_exp = (high - low) / (2 * 1.96)
            for policy in sorted(floors):
                if metric not in floors[policy]:
                    continue
                floor_mean, floor_se = floors[policy][metric]
                se_total = float(np.sqrt(se_exp**2 + floor_se**2))
                dev = point - floor_mean
                deviation_rows.append(
                    (
                        "地板偏离",
                        f"{experiment} {controller} vs {policy} mean_{metric}",
                        "",
                        round(dev, 4),
                        round(dev - 1.96 * se_total, 4),
                        round(dev + 1.96 * se_total, 4),
                        len(means),
                    )
                )

    # In-game contrast: architecture seats vs llm_baseline seats, paired by game.
    contrast_rows: list[tuple] = []
    paired_dv: list[float] = []
    paired_rg: list[float] = []
    for g, seats in sorted(per_game_seat.items()):
        name = index_to_name.get(g)
        if name is None:
            continue
        arch: list[tuple[float, float]] = []
        base: list[tuple[float, float]] = []
        for s, values in seats.items():
            controller = types.get((name, s), "")
            seat_mean = (
                float(np.mean([v[0] for v in values])),
                float(np.mean([v[1] for v in values])),
            )
            if controller == "llm_baseline":
                base.append(seat_mean)
            elif controller:
                arch.append(seat_mean)
        if arch and base:
            paired_dv.append(float(np.mean([v[0] for v in arch]) - np.mean([v[0] for v in base])))
            paired_rg.append(float(np.mean([v[1] for v in arch]) - np.mean([v[1] for v in base])))
    if paired_dv:
        for label, values in (("dv", paired_dv), ("regret", paired_rg)):
            arr = np.array(values)
            point, low, high = _boot_mean_ci(arr, rng)
            contrast_rows.append(
                (
                    "局内对照",
                    f"{experiment} arch-minus-baseline {label}",
                    "",
                    round(point, 4),
                    round(low, 4),
                    round(high, 4),
                    len(values),
                )
            )

    return rows, summary, deviation_rows, contrast_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiments", nargs="*", default=list(LLM_EXPERIMENTS))
    args = parser.parse_args()

    thresholds = _load_thresholds()
    floors = _load_floors()
    rng = np.random.default_rng(RNG_SEED)

    all_rows: list[tuple] = []
    summaries: list[tuple] = []
    validation_rows: list[tuple] = []
    for experiment in args.experiments:
        print(f"=== {experiment} ===", flush=True)
        rows, summary, deviations, contrasts = score_experiment(experiment, thresholds, floors, rng)
        all_rows.extend(rows)
        summaries.append(summary)
        validation_rows.extend(deviations)
        validation_rows.extend(contrasts)
        print(
            f"  {summary[1]} games, {summary[2]} valid decisions, "
            f"macro regret {summary[3]}, share_high {summary[7]}"
        )
        for row in deviations:
            print(
                f"  floor deviation: {row[1]} = {row[3]:+.2f} CI95 [{row[4]:+.2f}, {row[5]:+.2f}]"
            )
        for row in contrasts:
            print(
                f"  in-game contrast: {row[1]} = {row[3]:+.2f} CI95 [{row[4]:+.2f}, {row[5]:+.2f}]"
            )

    with (OUTPUT_DIR / "scores.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "run",
                "game_id",
                "decision_index",
                "seat",
                "kind",
                "complete_rounds",
                "candidate_count",
                "delta_v_executed",
                "delta_v_best",
                "regret",
                "tier",
                "flag",
            ]
        )
        writer.writerows(all_rows)
    with (OUTPUT_DIR / "scores_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "run",
                "games",
                "decisions",
                "mean_regret_macro",
                "mean_regret_weighted",
                "ci_low",
                "ci_high",
                "share_high",
            ]
        )
        writer.writerows(summaries)

    validation_path = OUTPUT_DIR / "validation_results.csv"
    write_header = not validation_path.exists()
    with validation_path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        if write_header:
            writer.writerow(
                ["analysis", "arm_or_scope", "checkpoint", "estimate", "ci_low", "ci_high", "n"]
            )
        writer.writerows(validation_rows)
    print(f"\nwrote {OUTPUT_DIR / 'scores.csv'} and {OUTPUT_DIR / 'scores_summary.csv'}")
    print(f"appended {len(validation_rows)} rows to validation_results.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
