"""Experiment 10 (CF, court faction vs FE) final statistics (config doc section 9).

Primary statistic: T = the court faction's total points over the 12 games
(each game: 2 ming courts + 2 FE; ranks score 3/2/1/0, faction sums to 6 per
game). A-priori band frozen in the doc: E = 36, sd = 4.5, threshold 9; T
inside [27, 45] is the indistinguishable noise band.

Floor review (the section-8.5 machinery adapted to factions): the null for
each game is the empirical PMF of the occupied SEAT PAIR's combined points in
the two 800-game scripted floors (pairing by game index preserves within-game
seat dependence). Convolving the 12 pair PMFs gives the exact null of T; both
one-sided exact tails and the normal-moment approximation are reported for
both floors, and the conservative floor rules the verdict per the doc.

Secondary: paired Cliff's delta of per-game (court - FE) point differences
with a 10k-resample game-level bootstrap; CI95 for superiority, CI90 for the
frozen TOST equivalence bound (delta = 0.25), same conventions as
effect_size.py.

Run from the repository root:
    .venv/Scripts/python.exe stat/cf_analysis.py
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, cast

import numpy as np
from floor_test import (
    convolve_pmfs,
    exact_tails,
    load_floors,
    normal_p_one_sided,
    pair_sum_pmf,
    verdict,
)

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs" / "court-fe-battle"
CSV_PATH = ROOT / "stat" / "cf_analysis.csv"
POINTS = (3, 2, 1, 0)
BASE_LETTER = {
    "qwen3.8-flash": "A",
    "deepseek-v4-flash": "B",
    "gpt-5.6-luna": "C",
    "GLM-5.3-Flash": "D",
}
APRIORI_E = 36.0
APRIORI_SD = 4.5
APRIORI_BAND = 9.0
DELTA = 0.25
BOOT_RESAMPLES = 10_000


def load_cf_games() -> list[dict[str, Any]]:
    """Per-game court seats, faction points, and base model letter."""
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
        court_ids = sorted(pid for pid in players if pid.startswith("court"))
        fe_ids = sorted(pid for pid in players if pid.startswith("fe"))
        rank_of = {pid: i for i, pid in enumerate(result["rankings"])}
        court_points = sum(POINTS[rank_of[pid]] for pid in court_ids)
        fe_points = sum(POINTS[rank_of[pid]] for pid in fe_ids)
        court_seats = sorted(int(players[pid]["seat"]) for pid in court_ids)
        role_profiles = cast(dict[str, Any], players[court_ids[0]].get("court_role_profiles") or {})
        chief_profile = cast(str | None, role_profiles.get("emperor"))
        base_model = config["model_profiles"][chief_profile]["model"] if chief_profile else ""
        games.append(
            {
                "game_id": config["game_id"],
                "court_seats": court_seats,
                "court_points": court_points,
                "fe_points": fe_points,
                "base_model": BASE_LETTER.get(base_model, base_model),
                "rankings": ">".join(result["rankings"]),
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


def main() -> None:
    games = load_cf_games()
    total = sum(int(g["court_points"]) for g in games)
    n = len(games)
    print(f"CF games: {n}, court faction total T = {total} (FE = {6 * n - total})")
    print("\nper-game results:")
    for g in games:
        seats = "-".join(str(s) for s in g["court_seats"])
        print(
            f"  {g['game_id']}: base={g['base_model']} court_seats={seats} "
            f"court={g['court_points']} fe={g['fe_points']} [{g['rankings']}]"
        )

    # A-priori 搂9 band verdict (frozen E/sd/threshold).
    apriori_excess = total - APRIORI_E
    band = (
        "within noise band (indistinguishable)"
        if abs(apriori_excess) <= APRIORI_BAND
        else "outside noise band"
    )
    print(
        f"\n[a-priori 搂9] E={APRIORI_E}, sd={APRIORI_SD}, band=卤{APRIORI_BAND}: "
        f"excess={apriori_excess:+.1f} => {band}"
    )

    rows: list[dict[str, Any]] = []
    floors = load_floors()
    for floor_name, (means, variances, seat_points) in floors.items():
        pair_pmfs = [
            pair_sum_pmf(seat_points, int(g["court_seats"][0]), int(g["court_seats"][1]))
            for g in games
        ]
        null_pmf = convolve_pmfs(pair_pmfs)
        support = np.arange(len(null_pmf))
        null_mean = float((support * null_pmf).sum())
        null_sd = float(np.sqrt(((support - null_mean) ** 2 * null_pmf).sum()))
        # Moment route via per-seat calibration (mirrors the 搂8.5.2 z path).
        seat_mean_total = sum(
            means[int(g["court_seats"][0])] + means[int(g["court_seats"][1])] for g in games
        )
        seat_var_total = sum(
            variances[int(g["court_seats"][0])] + variances[int(g["court_seats"][1])] for g in games
        )
        seat_sd = math.sqrt(seat_var_total)
        excess = total - seat_mean_total
        p_normal = normal_p_one_sided(excess / seat_sd)
        p_lower, p_upper = exact_tails(null_pmf, total)
        tier = verdict(excess, seat_sd)
        print(
            f"  [{floor_name}] null_mean={null_mean:.2f} null_sd={null_sd:.2f} "
            f"excess={excess:+.2f} p_normal_weaker={1 - p_normal:.4f} "
            f"p_exact_weaker={p_lower:.4f} p_exact_stronger={p_upper:.4f} => {tier}"
        )
        rows.append(
            {
                "group": "court_faction_vs_fe",
                "games": n,
                "observed_total": total,
                "floor": floor_name,
                "null_mean_exact": round(null_mean, 4),
                "null_sd_exact": round(null_sd, 4),
                "excess": round(excess, 4),
                "p_normal_one_sided_weaker": round(1 - p_normal, 4),
                "p_exact_one_sided_weaker": round(p_lower, 4),
                "p_exact_one_sided_stronger": round(p_upper, 4),
                "verdict": tier,
                "apriori_band_verdict": band,
            }
        )

    diffs = [int(g["court_points"]) - int(g["fe_points"]) for g in games]
    rng = np.random.default_rng(42)
    deltas = bootstrap_delta(diffs, rng)
    wins = sum(1 for d in diffs if d > 0)
    losses = sum(1 for d in diffs if d < 0)
    delta_hat = (wins - losses) / n
    ci95_lo, ci95_hi = (float(v) for v in np.percentile(deltas, [2.5, 97.5]))
    ci90_lo, ci90_hi = (float(v) for v in np.percentile(deltas, [5.0, 95.0]))
    if ci95_lo > 0:
        es_verdict = "court stronger"
    elif ci95_hi < 0:
        es_verdict = "court weaker"
    elif ci90_lo >= -DELTA and ci90_hi <= DELTA:
        es_verdict = "equivalent"
    else:
        es_verdict = "indistinguishable"
    print(
        f"\n[paired cliff] wins={wins} losses={losses} ties={n - wins - losses} "
        f"delta={delta_hat:+.3f} CI95=[{ci95_lo:+.3f},{ci95_hi:+.3f}] "
        f"CI90=[{ci90_lo:+.3f},{ci90_hi:+.3f}] => {es_verdict}"
    )
    rows.append(
        {
            "group": "court_faction_vs_fe_paired_cliff",
            "games": n,
            "observed_total": total,
            "floor": "-",
            "null_mean_exact": "",
            "null_sd_exact": "",
            "excess": "",
            "p_normal_one_sided_weaker": "",
            "p_exact_one_sided_weaker": "",
            "p_exact_one_sided_stronger": "",
            "verdict": f"delta={delta_hat:+.3f} CI95=[{ci95_lo:+.3f},{ci95_hi:+.3f}] "
            f"CI90=[{ci90_lo:+.3f},{ci90_hi:+.3f}]",
            "apriori_band_verdict": es_verdict,
        }
    )

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {CSV_PATH} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
