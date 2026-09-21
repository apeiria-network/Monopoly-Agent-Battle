"""Plackett-Luce ranking-model strength estimates for the three experiment sets.

Model: for a game ranking r_1 > ... > r_m, P(ranking) = Π_j θ_{r_j} / Σ_{k≥j} θ_{r_k},
with per-entity strength θ ≥ 0 estimated by Hunter's MM algorithm. Unlike two-sample
tests, PL uses the full 4-player ranking of every game, so the within-game negative
correlation (points sum to 6) is handled by construction. Entities are agent types:
court-vs-baseline pools the 3 LLM baselines as one entity; the melee fits the four
courts. Theta is normalized so the reference entity (baseline, or the geometric mean
for the melee) equals 1; P(i beats j head-to-head) = θ_i / (θ_i + θ_j). 95% CIs come
from a non-parametric bootstrap over games (resample games, refit, renormalize).

Invalid games are excluded. Superseded games live in each experiment's
"deprecate/" subdirectory and are ignored. Exports stat/pl_strength.csv.

Interpretation guide
--------------------
theta: strength parameter; only RATIOS are meaningful. theta = 1.9 vs a
reference fixed at 1.0 reads "about 1.9x the reference strength" (Elo analogy:
a factor of ~1.44 ≈ +100 Elo). p_win translates theta into an intuitive
head-to-head probability: P(entity beats reference) = θ_i / (θ_i + θ_ref);
for the melee the reference is an "average opponent" (θ = geometric mean = 1).
Decision rule: if the 95% CI of p_win CROSSES 0.5, the comparison is
indistinguishable — the point estimate is descriptive only and must not be
reported as a ranking conclusion. A CI entirely above (below) 0.5 licenses
"stronger" ("weaker"). With 16 games the CI is roughly ±0.3 wide, so only
huge effects (p_win outside ~[0.2, 0.8]) are detectable; "not significant"
here means "not resolvable at this sample size", NOT "equal". Bootstrap CIs
are reported on the bounded probability scale because theta-ratio CIs blow up
whenever a resample leaves the reference nearly winless.

Run from the repository root:
    .venv/Scripts/python.exe stat/pl_strength.py
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

ROOT = Path(__file__).resolve().parent.parent
COURTS = {"SH": "shang", "QI": "qin", "TA": "tang", "MI": "ming", "FE": "fe"}
BOOTSTRAP_REPLICATES = 2000
MM_MAX_ITERS = 10_000
MM_TOL = 1e-12

Ranking = list[str]  # entity labels, best first
IntMatrix = npt.NDArray[np.int64]
FloatVector = npt.NDArray[np.float64]


def load_court_vs_baseline(exp_dir: Path) -> list[Ranking]:
    """Entity ranking per game: focus agent by group, baselines pooled."""
    games: list[Ranking] = []
    for gdir in sorted(exp_dir.iterdir()):
        if not gdir.is_dir() or gdir.name == "deprecate":
            continue
        if not (gdir / "result.json").exists():
            continue
        result: dict[str, Any] = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
        if result["validity_status"] != "valid":
            continue
        config: dict[str, Any] = json.loads((gdir / "config.json").read_text(encoding="utf-8"))[
            "config"
        ]
        focus_id = next(
            p["player_id"] for p in config["players"] if p["controller_type"] != "llm_baseline"
        )
        group = COURTS[gdir.name[:2]]
        games.append([group if pid == focus_id else "baseline" for pid in result["rankings"]])
    return games


def load_melee(exp_dir: Path) -> list[Ranking]:
    """Entity ranking per melee game: court name from player id prefix."""
    games: list[Ranking] = []
    for gdir in sorted(exp_dir.iterdir()):
        if not gdir.is_dir() or not gdir.name.startswith("game-") or "." in gdir.name:
            continue
        if not (gdir / "result.json").exists():
            continue
        result: dict[str, Any] = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
        if result["validity_status"] != "valid":
            continue
        games.append([str(pid).split("-")[0] for pid in result["rankings"]])
    return games


def encode(games: list[Ranking], entities: list[str]) -> IntMatrix:
    """Encode entity rankings as a (games, players) integer matrix."""
    index = {e: i for i, e in enumerate(entities)}
    return np.asarray([[index[e] for e in game] for game in games], dtype=np.int64)


def fit_pl(ranking_matrix: IntMatrix, n: int) -> FloatVector:
    """Hunter's MM algorithm, vectorized across games; returns normalized theta."""
    m = ranking_matrix.shape[1]
    wins = np.zeros(n)
    np.add.at(
        wins,
        ranking_matrix.ravel(),
        np.tile(np.arange(m - 1, -1, -1, dtype=np.float64), ranking_matrix.shape[0]),
    )
    theta = np.full(n, 1.0 / n)
    for _ in range(MM_MAX_ITERS):
        suffix = np.cumsum(theta[ranking_matrix][:, ::-1], axis=1)[:, ::-1]
        inv_cumsum = np.cumsum(1.0 / suffix, axis=1)
        denom = np.zeros(n)
        np.add.at(denom, ranking_matrix.ravel(), inv_cumsum.ravel())
        new_theta = wins / denom
        new_theta /= new_theta.sum()
        if float(np.max(np.abs(new_theta - theta))) < MM_TOL:
            theta = new_theta
            break
        theta = new_theta
    return theta


