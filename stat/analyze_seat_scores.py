"""Per-seat score statistics for the scripted zero-LLM benchmark floors.

Scoring follows Courts-Battle-config-details.md §8: final rank maps to points
3/2/1/0 (rank 1 = 3 points, rank 4 = 0); the equal-strength expectation is 1.5.
For each experiment the script scans ``runs/<experiment>/<game>/result.json``
and reports, per seat 1-4, the cross-game point mean, variance (population,
ddof=0 — the 200-game set is consumed as the benchmark floor itself), standard
deviation and rank distribution, plus the seat effect (max-min seat mean).

Run from the repository root:
    .venv/Scripts/python.exe stat/analyze_seat_scores.py
    .venv/Scripts/python.exe stat/analyze_seat_scores.py --experiments greedy_script
    .venv/Scripts/python.exe stat/analyze_seat_scores.py --csv stat/seat_scores.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

_POINTS_BY_RANK = (3, 2, 1, 0)  # rank 1..4 -> points
_DEFAULT_EXPERIMENTS = ("greedy_script", "sane_random")


def _seat_of(player_id: str) -> int:
    """Player ids in scripted experiments end with their seat number."""
    return int(player_id.rsplit("-", 1)[-1])


def _collect(experiment: str) -> tuple[dict[int, list[int]], int]:
    """Return (seat -> list of per-game points, valid game count)."""
    per_seat: dict[int, list[int]] = {seat: [] for seat in (1, 2, 3, 4)}
    root = Path("runs") / experiment
    if not root.is_dir():
        raise SystemExit(f"run directory not found: {root}")
    games = 0
    for result_path in sorted(root.glob("*/result.json")):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        rankings = result.get("rankings")
        if result.get("validity_status") != "valid" or not rankings:
            continue
        games += 1
        for rank_index, player_id in enumerate(rankings):
            per_seat[_seat_of(str(player_id))].append(_POINTS_BY_RANK[rank_index])
    if games == 0:
        raise SystemExit(f"no valid games with results under {root}")
    return per_seat, games


def _rank_distribution(experiment: str, seat: int) -> list[int]:
    root = Path("runs") / experiment
    counts = [0, 0, 0, 0]
    for result_path in sorted(root.glob("*/result.json")):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        rankings = result.get("rankings")
        if result.get("validity_status") != "valid" or not rankings:
            continue
        for rank_index, player_id in enumerate(rankings):
            if _seat_of(str(player_id)) == seat:
                counts[rank_index] += 1
    return counts


def _mean(values: list[int]) -> float:
    return sum(values) / len(values)


def _pvariance(values: list[int], mean: float) -> float:
    return sum((v - mean) ** 2 for v in values) / len(values)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiments",
        nargs="+",
        default=list(_DEFAULT_EXPERIMENTS),
        help="experiment run-directory names under runs/ (default: both floors)",
    )
    parser.add_argument("--csv", type=Path, default=None, help="optional CSV output path")
    args = parser.parse_args()

    rows: list[dict[str, object]] = []
    for experiment in args.experiments:
        per_seat, games = _collect(experiment)
        print(f"\n=== {experiment}: {games} valid games ===")
        print(f"{'seat':<5}{'n':>4}{'mean':>8}{'var':>8}{'std':>8}   rank1/2/3/4 distribution")
        means: list[float] = []
        for seat in (1, 2, 3, 4):
            values = per_seat[seat]
            mean = _mean(values)
            var = _pvariance(values, mean)
            dist = _rank_distribution(experiment, seat)
            means.append(mean)
            print(
                f"{seat:<5}{len(values):>4}{mean:>8.3f}{var:>8.3f}{math.sqrt(var):>8.3f}"
                f"   {dist[0]}/{dist[1]}/{dist[2]}/{dist[3]}"
            )
            rows.append(
                {
                    "experiment": experiment,
                    "games": games,
                    "seat": seat,
                    "n": len(values),
                    "mean_points": round(mean, 4),
                    "variance": round(var, 4),
                    "std": round(math.sqrt(var), 4),
                    "rank1": dist[0],
                    "rank2": dist[1],
                    "rank3": dist[2],
                    "rank4": dist[3],
                }
            )
        print(f"seat effect (max-min mean): {max(means) - min(means):.3f} (chance mean = 1.5)")

    if args.csv is not None:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote {args.csv} ({len(rows)} rows)")


if __name__ == "__main__":
    sys.exit(main())
