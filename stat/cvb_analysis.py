"""Seat-calibrated null-distribution test for court-vs-baseline experiments.

Scope: ONLY the §8.5.3 null-distribution p-value plus the §8.5.2 verdict
(Courts-Battle-config-details.md). For a group's games, the exact one-sided p
(P of a luck-only total >= observed at the same seats) comes from convolving
per-seat empirical point PMFs over {0,1,2,3} taken from the 800-game scripted
floors — the deterministic equivalent of Monte-Carlo resampling. The verdict
(§8.5.2 three tiers) uses the floor seat moments internally; moments stay out
of the exported CSV. Both floors (sane_random, greedy_script) are reported and
must agree. CSV columns: group, games, observed_total, floor, p_exact_one_sided,
verdict.

Shared helpers (load_game / load_floor / report / ...) are also used by
fe_advantage_test.py. Net worth, token costs, per-game details: intentionally
out of scope, to be analysed separately later.

Run from the repository root:
    .venv/Scripts/python.exe stat/cvb_analysis.py
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CVB_DIR = ROOT / "runs" / "court-vs-baseline"
COURTS = {"SH": "shang", "QI": "qin", "TA": "tang", "MI": "ming", "FE": "fe"}
POINTS = (3, 2, 1, 0)

SeatPoints = dict[int, list[int]]
GameRow = dict[str, Any]
Floors = dict[str, tuple[dict[int, float], dict[int, float], SeatPoints]]


def load_game(gdir: Path) -> GameRow:
    """Minimal per-game extraction: focus player identity, seat, rank, points."""
    result: dict[str, Any] = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
    config: dict[str, Any] = json.loads((gdir / "config.json").read_text(encoding="utf-8"))[
        "config"
    ]
    players = config["players"]
    focus = next(p for p in players if p["controller_type"] != "llm_baseline")
    profiles = config["model_profiles"]
    role_profiles: dict[str, Any] = focus.get("court_role_profiles") or {}
    base_profile: str | None = role_profiles.get("emperor") or role_profiles.get("leader")
    rank_of = {pid: i + 1 for i, pid in enumerate(result["rankings"])}
    focus_id: str = focus["player_id"]
    return {
        "game_id": gdir.name,
        "group": COURTS[gdir.name[:2]],
        "seat": focus["seat"],
        "rank": rank_of[focus_id],
        "points": POINTS[rank_of[focus_id] - 1],
        "validity": result["validity_status"],
        "base_model": profiles[base_profile]["model"] if base_profile else None,
    }


def load_floor(floor_dir: Path) -> SeatPoints:
    """Per-seat point lists over valid games of a scripted floor experiment."""
    seat_points: SeatPoints = {1: [], 2: [], 3: [], 4: []}
    for gdir in sorted(floor_dir.iterdir()):
        if not gdir.is_dir() or not (gdir / "result.json").exists():
            continue
        result: dict[str, Any] = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
        config: dict[str, Any] = json.loads((gdir / "config.json").read_text(encoding="utf-8"))[
            "config"
        ]
        if result["validity_status"] != "valid":
            continue
        seat_of = {p["player_id"]: p["seat"] for p in config["players"]}
        for i, pid in enumerate(result["rankings"]):
            seat_points[seat_of[pid]].append(POINTS[i])
    return seat_points


def seat_moments(seat_points: SeatPoints) -> tuple[dict[int, float], dict[int, float]]:
    """Per-seat population mean and variance (ddof=0) of points."""
    means: dict[int, float] = {}
    variances: dict[int, float] = {}
    for seat, values in seat_points.items():
        mean = sum(values) / len(values)
        means[seat] = mean
        variances[seat] = sum((v - mean) ** 2 for v in values) / len(values)
    return means, variances


def exact_pvalue(seats: list[int], total: int, seat_points: SeatPoints) -> float:
    """Exact P(luck-only seat-calibrated total >= observed), one-sided, by convolution."""
    pmf = np.array([1.0])
    for seat in seats:
        values = seat_points[seat]
        seat_pmf = np.bincount(values, minlength=4)[:4] / len(values)
        pmf = np.convolve(pmf, seat_pmf)
    return float(pmf[total:].sum())


def verdict(excess: float, sd: float) -> str:
    if abs(excess) >= 2 * sd:
        return "distinguishable (beyond 95% threshold)"
    if abs(excess) >= sd:
        return "directional deviation (beyond 1sd, below 95% threshold)"
    return "within noise band"


def report(
    label: str, seats: list[int], total: int, floors: Floors, rows: list[dict[str, object]]
) -> None:
    """Print and collect one group's null-distribution test result for every floor."""
    n = len(seats)
    print(f"\n--- {label}: n={n} games, observed total={total} ---")
    for floor_name, (means, variances, seat_points) in floors.items():
        expected = sum(means[s] for s in seats)
        sd = math.sqrt(sum(variances[s] for s in seats))
        excess = total - expected
        p_exact = exact_pvalue(seats, total, seat_points)
        result = verdict(excess, sd)
        print(f"  [{floor_name}] p_exact={p_exact:.4f} => {result}")
        rows.append(
            {
                "group": label,
                "games": n,
                "observed_total": total,
                "floor": floor_name,
                "p_exact_one_sided": round(p_exact, 4),
                "verdict": result,
            }
        )


def load_floors() -> Floors:
    """Load both scripted floors with their seat moments."""
    floors: Floors = {}
    for name in ("sane_random", "greedy_script"):
        seat_points = load_floor(ROOT / "runs" / name)
        floors[name] = (*seat_moments(seat_points), seat_points)
    return floors


def write_csv(rows: list[dict[str, object]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {csv_path} ({len(rows)} rows)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "stat" / "cvb_analysis.csv",
        help="CSV output path (default: stat/cvb_analysis.csv)",
    )
    args = parser.parse_args()

    games: list[GameRow] = []
    for gdir in sorted(CVB_DIR.iterdir()):
        if not gdir.is_dir() or "-bak" in gdir.name or gdir.name.endswith(".invalid"):
            continue
        if (gdir / "result.json").exists():
            games.append(load_game(gdir))
    valid = [g for g in games if g["validity"] == "valid"]
    print(f"loaded {len(games)} games, valid={len(valid)}")

    floors = load_floors()
    rows: list[dict[str, object]] = []
    for group in ("shang", "qin", "tang", "ming"):
        gs = [g for g in valid if g["group"] == group]
        report(
            group,
            [g["seat"] for g in gs],
            sum(g["points"] for g in gs),
            floors,
            rows,
        )
    report(
        "all_courts",
        [g["seat"] for g in valid],
        sum(g["points"] for g in valid),
        floors,
        rows,
    )
    write_csv(rows, args.csv)


if __name__ == "__main__":
    main()
