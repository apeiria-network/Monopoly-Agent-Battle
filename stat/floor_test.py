"""Floor-calibrated null-distribution test: shared core for "X vs baseline" experiments.

Two calibration methods (Courts-Battle-config-details.md §8.5), both reported per
group per floor:

1. Scripted-floor calibration (§8.5.2) -> p_normal_one_sided + verdict. Per-seat
   mean μ_k and population variance σ_k² (ddof=0, matching analyze_seat_scores.py)
   of game points from the two 800-game scripted floors (sane_random,
   greedy_script). Floors matter because seats are not exchangeable: seat 1
   mean ≈ 1.63 vs seat 4 mean ≈ 1.39; a group's games are always judged against
   the seats it actually occupied. z = excess/σ_total, p from the normal tail.
   NOTE: this is the analytic moment-based approximation — fast but crude for
   small n and for the discrete 0-3 point distribution.

2. Monte-Carlo test (§8.5.3) -> p_exact_one_sided. The exact probability that a
   pure-luck player, playing the group's seat sequence, scores a total >= the
   observed total. Computed by convolving per-seat empirical point PMFs over
   {0,1,2,3} — the deterministic equivalent of resampling the 800 floor games,
   instant and free of sampling error; it uses the full empirical distribution
   rather than only its first two moments.

The verdict column applies the §8.5.2 three-tier rule (|excess| vs 1σ/2σ of the
null total, floor moments internal only). Both floors must yield the same tier
before any conclusion is drawn; per §8.5.2 the Monte-Carlo exact p is the
cross-check of the calibration verdict.

Interpretation guide
--------------------
Both p columns are one-sided "stronger than luck" tails; p < 0.025 is required
before claiming "stronger"; p ≈ 0.5 means at the luck expectation; p near 1
means BELOW expectation. When the two p's disagree, trust p_exact (exact) over
p_normal (approximation). Wording discipline: never write "tie" or "there is a
difference" — the only licensed statements are "indistinguishable" (within
noise band / directional deviation) or "distinguishable". "Directional
deviation" (1σ–2σ) is NOT a permitted "trend" claim.

Library only — no main. Entry points: courts_analysis.py (experiment 1),
cvb_analysis.py (experiment 2), fe_analysis.py (experiment 3),
cf_analysis.py (experiment 10, court faction vs FE).
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
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
        if not gdir.is_dir() or gdir.name == "deprecate":
            continue
        if not (gdir / "result.json").exists():
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


def pair_sum_pmf(seat_points: SeatPoints, seat_a: int, seat_b: int) -> np.ndarray:
    """Empirical PMF of a seat PAIR's combined points (support 0..6).

    load_floor appends exactly one value per seat per valid game in directory
    order, so index k of each seat list is the same floor game; pairing by
    index preserves the within-game dependence between the two seats.
    """
    values_a = seat_points[seat_a]
    values_b = seat_points[seat_b]
    assert len(values_a) == len(values_b)
    sums = [a + b for a, b in zip(values_a, values_b, strict=True)]
    return np.bincount(sums, minlength=7)[:7] / len(sums)


def convolve_pmfs(pmfs: list[np.ndarray]) -> np.ndarray:
    """Convolve a list of PMFs (arbitrary supports) into the total's PMF."""
    total_pmf = np.array([1.0])
    for pmf in pmfs:
        total_pmf = np.convolve(total_pmf, pmf)
    return total_pmf


def exact_tails(pmf: np.ndarray, total: int) -> tuple[float, float]:
    """Exact lower/upper one-sided tails P(T <= total) and P(T >= total)."""
    return float(pmf[: total + 1].sum()), float(pmf[total:].sum())


def verdict(excess: float, sd: float) -> str:
    """§8.5.2 three-tier rule from the excess over the calibrated expectation."""
    if abs(excess) >= 2 * sd:
        return "distinguishable (beyond 95% threshold)"
    if abs(excess) >= sd:
        return "directional deviation (beyond 1sd, below 95% threshold)"
    return "within noise band"


def normal_p_one_sided(z: float) -> float:
    """Method 1 (scripted-floor calibration) p-value: normal upper tail of z."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def report(
    label: str, seats: list[int], total: int, floors: Floors, rows: list[dict[str, object]]
) -> None:
    """Print and collect one group's test results for every floor (both methods)."""
    n = len(seats)
    print(f"\n--- {label}: n={n} games, observed total={total} ---")
    for floor_name, (means, variances, seat_points) in floors.items():
        expected = sum(means[s] for s in seats)
        sd = math.sqrt(sum(variances[s] for s in seats))
        excess = total - expected
        p_normal = normal_p_one_sided(excess / sd)
        p_exact = exact_pvalue(seats, total, seat_points)
        result = verdict(excess, sd)
        print(f"  [{floor_name}] p_normal={p_normal:.4f} p_exact={p_exact:.4f} => {result}")
        rows.append(
            {
                "group": label,
                "games": n,
                "observed_total": total,
                "floor": floor_name,
                "p_normal_one_sided": round(p_normal, 4),
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
