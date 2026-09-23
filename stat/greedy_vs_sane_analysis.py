"""Experiment 11 (greedy_script vs sane_random, 2v2) statistics (config doc section 10).

Primary statistic: T = the greedy_script side's total points over all valid
games (each game: 2 greedy_script seats + 2 sane_random seats; ranks score
3/2/1/0, the two sides sum to 6 per game). The 960-game design rotates all
C(4,2)=6 seat-pair configurations for the greedy side in equal (160-game)
proportion, so the design's own symmetry -- not either floor's data -- fixes
the null: E[T] = 3.0 * n_games, because greedy occupies every "good" and
"bad" seat combination equally often. This is the sole basis for the primary
verdict.

Floor convolution (both sane_random and greedy_script 800-game floors) is
reported only as a robustness / sanity check that the design-symmetric null
is not distorted by unexpectedly strong seat effects -- per config doc
section 10, neither floor may be used as an external reference for the
primary judgment, because both floors are themselves the objects being
compared here (unlike experiment 10, where a third-party floor calibrates a
court vs FE comparison).

Reported statistics:
  1. Primary: total T over all valid games, excess over the design-symmetric
     null (3.0/game), large-sample normal two-sided p, three-tier §8.5.2
     verdict against the sample's own excess-over-sd threshold.
  2. Floor cross-check: per-game null contribution from each floor's
     empirical seat-pair-sum PMF (matching the greedy seat pair actually
     used that game), convolved across all games, exact tails reported for
     both floors as a consistency check on (1).
  3. Paired effect size: per-game diff = T_i - (6 - T_i), Cliff's delta with
     a game-level bootstrap, CI95 for superiority and CI90 for the frozen
     TOST equivalence bound (delta = 0.25), mirroring cf_analysis.py.
  4. Seat-pair symmetry diagnostic: the six configurations are the three
     complementary bipartitions of the four seats -- {1,2}|{3,4},
     {1,3}|{2,4}, {1,4}|{2,3} -- each realized with greedy on either half.
     For each bipartition we report the greedy-side excess with greedy on
     each half; same-sign excess across both halves of a bipartition
     supports a genuine controller effect (not a seat artifact), opposite
     signs or a large within-bipartition gap flags possible seat-strength
     confounding that would need the floors' per-seat marginals to net out.

Process cross-check (bankruptcy rate, end_reason, round counts) is printed
but not scored — it is compared narratively against the section-8 800+800
floor figures already on record.

Run from the repository root:
    .venv/Scripts/python.exe stat/greedy_vs_sane_analysis.py
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from floor_test import (
    load_floors,
    normal_p_one_sided,
    pair_sum_pmf,
    verdict,
)

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs" / "greedy_vs_sane_random"
CSV_MAIN = ROOT / "stat" / "greedy_vs_sane_analysis.csv"
CSV_SEATPAIR = ROOT / "stat" / "greedy_vs_sane_seatpair.csv"
POINTS = (3, 2, 1, 0)
NULL_PER_GAME = 3.0
DELTA = 0.25
BOOT_RESAMPLES = 10_000

# The three complementary bipartitions of {1,2,3,4}; each entry pairs the two
# seat-pair configurations that are complements within one bipartition.
BIPARTITIONS: tuple[tuple[tuple[int, int], tuple[int, int]], ...] = (
    ((1, 2), (3, 4)),
    ((1, 3), (2, 4)),
    ((1, 4), (2, 3)),
)


def load_games() -> list[dict[str, Any]]:
    """Per-game greedy/sane points and the greedy side's seat pair."""
    games: list[dict[str, Any]] = []
    for gdir in sorted(RUNS.iterdir()):
        if not gdir.is_dir() or not (gdir / "result.json").exists():
            continue
        result: dict[str, Any] = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
        if result["validity_status"] != "valid":
            continue
        config: dict[str, Any] = json.loads((gdir / "config.json").read_text(encoding="utf-8"))[
            "config"
        ]
        players = {p["player_id"]: p for p in config["players"]}
        rank_of = {pid: i for i, pid in enumerate(result["rankings"])}
        greedy_ids = [pid for pid, p in players.items() if p["controller_type"] == "greedy_script"]
        sane_ids = [pid for pid, p in players.items() if p["controller_type"] == "sane_random"]
        greedy_points = sum(POINTS[rank_of[pid]] for pid in greedy_ids)
        sane_points = sum(POINTS[rank_of[pid]] for pid in sane_ids)
        greedy_seats = tuple(sorted(int(players[pid]["seat"]) for pid in greedy_ids))
        games.append(
            {
                "game_id": config["game_id"],
                "seed": config["seed"],
                "greedy_seats": greedy_seats,
                "greedy_points": greedy_points,
                "sane_points": sane_points,
                "rounds": result.get("total_rounds"),
                "end_reason": result.get("end_reason"),
                "bankrupt": sum(
                    1
                    for p in result.get("players", {}).values()
                    if isinstance(p, dict) and p.get("bankrupt")
                )
                if isinstance(result.get("players"), dict)
                else None,
            }
        )
    return games


