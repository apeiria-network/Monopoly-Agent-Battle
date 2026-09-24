"""Section 6.6 test 2 -- policy separation -- plus the regret tier thresholds.

WHAT THIS COMPUTES
------------------
Two jobs on the SAME 320-game floor sample (section 6.6):

    sample = sane_random games 0-99 + greedy_script games 0-99
             + greedy_vs_sane_random games 0-119

1. POLICY SEPARATION (test 2). Experiment 11 proved with 960 direct games that
   greedy_script beats sane_random by +0.19 points/game (z = 4.609): the two
   floor policies really differ in strength. If our delta-V metric cannot even
   tell these two apart, it has no hope of resolving the far smaller
   differences between LLM architectures. So: per (game, seat) mean executed
   delta-V, contrast greedy seats against sane seats, game-cluster bootstrap
   CI95. The preregistered expectation is a clearly positive gap (greedy
   higher). Section 6.6 also requires the regret THRESHOLDS below to come from
   this same sample.

   The 2v2 games carry the cleanest evidence because greedy and sane seats
   share the same dice and deck; the single-type experiments are reported as
   an unpaired cross-check.

2. REGRET TIER THRESHOLDS (section 6.3). Per decision kind, the P50 and P90
   quantiles of regret over the sample's valid decisions (candidate_count >=
   2). These are the cut points the LLM scoring pass uses to label decisions
   reasonable / intermediate / suboptimal. They are FLOOR quantiles: they say
   what is typical for scripted play, not what is correct.

HOW TO READ THE OUTPUT
----------------------
Console prints the paired and unpaired contrasts with CI95 and the verdict,
then the per-kind thresholds.
stat/judge/data/policy.csv: one row per (game, seat, kind-group collapsed) --
    the analysis input.
stat/judge/data/validation_results.csv: thresholds keyed analysis=分档阈值,
    contrasts keyed analysis=政策分辨.

Prerequisite: the C-block scoring pass must have produced
    decisions_sane_random__policy100.npz
    decisions_greedy_script__policy100.npz
    decisions_greedy_vs_sane_random__policy120.npz
via evaluate.py --limit/--tag. This script never touches the engine.

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


ROOT = Path(__file__).resolve().parent.parent.parent
RUNS = ROOT / "runs"
DATA_DIR = Path(__file__).resolve().parent / "data"

BOOT_RESAMPLES = 10_000
RNG_SEED = 20260924

SAMPLES = (
    ("sane_random", "policy100"),
    ("greedy_script", "policy100"),
    ("greedy_vs_sane_random", "policy120"),
)

KIND_NAMES = {
    0: "asset_management",
    1: "payment_resolution",
    2: "jail",
    3: "forced_discard",
    4: "theft_card_selection",
}


def _load_tagged(experiment: str, tag: str) -> tuple[np.ndarray, dict[str, int], list[str]]:
    path = DATA_DIR / f"decisions_{experiment}__{tag}.npz"
    if not path.exists():
        raise SystemExit(
            f"missing {path} -- run: .venv/Scripts/python.exe stat/judge/evaluate.py "
            f"{experiment} --limit {tag.replace('policy', '')} --tag {tag}"
        )
    with np.load(path, allow_pickle=False) as archive:
        table = archive["table"]
        columns = {str(name): index for index, name in enumerate(archive["columns"])}
        games = [str(name) for name in archive["games"]]
    return table, columns, games


def _controller_types(experiment: str, game_names: list[str]) -> dict[tuple[str, int], str]:
    """Map (game_name, seat) -> controller_type from each game's config.json."""
    mapping: dict[tuple[str, int], str] = {}
    for name in game_names:
        config = json.loads((RUNS / experiment / name / "config.json").read_text(encoding="utf-8"))
        for player in config["config"]["players"]:
            mapping[(name, int(player["seat"]))] = str(player["controller_type"])
    return mapping


