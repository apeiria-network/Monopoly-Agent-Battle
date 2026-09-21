"""Cliff's delta effect sizes with bootstrap CIs for agent-vs-agent comparisons.

No floor data: every comparison is a direct agent-vs-agent contrast on observed
games, so luck/seat effects cancel inside the pairing instead of being calibrated
away. The sampling unit is always the GAME (decisions within a game are not
independent, Courts-Battle-config-details.md §6.3).

Comparisons (stat/effect_size.csv):
1. X vs baseline, PAIRED (court-vs-baseline & fe-vs-baseline runs): per game
   d = focus points - mean points of the 3 baselines = focus - (6-focus)/3.
2. FE vs mirror courts, PAIRED BY SEED: FE games mirror the first 16 cvb games
   (same seed, same seat); d = FE points - mirror court points.
3. Melee court vs court, PAIRED BY GAME: all four courts share every melee game;
   d = court A points - court B points.
4. cvb court vs court, INDEPENDENT (different seeds): independent-samples delta.

Cliff's delta: paired, δ = (W - L)/n with W/L = games with d > 0 / d < 0 (ties
ignored in the numerator); independent, δ = [#{x>y} - #{x<y}] / (n_x n_y).
δ ∈ [-1, 1], 0 = even; |δ| ≈ 0.147 / 0.33 / 0.474 are conventional
small/medium/large landmarks. CIs are bootstrap percentiles over games
(10 000 resamples): 95% for the superiority check, 90% for the TOST
equivalence check.

Verdict rule (smallest effect of interest Δ = 0.25, frozen by the project owner;
override with --delta): "stronger"/"weaker" iff the 95% CI excludes 0;
"equivalent" iff the 90% CI lies entirely inside (-Δ, +Δ); otherwise
"indistinguishable" — the only honest verdict when the CI spans both 0 and ±Δ.
Never read the point estimate alone as a difference.

Run from the repository root:
    .venv/Scripts/python.exe stat/effect_size.py [--delta 0.25]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from floor_test import COURTS, POINTS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_REPLICATES = 10_000
FloatVector = npt.NDArray[np.float64]

FocusGame = dict[str, Any]  # group, seed, focus_points, valid
MeleeGame = dict[str, Any]  # game_id, points per court, valid


def load_focus_games(exp_dir: Path) -> list[FocusGame]:
    """Focus-agent points and seed per game of a court-vs-baseline style run."""
    games: list[FocusGame] = []
    for gdir in sorted(exp_dir.iterdir()):
        if not gdir.is_dir() or gdir.name == "deprecate" or not (gdir / "result.json").exists():
            continue
        result: dict[str, Any] = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
        config: dict[str, Any] = json.loads((gdir / "config.json").read_text(encoding="utf-8"))[
            "config"
        ]
        focus_id = next(
            p["player_id"] for p in config["players"] if p["controller_type"] != "llm_baseline"
        )
        rank_of = {pid: i + 1 for i, pid in enumerate(result["rankings"])}
        games.append(
            {
                "group": COURTS[gdir.name[:2]],
                "seed": config["seed"],
                "focus_points": POINTS[rank_of[focus_id] - 1],
                "valid": result["validity_status"] == "valid",
            }
        )
    return games


def load_melee_games(exp_dir: Path) -> list[MeleeGame]:
    """Per-court points for every melee game."""
    games: list[MeleeGame] = []
    for gdir in sorted(exp_dir.iterdir()):
        if not gdir.is_dir() or not gdir.name.startswith("game-") or "." in gdir.name:
            continue
        if not (gdir / "result.json").exists():
            continue
        result: dict[str, Any] = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
        points = {str(pid).split("-")[0]: POINTS[i] for i, pid in enumerate(result["rankings"])}
        games.append(
            {
                "game_id": gdir.name,
                "points": points,
                "valid": result["validity_status"] == "valid",
            }
        )
    return games


def paired_delta(d: FloatVector) -> float:
    """Cliff's delta for paired differences: (W - L) / n."""
    return float((int(np.sum(d > 0)) - int(np.sum(d < 0))) / len(d))


def independent_delta(x: FloatVector, y: FloatVector) -> float:
    """Cliff's delta for two independent samples."""
    wins = int(np.sum(x[:, None] > y[None, :]))
    losses = int(np.sum(x[:, None] < y[None, :]))
    return (wins - losses) / (len(x) * len(y))


def bootstrap_paired(d: FloatVector, seed: int) -> FloatVector:
    rng = np.random.default_rng(seed)
    samples = d[rng.integers(0, len(d), size=(BOOTSTRAP_REPLICATES, len(d)))]
    return (np.sum(samples > 0, axis=1) - np.sum(samples < 0, axis=1)) / len(d)


def bootstrap_independent(x: FloatVector, y: FloatVector, seed: int) -> FloatVector:
    rng = np.random.default_rng(seed)
    xs = x[rng.integers(0, len(x), size=(BOOTSTRAP_REPLICATES, len(x)))]
    ys = y[rng.integers(0, len(y), size=(BOOTSTRAP_REPLICATES, len(y)))]
    wins = np.sum(xs[:, :, None] > ys[:, None, :], axis=(1, 2))
    losses = np.sum(xs[:, :, None] < ys[:, None, :], axis=(1, 2))
    return (wins - losses) / (len(x) * len(y))


