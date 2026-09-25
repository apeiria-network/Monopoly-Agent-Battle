"""Section 6.6 test 3 -- the GATE: does delta-V order candidates correctly?

WHAT THIS TESTS
---------------
Tests 1 and 2 of section 6.6 only show that V(s) tracks final outcomes ACROSS
situations. Regret, however, compares candidates WITHIN one decision, and
nothing in tests 1-2 vouches for that ordering. This test is the only direct
check, and section 6.6 makes the regret口径 live or die by it: if it fails,
R(d) may only be reported as "deviation from V", never as correctness.

DESIGN (why this is identification, not correlation)
----------------------------------------------------
SaneRandomController draws its action UNIFORMLY: first over options, then over
the option's target values (random_baseline.py:40-56). Its RNG is derived from
sha256("sane-random-v1:{seed}:{seat}:{player_id}") and never touches the engine
RNG stream, so whether the draw happened to land on a high-delta-V or
low-delta-V candidate is pure luck, independent of the position, the dice and
the opponents. The experiment is already inside the data; nothing is
re-simulated and no counterfactual is constructed (section 2.4 boundary).

Per decision d we stored the probability-weighted mid-percentile

    L_d = P(delta_v < delta_v_chosen) + P(ties)/2      E[L_d] = 1/2 by design

under that exact two-stage draw law (evaluate.py). Per (game, player) we then
form the mean luck z_i = mean(L_d - 1/2) and ask: do players who happened to
draw higher-delta-V candidates end the game better?

ESTIMATOR
---------
Within-game fixed effects: the four seats of one game share the dice sequence
and deck order, so z_i and the outcome are both demeaned per game and the
slope is estimated through the origin on the demeaned values. Inference is by
game-cluster bootstrap (10,000 resamples); decisions within a game are
correlated, so the game -- never the decision -- is the resampling unit.

Two preregistered outcomes, and BOTH must agree (section 6.6):
    Y1 = game points (3/2/1/0 by final ranking)  -> expect positive slope
    Y2 = final rank (1 = best)                   -> expect negative slope
Terminal net worth is reported as a third, robustness outcome.

POSITIVE CONTROL
----------------
The same pipeline with luck computed on delta-M_assets instead of delta-V.
Net worth is mechanically tied to the outcome, so this slope MUST be
significantly positive; if even that cannot be detected, the test has no
power and a negative main result would be uninformative. Section 6.6 rules the
whole test void in that case.

BALANCE CHECK (precondition)
----------------------------
E[L_d] = 1/2 is an identity only if the recorded draw law matches what the
controller actually did. Because L_d is discrete (an m-point draw set yields a
grid of mid-percentiles), a naive KS test against the continuous uniform
rejects mechanically at this sample size; instead we replay a small random
subsample of decisions, recompute the EXACT null variance of L_d per decision
from its values and draw probabilities, and form a z-statistic for
sum(L_d - 1/2). A naive KS against the continuous uniform is also printed,
for reference only.

VERDICT (preregistered in section 6.6, applied verbatim)
--------------------------------------------------------
PASS requires: balance check passes; positive control significantly positive;
main slope's 95% CI below-bounded above 0 with the correct sign for BOTH Y1
and Y2. Otherwise FAIL: regret may not be called correctness.

HOW TO READ THE OUTPUT
----------------------
Console prints each stage with its estimate, CI and verdict.
stat/judge/ranking.csv: one row per (game, player) -- the analysis input.
stat/judge/validation_results.csv: one row per reported number, keyed
    analysis=排序检验 / 平衡检验 (this is the paper-facing numbers table).

Usage from the repository root:
    .venv/Scripts/python.exe stat/judge/ranking.py [--table decisions_sane_random]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate as evaluate_module
import value as value_module
from replay_tools import iter_decision_points

from monopoly_agent_battle.decision.requests import _candidate_commands
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.board_data.classic_us_40 import BOARD_BY_POSITION

ROOT = Path(__file__).resolve().parent.parent.parent
RUNS = ROOT / "runs"
DATA_DIR = Path(__file__).resolve().parent / "data"
OUTPUT_DIR = Path(__file__).resolve().parent

BOOT_RESAMPLES = 10_000
BALANCE_SUBSAMPLE_GAMES = 10
RNG_SEED = 20260924


def _terminal_net_worth(result: dict, player_id: str) -> int:
    """Net worth at game end from result.json, mirroring classic_level0.net_worth."""
    player = result["players"][player_id]
    properties = result["properties"]
    total = int(player["cash"])
    for position in player["properties"]:
        space = BOARD_BY_POSITION[int(position)]
        state = properties[str(position)]
        total += space.price or 0
        total += (space.building_cost or 0) * int(state["building_level"])
        if state["mortgaged"]:
            total -= space.price or 0
    return total


def load_outcomes(
    experiment: str, game_names: list[str]
) -> dict[str, dict[str, tuple[float, ...]]]:
    """Return {game_name: {seat: (points, rank, net_worth)}} for each game."""
    outcomes: dict[str, dict[str, tuple[float, ...]]] = {}
    for name in game_names:
        directory = RUNS / experiment / name
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        seat_of = {
            str(player["player_id"]): int(player["seat"]) for player in config["config"]["players"]
        }
        rankings = list(result["rankings"])
        per_seat: dict[str, tuple[float, ...]] = {}
        for player_id, seat in seat_of.items():
            rank = rankings.index(player_id) + 1
            points = float(len(rankings) - rank)
            worth = float(_terminal_net_worth(result, player_id))
            per_seat[str(seat)] = (points, float(rank), worth)
        outcomes[name] = per_seat
    return outcomes


def build_player_rows(
    experiment: str,
) -> tuple[list[tuple[str, float, float, float, float, float, float, float]], dict[str, int]]:
    """Aggregate the decision table to one row per (game, player)."""
    table, columns = evaluate_module.load_table(experiment)
    # game_index -> directory name comes from the FILESYSTEM listing, the same
    # ordering evaluate.py scored against. The npz games array is concatenated
    # in shard order and is NOT positionally aligned with game_index.
    directories = evaluate_module.experiment_directories(experiment)
    index_to_name = {index: directory.name for index, directory in enumerate(directories)}
    outcomes = load_outcomes(experiment, list(index_to_name.values()))

    game_index = table[:, columns["game_index"]].astype(int)
    seat = table[:, columns["seat"]].astype(int)
    luck = table[:, columns["luck_L"]]
    luck_assets = table[:, columns["luck_L_massets"]]

    rows: dict[tuple[int, int], dict[str, list[float]]] = {}
    for g, s, luck_value, luck_assets_value in zip(
        game_index, seat, luck, luck_assets, strict=True
    ):
        if np.isnan(luck_value):
            continue
        entry = rows.setdefault((int(g), int(s)), {"luck": [], "luck_assets": []})
        entry["luck"].append(float(luck_value))
        entry["luck_assets"].append(float(luck_assets_value))

    player_rows: list[tuple[str, float, float, float, float, float, float, float]] = []
    for (g, s), entry in sorted(rows.items()):
        name = index_to_name.get(g)
        if name is None or name not in outcomes:
            continue
        per_seat = outcomes[name]
        seat_outcome = per_seat.get(str(s))
        if seat_outcome is None:
            continue
        points, rank, worth = seat_outcome
        player_rows.append(
            (
                name,
                float(s),
                float(np.mean(entry["luck"]) - 0.5),
                float(np.mean(entry["luck_assets"]) - 0.5),
                float(len(entry["luck"])),
                points,
                rank,
                worth,
            )
        )
    return player_rows, columns


def _fixed_effect_slope(
    z: np.ndarray, y: np.ndarray, games: np.ndarray
) -> tuple[float, np.ndarray]:
    """Within-game demeaned slope through the origin, with per-game contributions."""
    z_dm = np.empty_like(z)
    y_dm = np.empty_like(y)
    for game in np.unique(games):
        mask = games == game
        z_dm[mask] = z[mask] - z[mask].mean()
        y_dm[mask] = y[mask] - y[mask].mean()
    denom = float(z_dm @ z_dm)
    slope = float(z_dm @ y_dm / denom) if denom > 0 else float("nan")
    # Per-game score contributions, the bootstrap resampling unit.
    per_game = np.array(
        [
            (z_dm[games == game] @ y_dm[games == game], z_dm[games == game] @ z_dm[games == game])
            for game in np.unique(games)
        ]
    )
    return slope, per_game


def _cluster_bootstrap(per_game: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    """Game-cluster bootstrap CI for the slope, resampling whole games."""
    n = per_game.shape[0]
    estimates: list[float] = []
    for _ in range(BOOT_RESAMPLES):
        sample = per_game[rng.integers(0, n, size=n)]
        denom = sample[:, 1].sum()
        if denom > 0:
            estimates.append(float(sample[:, 0].sum() / denom))
    if not estimates:
        return float("nan"), float("nan")
    return float(np.percentile(estimates, 2.5)), float(np.percentile(estimates, 97.5))


def balance_check(experiment: str, game_names: list[str]) -> tuple[float, float, int]:
    """Exact-null z for sum(L-1/2) on a replayed subsample, plus naive KS.

    Returns (z_statistic, naive_ks_distance, decisions_checked).
    """
    rng = np.random.default_rng(RNG_SEED)
    names = [n for n in game_names if (RUNS / experiment / n / "events.jsonl").exists()]
    chosen = list(rng.choice(names, size=min(BALANCE_SUBSAMPLE_GAMES, len(names)), replace=False))

    observed = 0.0
    null_variance = 0.0
    luck_values: list[float] = []
    checked = 0
    for name in chosen:
        directory = RUNS / experiment / name
        for point in iter_decision_points(directory):
            engine = point.engine
            player_id = engine.state.current_player_id
            if engine.state.turn_phase is TurnPhase.PAYMENT_RESOLUTION:
                player_id = engine.state.settlement_operations[0].player_id
            kind = evaluate_module._phase_kind(engine, player_id)
            if kind is None:
                continue
            candidates = _candidate_commands(engine, player_id)
            if len(candidates) < 2:
                continue
            baseline = value_module.evaluate(engine.state, player_id)
            legal: list = []
            values: list[float] = []
            for command in candidates:
                scored = evaluate_module._evaluate_candidate(engine, command, player_id)
                if scored is None:
                    continue
                legal.append(command)
                values.append(scored[0] - baseline.total)
            executed_index = evaluate_module._match_executed(legal, point.command)
            if executed_index is None:
                continue
            probabilities = evaluate_module._draw_probabilities(engine, legal, kind)
            if executed_index not in probabilities or len(probabilities) < 2:
                continue
            l_obs = evaluate_module._mid_percentile(values, probabilities, executed_index)
            # Exact null moments: under the null, the chosen index follows the
            # two-stage law, so L's variance is E[(L(J)-1/2)^2] over that law.
            null_mean = 0.0
            null_second = 0.0
            for index, probability in probabilities.items():
                l_j = evaluate_module._mid_percentile(values, probabilities, index)
                null_mean += probability * l_j
                null_second += probability * l_j * l_j
            observed += l_obs - null_mean
            null_variance += null_second - null_mean * null_mean
            luck_values.append(l_obs)
            checked += 1
    z = observed / np.sqrt(null_variance) if null_variance > 0 else float("nan")
    if luck_values:
        grid = np.sort(np.array(luck_values))
        uniform = (np.arange(len(grid)) + 0.5) / len(grid)
        ks = float(np.max(np.abs(grid - uniform)))
    else:
        ks = float("nan")
    return float(z), ks, checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", default="sane_random")
    parser.add_argument("--games", type=int, default=None, help="cap games (dev only)")
    args = parser.parse_args()

    game_names = [d.name for d in evaluate_module.experiment_directories(args.experiment)]
    if args.games is not None:
        game_names = game_names[: args.games]

    print("=== balance check (precondition) ===", flush=True)
    z, ks, checked = balance_check(args.experiment, game_names)
    balance_ok = abs(z) < 1.96
    print(
        f"exact-null z = {z:.3f} over {checked} replayed decisions -> "
        f"{'ok' if balance_ok else 'FAILED'}"
    )
    print(f"naive KS vs continuous uniform = {ks:.3f} (reference only; L is discrete)")

    print("\n=== building (game, player) rows ===", flush=True)
    player_rows, _columns = build_player_rows(args.experiment)
    if args.games is not None:
        allowed = set(game_names)
        player_rows = [row for row in player_rows if row[0] in allowed]
    print(f"{len(player_rows)} (game, player) rows")

    games_arr = np.array([row[0] for row in player_rows])
    z_luck = np.array([row[2] for row in player_rows])
    z_assets = np.array([row[3] for row in player_rows])
    outcomes = {
        "points": (np.array([row[5] for row in player_rows]), +1),
        "rank": (np.array([row[6] for row in player_rows]), -1),
        "net_worth": (np.array([row[7] for row in player_rows]), +1),
    }

    rng = np.random.default_rng(RNG_SEED)
    results: dict[str, tuple[float, float, float]] = {}
    for label, luck_vector in (("main(dV)", z_luck), ("positive_control(dM)", z_assets)):
        for outcome_name, (y, sign) in outcomes.items():
            slope, per_game = _fixed_effect_slope(luck_vector, y, games_arr)
            low, high = _cluster_bootstrap(per_game, rng)
            # Sign-adjust so that the "correct" direction is positive for every
            # outcome (rank is inverted: better rank = smaller number).
            adjusted = (slope * sign, low * sign, high * sign)
            slope_a, low_a, high_a = (
                adjusted[0],
                min(adjusted[1], adjusted[2]),
                max(adjusted[1], adjusted[2]),
            )
            results[f"{label} x {outcome_name}"] = (slope_a, low_a, high_a)
            print(
                f"{label:24s} x {outcome_name:9s}: slope={slope_a:+.4f} "
                f"CI95=[{low_a:+.4f}, {high_a:+.4f}]"
            )

    control_ok = all(results[f"positive_control(dM) x {name}"][1] > 0 for name in outcomes)
    main_ok = all(results[f"main(dV) x {name}"][1] > 0 for name in ("points", "rank"))
    verdict = "PASS" if (balance_ok and control_ok and main_ok) else "FAIL"
    print(
        f"\nbalance={'ok' if balance_ok else 'FAIL'} "
        f"positive_control={'ok' if control_ok else 'FAIL'} "
        f"main(points&rank)={'ok' if main_ok else 'FAIL'}"
    )
    print(f"VERDICT (section 6.6 gate): {verdict}")
    if verdict == "FAIL":
        print("-> R(d) may only be reported as 'deviation from V', not correctness.")

    import csv

    with (OUTPUT_DIR / "ranking.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "game",
                "seat",
                "z_luck",
                "z_luck_massets",
                "n_decisions",
                "points",
                "rank",
                "net_worth",
            ]
        )
        writer.writerows(player_rows)

    validation_path = OUTPUT_DIR / "validation_results.csv"
    write_header = not validation_path.exists()
    with validation_path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        if write_header:
            writer.writerow(
                ["analysis", "arm_or_scope", "checkpoint", "estimate", "ci_low", "ci_high", "n"]
            )
        writer.writerow(["平衡检验", "exact-null z", "", z, "", "", checked])
        for key, (slope, low, high) in results.items():
            writer.writerow(["排序检验", key, "", slope, low, high, len(player_rows)])
    print(f"wrote {OUTPUT_DIR / 'ranking.csv'} and appended validation_results.csv")
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