def bootstrap_delta(diffs: list[int], rng: np.random.Generator) -> np.ndarray:
    """Bootstrap distribution of paired Cliff's delta over game resamples."""
    arr = np.asarray(diffs, dtype=float)
    n = len(arr)
    deltas = np.empty(BOOT_RESAMPLES)
    for i in range(BOOT_RESAMPLES):
        sample = arr[rng.integers(0, n, n)]
        wins = float(np.sum(sample > 0))
        losses = float(np.sum(sample < 0))
        deltas[i] = (wins - losses) / n
    return deltas


def normal_two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def main() -> None:
    games = load_games()
    n = len(games)
    if n == 0:
        print("no valid games found under runs/greedy_vs_sane_random -- nothing to analyze")
        return
    total = sum(int(g["greedy_points"]) for g in games)
    print(f"greedy_vs_sane_random: n={n} valid games, greedy-side total T={total}")

    rows: list[dict[str, Any]] = []

    # -- 1. Primary: design-symmetric null, large-sample normal test ----------
    null_mean = NULL_PER_GAME * n
    diffs_from_null = [int(g["greedy_points"]) - NULL_PER_GAME for g in games]
    sample_sd = float(np.std(diffs_from_null, ddof=1))
    se = sample_sd / math.sqrt(n)
    excess = total - null_mean
    z = excess / (se * n) if se > 0 else 0.0
    # excess is a TOTAL; convert to the same scale as se*n for the z-stat.
    z = (total / n - NULL_PER_GAME) / se if se > 0 else 0.0
    p_two_sided = normal_two_sided_p(z)
    tier = verdict(total / n - NULL_PER_GAME, se)
    print(
        f"\n[1. primary, design-symmetric null] E[T]={null_mean:.1f} "
        f"(={NULL_PER_GAME}/game x {n}) observed T={total} excess={excess:+.2f} "
        f"({excess / n:+.4f}/game) sample_sd/game={sample_sd:.4f} se(mean)={se:.4f} "
        f"z={z:+.3f} p_two_sided={p_two_sided:.5f} => {tier}"
    )
    rows.append(
        {
            "group": "greedy_vs_sane_primary",
            "games": n,
            "observed_total": total,
            "null_mean": round(null_mean, 4),
            "excess": round(excess, 4),
            "excess_per_game": round(excess / n, 4),
            "sample_sd_per_game": round(sample_sd, 4),
            "z": round(z, 4),
            "p_two_sided_normal": round(p_two_sided, 6),
            "verdict": tier,
        }
    )

    # -- 2. Floor cross-check: per-game seat-pair PMF convolution -------------
    floors = load_floors()
    for floor_name, (_, _, seat_points) in floors.items():
        pair_pmfs = [pair_sum_pmf(seat_points, *g["greedy_seats"]) for g in games]
        null_pmf = np.array([1.0])
        for pmf in pair_pmfs:
            null_pmf = np.convolve(null_pmf, pmf)
        support = np.arange(len(null_pmf))
        floor_null_mean = float((support * null_pmf).sum())
        floor_null_sd = float(np.sqrt(((support - floor_null_mean) ** 2 * null_pmf).sum()))
        p_lower = float(null_pmf[: total + 1].sum())
        p_upper = float(null_pmf[total:].sum())
        floor_excess = total - floor_null_mean
        print(
            f"[2. floor cross-check: {floor_name}] implied null_mean={floor_null_mean:.2f} "
            f"(design says {null_mean:.1f}) null_sd={floor_null_sd:.2f} "
            f"excess_vs_floor={floor_excess:+.2f} "
            f"p_exact(T>=obs)={p_upper:.4f} p_exact(T<=obs)={p_lower:.4f}"
        )
        rows.append(
            {
                "group": f"greedy_vs_sane_floor_crosscheck_{floor_name}",
                "games": n,
                "observed_total": total,
                "null_mean": round(floor_null_mean, 4),
                "excess": round(floor_excess, 4),
                "excess_per_game": round(floor_excess / n, 4),
                "sample_sd_per_game": round(floor_null_sd / math.sqrt(n), 4),
                "z": "",
                "p_two_sided_normal": "",
                "verdict": f"p_exact_stronger={p_upper:.4f} p_exact_weaker={p_lower:.4f}",
            }
        )

    # -- 3. Paired effect size --------------------------------------------------
    pair_diffs = [int(g["greedy_points"]) - int(g["sane_points"]) for g in games]
    rng = np.random.default_rng(42)
    deltas = bootstrap_delta(pair_diffs, rng)
    wins = sum(1 for d in pair_diffs if d > 0)
    losses = sum(1 for d in pair_diffs if d < 0)
    ties = n - wins - losses
    delta_hat = (wins - losses) / n
    ci95_lo, ci95_hi = (float(v) for v in np.percentile(deltas, [2.5, 97.5]))
    ci90_lo, ci90_hi = (float(v) for v in np.percentile(deltas, [5.0, 95.0]))
    if ci95_lo > 0:
        es_verdict = "greedy_script stronger"
    elif ci95_hi < 0:
        es_verdict = "sane_random stronger"
    elif ci90_lo >= -DELTA and ci90_hi <= DELTA:
        es_verdict = "equivalent"
    else:
        es_verdict = "indistinguishable"
    print(
        f"\n[3. paired cliff] wins={wins} losses={losses} ties={ties} "
        f"delta={delta_hat:+.4f} CI95=[{ci95_lo:+.4f},{ci95_hi:+.4f}] "
        f"CI90=[{ci90_lo:+.4f},{ci90_hi:+.4f}] => {es_verdict}"
    )
    rows.append(
        {
            "group": "greedy_vs_sane_paired_cliff",
            "games": n,
            "observed_total": total,
            "null_mean": "",
            "excess": "",
            "excess_per_game": "",
            "sample_sd_per_game": "",
            "z": "",
            "p_two_sided_normal": "",
            "verdict": f"delta={delta_hat:+.4f} CI95=[{ci95_lo:+.4f},{ci95_hi:+.4f}] "
            f"CI90=[{ci90_lo:+.4f},{ci90_hi:+.4f}] => {es_verdict}",
        }
    )

    CSV_MAIN.parent.mkdir(parents=True, exist_ok=True)
    with CSV_MAIN.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {CSV_MAIN} ({len(rows)} rows)")

    # -- 4. Seat-pair bipartition symmetry diagnostic --------------------------
    by_pair: dict[tuple[int, int], list[int]] = {}
    for g in games:
        by_pair.setdefault(g["greedy_seats"], []).append(int(g["greedy_points"]))

    seatpair_rows: list[dict[str, Any]] = []
    print("\n[4. seat-pair bipartition symmetry diagnostic]")
    for half_a, half_b in BIPARTITIONS:
        vals_a = by_pair.get(half_a, [])
        vals_b = by_pair.get(half_b, [])
        mean_a = float(np.mean(vals_a)) if vals_a else float("nan")
        mean_b = float(np.mean(vals_b)) if vals_b else float("nan")
        excess_a = mean_a - NULL_PER_GAME
        excess_b = mean_b - NULL_PER_GAME
        same_sign = (excess_a > 0) == (excess_b > 0) or (excess_a == 0 and excess_b == 0)
        gap = abs(excess_a - excess_b)
        flag = "OK (same sign)" if same_sign else "FLAG (opposite sign)"
        print(
            f"  bipartition {half_a}|{half_b}: "
            f"greedy@{half_a} n={len(vals_a):3d} mean={mean_a:.3f} excess={excess_a:+.3f}  |  "
            f"greedy@{half_b} n={len(vals_b):3d} mean={mean_b:.3f} excess={excess_b:+.3f}  "
            f"-> gap={gap:.3f} {flag}"
        )
        seatpair_rows.append(
            {
                "bipartition": f"{half_a}|{half_b}",
                "greedy_seats": str(half_a),
                "n": len(vals_a),
                "mean_T": round(mean_a, 4),
                "excess": round(excess_a, 4),
            }
        )
        seatpair_rows.append(
            {
                "bipartition": f"{half_a}|{half_b}",
                "greedy_seats": str(half_b),
                "n": len(vals_b),
                "mean_T": round(mean_b, 4),
                "excess": round(excess_b, 4),
            }
        )

    with CSV_SEATPAIR.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(seatpair_rows[0]))
        writer.writeheader()
        writer.writerows(seatpair_rows)
    print(f"wrote {CSV_SEATPAIR} ({len(seatpair_rows)} rows)")

    # -- Process cross-check (not scored) --------------------------------------
    end_reasons: dict[str, int] = {}
    for g in games:
        er = g["end_reason"] or "unknown"
        end_reasons[er] = end_reasons.get(er, 0) + 1
    rounds = [g["rounds"] for g in games if g["rounds"] is not None]
    print(f"\n[process cross-check, not scored] end_reason counts: {end_reasons}")
    if rounds:
        print(
            f"  rounds: mean={np.mean(rounds):.2f} min={min(rounds)} max={max(rounds)} "
            f"(section-8 floors: sane_random/greedy_script both ~50, round_limit-dominated)"
        )


if __name__ == "__main__":
    main()