def _cluster_bootstrap_diff(
    pairs: np.ndarray, resamples: int, rng: np.random.Generator
) -> tuple[float, float, float]:
    """CI for the mean of per-game paired differences (greedy minus sane)."""
    diffs = pairs[:, 0] - pairs[:, 1]
    point = float(diffs.mean())
    n = len(diffs)
    boot = [float(diffs[rng.integers(0, n, size=n)].mean()) for _ in range(resamples)]
    return point, float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    seat_rows: list[tuple[str, str, float, float, float, int]] = []
    regret_by_kind: dict[int, list[float]] = {}
    for experiment, tag in SAMPLES:
        table, columns, games = _load_tagged(experiment, tag)
        types = _controller_types(experiment, games)
        game_index = table[:, columns["game_index"]].astype(int)
        seat = table[:, columns["seat"]].astype(int)
        kind = table[:, columns["kind_code"]].astype(int)
        candidates = table[:, columns["candidate_count"]]
        executed = table[:, columns["delta_v_executed"]]
        regret = table[:, columns["regret"]]

        for row_kind, row_regret in zip(kind, regret, strict=True):
            regret_by_kind.setdefault(int(row_kind), []).append(float(row_regret))

        per_seat: dict[tuple[int, int], list[float]] = {}
        for g, s, c, dv in zip(game_index, seat, candidates, executed, strict=True):
            if c < 2:
                continue
            per_seat.setdefault((int(g), int(s)), []).append(float(dv))
        for (g, s), values in sorted(per_seat.items()):
            name = games[g] if g < len(games) else None
            if name is None:
                continue
            controller = types.get((name, s), "unknown")
            seat_rows.append(
                (experiment, controller, float(s), float(np.mean(values)), float(len(values)), g)
            )

    print(f"{len(seat_rows)} (game, seat) rows from {len({(r[0], r[5]) for r in seat_rows})} games")

    rng = np.random.default_rng(RNG_SEED)
    print("\n=== policy separation: greedy minus sane mean executed delta-V ===")

    # Paired contrast inside the 2v2 games (shared dice and deck).
    paired: list[tuple[float, float]] = []
    per_game: dict[int, dict[str, list[float]]] = {}
    for experiment, controller, _seat, mean_dv, _n, g in seat_rows:
        if experiment != "greedy_vs_sane_random":
            continue
        per_game.setdefault(g, {}).setdefault(controller, []).append(mean_dv)
    for controllers in per_game.values():
        greedy = controllers.get("greedy_script")
        sane = controllers.get("sane_random")
        if greedy and sane:
            paired.append((float(np.mean(greedy)), float(np.mean(sane))))
    pairs = np.array(paired)
    point, low, high = _cluster_bootstrap_diff(pairs, BOOT_RESAMPLES, rng)
    print(f"2v2 paired ({len(pairs)} games): {point:+.2f} CI95=[{low:+.2f}, {high:+.2f}]")

    # Unpaired cross-check across the single-type experiments.
    greedy_means = np.array([r[3] for r in seat_rows if r[0] == "greedy_script"])
    sane_means = np.array([r[3] for r in seat_rows if r[0] == "sane_random"])
    unpaired = float(greedy_means.mean() - sane_means.mean())
    boot_u = []
    for _ in range(BOOT_RESAMPLES):
        g_sample = greedy_means[rng.integers(0, len(greedy_means), size=len(greedy_means))]
        s_sample = sane_means[rng.integers(0, len(sane_means), size=len(sane_means))]
        boot_u.append(float(g_sample.mean() - s_sample.mean()))
    low_u, high_u = float(np.percentile(boot_u, 2.5)), float(np.percentile(boot_u, 97.5))
    print(f"unpaired cross-experiment:    {unpaired:+.2f} CI95=[{low_u:+.2f}, {high_u:+.2f}]")

    verdict = "PASS" if (low > 0 and low_u > 0) else "FAIL"
    print(
        "expected direction: positive (experiment 11: greedy beats sane, +0.19 pts/game, z=4.609)"
    )
    print(f"VERDICT (section 6.6 test 2): {verdict}")

    print("\n=== regret tier thresholds (section 6.3, floor quantiles) ===")
    thresholds: dict[str, tuple[float, float]] = {}
    for kind_code in sorted(regret_by_kind):
        values = np.array(regret_by_kind[kind_code])
        p50, p90 = float(np.percentile(values, 50)), float(np.percentile(values, 90))
        name = KIND_NAMES.get(kind_code, str(kind_code))
        thresholds[name] = (p50, p90)
        print(f"  {name:20s}: P50={p50:8.2f}  P90={p90:8.2f}  (n={len(values)})")

    with (DATA_DIR / "policy.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "experiment",
                "controller_type",
                "seat",
                "mean_delta_v_executed",
                "n_decisions",
                "game_index",
            ]
        )
        writer.writerows(seat_rows)

    validation_path = DATA_DIR / "validation_results.csv"
    write_header = not validation_path.exists()
    with validation_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if write_header:
            writer.writerow(
                ["analysis", "arm_or_scope", "checkpoint", "estimate", "ci_low", "ci_high", "n"]
            )
        writer.writerow(["政策分辨", "2v2 paired", "", point, low, high, len(pairs)])
        writer.writerow(
            [
                "政策分辨",
                "unpaired",
                "",
                unpaired,
                low_u,
                high_u,
                len(greedy_means) + len(sane_means),
            ]
        )
        for kind_code in sorted(regret_by_kind):
            name = KIND_NAMES.get(kind_code, str(kind_code))
            p50, p90 = thresholds[name]
            writer.writerow(["分档阈值", name, "P50", p50, "", "", len(regret_by_kind[kind_code])])
            writer.writerow(["分档阈值", name, "P90", p90, "", "", len(regret_by_kind[kind_code])])
    print(f"\nwrote {DATA_DIR / 'policy.csv'} and appended validation_results.csv")
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
