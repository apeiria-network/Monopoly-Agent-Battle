"""Section 6.6 item 2 -- floor reference levels -- plus regret tier thresholds.

WHAT THIS COMPUTES
------------------
The floor is measured on EVENLY MATCHED games only (section 6.6):

    sample = sane_random games 0-99 + greedy_script games 0-99

1. FLOOR LEVELS. Per (game, seat) mean executed delta-V and mean regret over
   valid decisions (candidate_count >= 2), then per policy: the mean and
   variance across seats, with a game-cluster bootstrap CI (10,000). These are
   ABSOLUTE reference levels: how much value a scripted policy extracts per
   decision, and how far from the oracle it sits, when all four opponents play
   the same policy. LLM-experiment players are later reported as deviations
   from these levels (score.py). Regret is the primary currency for
   cross-opponent comparison because it nets out opportunity (regret is
   identically 0 when nothing can be done); delta-V is reported alongside.

   The gap between the two floors (greedy minus sane) is reported
   descriptively: it must merely sit in the known direction (experiment 11:
   greedy beats sane, +0.19 pts/game). No preregistered verdict is attached --
   this item is a measurement, not a discrimination test. The 2v2 games are
   game-level leverage evidence and play no role here.

2. REGRET TIER THRESHOLDS (section 6.3). Per decision kind, the P50 and P90
   quantiles of regret over the sample's valid decisions. These are the cut
   points the LLM scoring pass uses to label decisions reasonable /
   intermediate / suboptimal. They are FLOOR quantiles: they say what is
   typical for scripted play, not what is correct.

HOW TO READ THE OUTPUT
----------------------
Console prints the two floors (mean, sd, CI95) for delta-V and regret, the
descriptive floor gap, then the per-kind thresholds.
stat/judge/policy.csv: one row per (game, seat) -- the analysis input.
stat/judge/validation_results.csv: floors keyed analysis=地板基准, thresholds
    keyed analysis=分档阈值.

Prerequisite: the C-block scoring pass must have produced
    decisions_sane_random__policy100.npz
    decisions_greedy_script__policy100.npz
via evaluate.py --limit 100 --tag policy100. This script never touches the
engine.

Usage from the repository root:
    .venv/Scripts/python.exe stat/judge/policy.py
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

BOOT_RESAMPLES = 10_000
RNG_SEED = 20260924

SAMPLES = (
    ("sane_random", "policy100"),
    ("greedy_script", "policy100"),
)

KIND_NAMES = {
    0: "asset_management",
    1: "payment_resolution",
    2: "jail",
    3: "forced_discard",
    4: "theft_card_selection",
}


def _load_tagged(experiment: str, tag: str) -> tuple[np.ndarray, dict[str, int]]:
    path = DATA_DIR / f"decisions_{experiment}__{tag}.npz"
    if not path.exists():
        raise SystemExit(
            f"missing {path} -- run: .venv/Scripts/python.exe stat/judge/evaluate.py "
            f"{experiment} --limit 100 --tag {tag}"
        )
    with np.load(path, allow_pickle=False) as archive:
        table = archive["table"]
        columns = {str(name): index for index, name in enumerate(archive["columns"])}
    return table, columns


def _controller_types(experiment: str, game_names: list[str]) -> dict[tuple[str, int], str]:
    """Map (game_name, seat) -> controller_type from each game's config.json."""
    mapping: dict[tuple[str, int], str] = {}
    for name in game_names:
        config = json.loads((RUNS / experiment / name / "config.json").read_text(encoding="utf-8"))
        for player in config["config"]["players"]:
            mapping[(name, int(player["seat"]))] = str(player["controller_type"])
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    seat_rows: list[tuple[str, str, float, float, float, float, int]] = []
    regret_by_kind: dict[int, list[float]] = {}
    per_policy_games: dict[str, dict[str, dict[int, tuple[float, float]]]] = {}
    for experiment, tag in SAMPLES:
        table, columns = _load_tagged(experiment, tag)
        # game_index -> name comes from the filesystem listing (the same
        # ordering evaluate.py scored against), NOT the npz games array:
        # a game skipped mid-run would shift that array's positions.
        index_to_name = {
            index: d.name
            for index, d in enumerate(evaluate_module.experiment_directories(experiment))
        }
        types = _controller_types(experiment, list(index_to_name.values()))
        game_index = table[:, columns["game_index"]].astype(int)
        seat = table[:, columns["seat"]].astype(int)
        kind = table[:, columns["kind_code"]].astype(int)
        candidates = table[:, columns["candidate_count"]]
        executed = table[:, columns["delta_v_executed"]]
        regret = table[:, columns["regret"]]

        for row_kind, row_regret in zip(kind, regret, strict=True):
            regret_by_kind.setdefault(int(row_kind), []).append(float(row_regret))

        per_seat: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for g, s, c, dv, r in zip(game_index, seat, candidates, executed, regret, strict=True):
            if c < 2:
                continue
            per_seat.setdefault((int(g), int(s)), []).append((float(dv), float(r)))
        for (g, s), values in sorted(per_seat.items()):
            name = index_to_name.get(g)
            if name is None:
                continue
            controller = types.get((name, s), "unknown")
            mean_dv = float(np.mean([v[0] for v in values]))
            mean_regret = float(np.mean([v[1] for v in values]))
            seat_rows.append(
                (experiment, controller, float(s), mean_dv, mean_regret, float(len(values)), g)
            )
            per_policy_games.setdefault(controller, {}).setdefault(name, {})[s] = (
                mean_dv,
                mean_regret,
            )

    print(f"{len(seat_rows)} (game, seat) rows from {len({(r[0], r[6]) for r in seat_rows})} games")

    rng = np.random.default_rng(RNG_SEED)
    print("\n=== floor reference levels (evenly matched games) ===")
    floors: dict[str, dict[str, tuple[float, float, float, float]]] = {}
    for controller in sorted(per_policy_games):
        # Cluster unit = game: each game contributes its 4 seat means.
        game_units = [list(seats.values()) for seats in per_policy_games[controller].values()]

        def pool(metric_index: int, sample: list[int], units: list = game_units) -> float:
            values = [units[g][s][metric_index] for g in sample for s in range(len(units[g]))]
            return float(np.mean(values))

        dv_seats = np.array([v[0] for unit in game_units for v in unit])
        rg_seats = np.array([v[1] for unit in game_units for v in unit])
        n_games = len(game_units)
        dv_boot = [
            pool(0, list(rng.integers(0, n_games, size=n_games))) for _ in range(BOOT_RESAMPLES)
        ]
        rg_boot = [
            pool(1, list(rng.integers(0, n_games, size=n_games))) for _ in range(BOOT_RESAMPLES)
        ]
        floors[controller] = {
            "dv": (
                float(dv_seats.mean()),
                float(dv_seats.std(ddof=1)),
                float(np.percentile(dv_boot, 2.5)),
                float(np.percentile(dv_boot, 97.5)),
            ),
            "regret": (
                float(rg_seats.mean()),
                float(rg_seats.std(ddof=1)),
                float(np.percentile(rg_boot, 2.5)),
                float(np.percentile(rg_boot, 97.5)),
            ),
        }
        print(
            f"  {controller:14s}: mean_dV={dv_seats.mean():8.2f} "
            f"(sd {dv_seats.std(ddof=1):6.2f}, "
            f"CI95 [{np.percentile(dv_boot, 2.5):7.2f}, {np.percentile(dv_boot, 97.5):7.2f}])   "
            f"mean_regret={rg_seats.mean():7.2f} (sd {rg_seats.std(ddof=1):6.2f}, "
            f"CI95 [{np.percentile(rg_boot, 2.5):6.2f}, {np.percentile(rg_boot, 97.5):6.2f}])"
        )

    if "greedy_script" in floors and "sane_random" in floors:
        gap_dv = floors["greedy_script"]["dv"][0] - floors["sane_random"]["dv"][0]
        gap_rg = floors["greedy_script"]["regret"][0] - floors["sane_random"]["regret"][0]
        print(
            f"\nfloor gap (greedy - sane): dV {gap_dv:+.2f}, regret {gap_rg:+.2f} "
            f"(descriptive; direction must match experiment 11: greedy stronger)"
        )

    print("\n=== regret tier thresholds (section 6.3, floor quantiles, 200 games) ===")
    thresholds: dict[str, tuple[float, float]] = {}
    for kind_code in sorted(regret_by_kind):
        values = np.array(regret_by_kind[kind_code])
        p50, p90 = float(np.percentile(values, 50)), float(np.percentile(values, 90))
        name = KIND_NAMES.get(kind_code, str(kind_code))
        thresholds[name] = (p50, p90)
        print(f"  {name:20s}: P50={p50:8.2f}  P90={p90:8.2f}  (n={len(values)})")

    with (OUTPUT_DIR / "policy.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "experiment",
                "controller_type",
                "seat",
                "mean_delta_v_executed",
                "mean_regret",
                "n_decisions",
                "game_index",
            ]
        )
        writer.writerows(seat_rows)

    validation_path = OUTPUT_DIR / "validation_results.csv"
    write_header = not validation_path.exists()
    with validation_path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        if write_header:
            writer.writerow(
                ["analysis", "arm_or_scope", "checkpoint", "estimate", "ci_low", "ci_high", "n"]
            )
        for controller in sorted(floors):
            n_seats = sum(len(seats) for seats in per_policy_games[controller].values())
            for metric in ("dv", "regret"):
                mean, sd, low, high = floors[controller][metric]
                writer.writerow(
                    ["地板基准", f"{controller} mean_{metric}", "", mean, low, high, n_seats]
                )
                writer.writerow(["地板基准", f"{controller} sd_{metric}", "", sd, "", "", n_seats])
        for kind_code in sorted(regret_by_kind):
            name = KIND_NAMES.get(kind_code, str(kind_code))
            p50, p90 = thresholds[name]
            writer.writerow(["分档阈值", name, "P50", p50, "", "", len(regret_by_kind[kind_code])])
            writer.writerow(["分档阈值", name, "P90", p90, "", "", len(regret_by_kind[kind_code])])
    print(f"\nwrote {OUTPUT_DIR / 'policy.csv'} and appended validation_results.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
