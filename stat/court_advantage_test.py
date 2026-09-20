"""Seat-calibrated significance test: do courts beat baselines in court-vs-baseline?

Method (Courts-Battle-config-details.md §8.5): for each court's 16 games (and the
pooled 64 games), the null hypothesis is "court strength = floor strength at the
same seat". The seat-calibrated expected total is E = Σ μ_k(i) and its variance is
Var = Σ σ_k(i)² over the court's seat sequence, with μ_k/σ_k² taken from the 800-game
scripted floors (population variance, ddof=0, matching analyze_seat_scores.py).
Report the calibrated excess T − E, the analytic z with a one-sided normal p, and the
exact one-sided p from the convolution of per-seat empirical point PMFs (deterministic
replacement for §8.5.3 Monte-Carlo resampling; same method §9 prescribes for
experiment 10). Verdicts follow §8.5.2: |excess| beyond the 95% detection threshold
(≈2σ of the null total) is "distinguishable"; inside ±1σ is "within noise band";
between is "directional deviation, not distinguishable". Both floors must agree.

Run from the repository root:
    .venv/Scripts/python.exe stat/court_advantage_test.py
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cvb_analysis import exact_pvalue, load_floor  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GAMES_JSON = ROOT / "stat" / "cvb_games.json"


def seat_moments(seat_points: dict[int, list[int]]) -> tuple[dict[int, float], dict[int, float]]:
    """Per-seat population mean and variance (ddof=0) of points."""
    means: dict[int, float] = {}
    variances: dict[int, float] = {}
    for seat, values in seat_points.items():
        mean = sum(values) / len(values)
        means[seat] = mean
        variances[seat] = sum((v - mean) ** 2 for v in values) / len(values)
    return means, variances


def normal_p_one_sided(z: float) -> float:
    """One-sided p for H1 'stronger than floor' (upper tail)."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def verdict(excess: float, sd: float) -> str:
    if abs(excess) >= 2 * sd:
        return "distinguishable (beyond 95% threshold)"
    if abs(excess) >= sd:
        return "directional deviation (beyond 1sd, below 95% threshold)"
    return "within noise band"


def report(
    label: str,
    seats: list[int],
    total: int,
    floors: dict[str, tuple[dict[int, float], dict[int, float], dict[int, list[int]]]],
    rows: list[dict[str, object]],
) -> None:
    n = len(seats)
    mean = total / n
    print(f"\n--- {label}: n={n} games, observed total={total} (uncalibrated mean {mean:.3f}) ---")
    for floor_name, (means, variances, seat_points) in floors.items():
        expected = sum(means[s] for s in seats)
        sd = math.sqrt(sum(variances[s] for s in seats))
        excess = total - expected
        z = excess / sd
        p_norm = normal_p_one_sided(z)
        p_exact = exact_pvalue(seats, total, seat_points)
        result = verdict(excess, sd)
        print(
            f"  [{floor_name}] calibrated expectation={expected:.2f} sd={sd:.2f} "
            f"excess={excess:+.2f} z={z:+.3f} p_normal={p_norm:.4f} p_exact={p_exact:.4f} "
            f"=> {result}"
        )
        rows.append(
            {
                "group": label,
                "games": n,
                "observed_total": total,
                "uncalibrated_mean": round(mean, 4),
                "floor": floor_name,
                "calibrated_expectation": round(expected, 4),
                "null_sd": round(sd, 4),
                "excess": round(excess, 4),
                "z": round(z, 4),
                "p_normal_one_sided": round(p_norm, 4),
                "p_exact_one_sided": round(p_exact, 4),
                "verdict": result,
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "stat" / "court_advantage_test.csv",
        help="CSV output path (default: stat/court_advantage_test.csv)",
    )
    args = parser.parse_args()

    games: list[dict[str, Any]] = json.loads(GAMES_JSON.read_text(encoding="utf-8"))["games"]
    valid = [g for g in games if g["validity"] == "valid"]
    sane_seats, _ = load_floor(ROOT / "runs" / "sane_random")
    greedy_seats, _ = load_floor(ROOT / "runs" / "greedy_script")
    floors = {
        "sane_random": (*seat_moments(sane_seats), sane_seats),
        "greedy_script": (*seat_moments(greedy_seats), greedy_seats),
    }

    rows: list[dict[str, object]] = []
    for ck in ("shang", "qin", "tang", "ming"):
        gs = [g for g in valid if g["court"] == ck]
        seats = [g["court_seat"] for g in gs]
        total = sum(g["court_points"] for g in gs)
        report(ck, seats, total, floors, rows)

    seats = [g["court_seat"] for g in valid]
    total = sum(g["court_points"] for g in valid)
    report("all_courts", seats, total, floors, rows)

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {args.csv} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
