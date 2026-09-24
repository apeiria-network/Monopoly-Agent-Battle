"""Section 6.9 job 4 -- score the 108 LLM games against the floor thresholds.

WHAT THIS COMPUTES
------------------
Reads the per-decision delta-V tables of the four LLM experiments and labels
every valid decision with a tier, using the FLOOR quantile thresholds produced
by policy.py (section 6.3: per decision kind, regret <= P50 is "reasonable",
P50-P90 is "intermediate", > P90 is "suboptimal"). It then aggregates to one
summary row per experiment.

Aggregation follows section 6.3 exactly:

    macro      per-game mean regret first, then the plain mean across games
               (every game weighs equally regardless of decision count)
    weighted   the plain mean over all decisions (robustness cross-check only)
    CI         game-cluster bootstrap (10,000) of the macro mean
    share_high fraction of valid decisions in the "suboptimal" tier

INTERPRETATION (read before quoting any number)
-----------------------------------------------
* The tiers are FLOOR quantiles: "suboptimal" means "worse than the top
  decile of scripted-floor play", an operational label, not a mistake.
* Section 6.6 test 3 (ranking.py) is the gate: until it passes, regret and
  its tiers may only be reported as "deviation from the public heuristic V",
  never as decision correctness.
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
stat/judge/scores_summary.csv: one row per experiment (run, games,
    decisions, mean_regret_macro, mean_regret_weighted, ci_low, ci_high,
    share_high).

Prerequisites: evaluate.py scoring passes for the four LLM experiments, and
policy.py thresholds in validation_results.csv. This script never touches the
engine.

Usage from the repository root:
    .venv/Scripts/python.exe stat/judge/score.py [experiment ...]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent.parent
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


def score_experiment(
    experiment: str, thresholds: dict[str, tuple[float, float]], rng: np.random.Generator
) -> tuple[list[tuple], tuple]:
    """Score one experiment; return (decision rows, summary row)."""
    path = DATA_DIR / f"decisions_{experiment}.npz"
    if not path.exists():
        raise SystemExit(f"missing {path} -- run evaluate.py for {experiment} first")
    with np.load(path, allow_pickle=False) as archive:
        table = archive["table"]
        columns = {str(name): index for index, name in enumerate(archive["columns"])}

    # game_index -> name comes from the filesystem listing (the same ordering
    # evaluate.py scored against), NOT the shard-ordered npz games array.
    import evaluate as evaluate_module

    index_to_name = {
        index: d.name for index, d in enumerate(evaluate_module.experiment_directories(experiment))
    }

    get = lambda name: table[:, columns[name]]  # noqa: E731
    game_index = get("game_index").astype(int)
    kind = get("kind_code").astype(int)
    candidates = get("candidate_count")
    regret = get("regret")
    executed_pruned = get("executed_pruned").astype(int)
    pruned_all = get("pruned_all").astype(int)

    rows: list[tuple] = []
    valid_mask = candidates >= 2
    per_game_regret: dict[int, list[float]] = {}
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
        per_game_regret.setdefault(int(game_index[i]), []).append(float(regret[i]))
        rows.append(
            (
                experiment,
                index_to_name.get(int(game_index[i]), int(game_index[i])),
                int(get("decision_index")[i]),
                int(get("seat")[i]),
                KIND_NAMES.get(int(kind[i]), kind[i]),
                int(get("complete_rounds")[i]),
                int(candidates[i]),
                round(float(get("delta_v_executed")[i]), 4),
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
    return rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiments", nargs="*", default=list(LLM_EXPERIMENTS))
    args = parser.parse_args()

    thresholds = _load_thresholds()
    rng = np.random.default_rng(RNG_SEED)

    all_rows: list[tuple] = []
    summaries: list[tuple] = []
    for experiment in args.experiments:
        print(f"=== {experiment} ===", flush=True)
        rows, summary = score_experiment(experiment, thresholds, rng)
        all_rows.extend(rows)
        summaries.append(summary)
        print(
            f"  {summary[1]} games, {summary[2]} valid decisions, "
            f"macro regret {summary[3]}, share_high {summary[7]}"
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
    print(f"\nwrote {OUTPUT_DIR / 'scores.csv'} and {OUTPUT_DIR / 'scores_summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