def verdict(ci95: tuple[float, float], ci90: tuple[float, float], delta: float) -> str:
    if ci95[0] > 0:
        return "stronger"
    if ci95[1] < 0:
        return "weaker"
    if -delta < ci90[0] and ci90[1] < delta:
        return "equivalent"
    return "indistinguishable"


def analyse(
    comparison: str,
    design: str,
    n_label: str,
    delta_value: float,
    boots: FloatVector,
    delta_threshold: float,
    rows: list[dict[str, object]],
) -> None:
    lo95, hi95 = (float(v) for v in np.percentile(boots, [2.5, 97.5]))
    lo90, hi90 = (float(v) for v in np.percentile(boots, [5.0, 95.0]))
    ci95 = (lo95, hi95)
    ci90 = (lo90, hi90)
    result = verdict(ci95, ci90, delta_threshold)
    print(
        f"{comparison:<28} {design:<12} n={n_label:<7}"
        f"δ={delta_value:+.3f} CI95=[{ci95[0]:+.3f},{ci95[1]:+.3f}] "
        f"CI90=[{ci90[0]:+.3f},{ci90[1]:+.3f}] => {result}"
    )
    rows.append(
        {
            "comparison": comparison,
            "design": design,
            "n": n_label,
            "delta": round(delta_value, 4),
            "ci95_low": round(ci95[0], 4),
            "ci95_high": round(ci95[1], 4),
            "ci90_low": round(ci90[0], 4),
            "ci90_high": round(ci90[1], 4),
            "delta_threshold": delta_threshold,
            "verdict": result,
        }
    )


def analyse_paired(
    comparison: str,
    d: FloatVector,
    delta_threshold: float,
    rows: list[dict[str, object]],
    seed: int = 41,
) -> None:
    analyse(
        comparison,
        "paired",
        str(len(d)),
        paired_delta(d),
        bootstrap_paired(d, seed),
        delta_threshold,
        rows,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delta",
        type=float,
        default=0.25,
        help="smallest effect of interest for the equivalence verdict (default 0.25)",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "stat" / "effect_size.csv",
        help="CSV output path (default: stat/effect_size.csv)",
    )
    args = parser.parse_args()

    cvb = [g for g in load_focus_games(ROOT / "runs" / "court-vs-baseline") if g["valid"]]
    fe = [g for g in load_focus_games(ROOT / "runs" / "fe-vs-baseline") if g["valid"]]
    melee = [g for g in load_melee_games(ROOT / "runs" / "4-courts-battle") if g["valid"]]
    print(f"cvb={len(cvb)} fe={len(fe)} melee={len(melee)} valid games")

    rows: list[dict[str, object]] = []

    # 1. X vs baseline, paired: d = focus - baseline mean = focus - (6-focus)/3.
    def vs_baseline(games: list[FocusGame]) -> FloatVector:
        pts = np.asarray([g["focus_points"] for g in games], dtype=np.float64)
        return pts - (6.0 - pts) / 3.0

    for group in ("shang", "qin", "tang", "ming"):
        gs = [g for g in cvb if g["group"] == group]
        analyse_paired(f"{group}_vs_baseline", vs_baseline(gs), args.delta, rows)
    analyse_paired("all_courts_vs_baseline", vs_baseline(cvb), args.delta, rows)
    analyse_paired("fe_vs_baseline", vs_baseline(fe), args.delta, rows)

    # 2. FE vs mirror courts, paired by seed.
    cvb_by_seed = {g["seed"]: g for g in cvb}
    mirror = [(g, cvb_by_seed[g["seed"]]) for g in fe if g["seed"] in cvb_by_seed]
    d_mirror = np.asarray(
        [g["focus_points"] - m["focus_points"] for g, m in mirror], dtype=np.float64
    )
    analyse_paired("fe_vs_all_mirror_courts", d_mirror, args.delta, rows)
    for group in ("shang", "qin", "tang", "ming"):
        d_sub = np.asarray(
            [g["focus_points"] - m["focus_points"] for g, m in mirror if m["group"] == group],
            dtype=np.float64,
        )
        if len(d_sub):
            analyse_paired(f"fe_vs_{group}", d_sub, args.delta, rows)

    # 3. Melee court vs court, paired by game.
    courts = ("shang", "qin", "tang", "ming")
    for i, a in enumerate(courts):
        for b in courts[i + 1 :]:
            d = np.asarray([g["points"][a] - g["points"][b] for g in melee], dtype=np.float64)
            analyse_paired(f"melee_{a}_vs_{b}", d, args.delta, rows)

    # 4. cvb court vs court, independent samples (different seeds).
    for i, a in enumerate(courts):
        for b in courts[i + 1 :]:
            x = np.asarray([g["focus_points"] for g in cvb if g["group"] == a], dtype=np.float64)
            y = np.asarray([g["focus_points"] for g in cvb if g["group"] == b], dtype=np.float64)
            analyse(
                f"cvb_{a}_vs_{b}",
                "independent",
                f"{len(x)}v{len(y)}",
                independent_delta(x, y),
                bootstrap_independent(x, y, seed=43),
                args.delta,
                rows,
            )

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {args.csv} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