def normalize(theta: FloatVector, entities: list[str], reference: str | None) -> FloatVector:
    """Scale theta so the reference entity (or geometric mean if None) equals 1."""
    if reference is not None:
        return theta / theta[entities.index(reference)]
    return theta / float(np.exp(np.mean(np.log(theta))))


def win_probability(theta: FloatVector, entities: list[str], reference: str | None) -> FloatVector:
    """Head-to-head win probability of each entity against the reference opponent.

    With a named reference, P(entity beats reference). With geometric-mean
    normalization (reference=None), P(entity beats an average opponent, θ=1).
    Bounded in [0, 1], so it is the stable scale for confidence intervals.
    """
    if reference is not None:
        ref_theta = theta[entities.index(reference)]
        return theta / (theta + ref_theta)
    return theta / (theta + 1.0)


def bootstrap_ci(
    games: list[Ranking], entities: list[str], reference: str | None, seed: int
) -> tuple[FloatVector, FloatVector]:
    """Percentile 95% CI of the win probability over game-resampled refits."""
    rng = np.random.default_rng(seed)
    ranking_matrix = encode(games, entities)
    reps = np.zeros((BOOTSTRAP_REPLICATES, len(entities)))
    for b in range(BOOTSTRAP_REPLICATES):
        sample = ranking_matrix[rng.integers(0, len(games), size=len(games))]
        theta = normalize(fit_pl(sample, len(entities)), entities, reference)
        reps[b] = win_probability(theta, entities, reference)
    lo: FloatVector = np.percentile(reps, 2.5, axis=0)
    hi: FloatVector = np.percentile(reps, 97.5, axis=0)
    return lo, hi


def analyse(
    label: str,
    games: list[Ranking],
    reference: str | None,
    rows: list[dict[str, object]],
) -> None:
    entities = sorted({e for game in games for e in game})
    theta = normalize(fit_pl(encode(games, entities), len(entities)), entities, reference)
    p_win = win_probability(theta, entities, reference)
    lo, hi = bootstrap_ci(games, entities, reference, seed=29)
    opponent = reference or "average opponent"
    print(f"\n=== {label}: {len(games)} games, reference={opponent} ===")
    for i, entity in enumerate(entities):
        print(
            f"  {entity:<10} theta={theta[i]:.3f}  P(beat {opponent})={p_win[i]:.3f}  "
            f"95% CI [{lo[i]:.3f}, {hi[i]:.3f}]"
        )
        rows.append(
            {
                "experiment": label,
                "entity": entity,
                "games": len(games),
                "theta": round(float(theta[i]), 4),
                "p_win": round(float(p_win[i]), 4),
                "p_win_ci_low": round(float(lo[i]), 4),
                "p_win_ci_high": round(float(hi[i]), 4),
                "opponent": opponent,
            }
        )
    print("  pairwise P(row beats column):")
    header = "".join(f"{e[:8]:>10}" for e in entities)
    print(f"  {'':<10}{header}")
    for i, ei in enumerate(entities):
        cells = "".join(
            f"{theta[i] / (theta[i] + theta[j]):>10.3f}" for j, _ in enumerate(entities)
        )
        print(f"  {ei[:8]:<10}{cells}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "stat" / "pl_strength.csv",
        help="CSV output path (default: stat/pl_strength.csv)",
    )
    args = parser.parse_args()

    rows: list[dict[str, object]] = []
    analyse(
        "court-vs-baseline",
        load_court_vs_baseline(ROOT / "runs" / "court-vs-baseline"),
        "baseline",
        rows,
    )
    analyse(
        "fe-vs-baseline",
        load_court_vs_baseline(ROOT / "runs" / "fe-vs-baseline"),
        "baseline",
        rows,
    )
    analyse("4-courts-battle", load_melee(ROOT / "runs" / "4-courts-battle"), None, rows)

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {args.csv} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
