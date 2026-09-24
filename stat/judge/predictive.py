"""Section 6.6 tests 1 (+placebo): does V(s) predict final net worth?

WHAT THIS COMPUTES
------------------
Test 1 is the predictive-power check. It replays every floor game and, every 5
complete rounds (r = 5, 10, ..., 50), evaluates V(s) for each surviving player.
The label is that player's TRUE terminal net worth from result.json. If V(s)
carries no information about where a player ends up, the whole section-6 value
function is dead before the harder questions are even asked.

Three arms, all on the same checkpoints:

    main       panel Spearman correlation of V(s) vs terminal net worth over
               all (game, checkpoint, player) observations, plus the rho curve
               by round (r = 5...50). Section 6.6 requires rho to INCREASE with
               r: later states should predict better.
    control    the identical pipeline using only M_assets (net worth) instead
               of full V. Section 6.6 requires V to beat it: the rent and
               monopoly terms must add predictive content, not noise.
    placebo    the identical pipeline after shuffling terminal net worth
               BETWEEN games. rho must fall back to ~0; if it stays positive,
               the test manufactures correlation from thin air and all of
               section 6.6 is void.

SCOPE (section 6.6)
-------------------
Floor games ONLY: sane_random 800 + greedy_script 800 + greedy_vs_sane_random
960 = 2,560 games. The 108 LLM games are never touched here.

INFERENCE
---------
Per-checkpoint rho uses every surviving player. The headline "V beats
M_assets" contrast is the per-checkpoint difference in rho, cluster-
bootstrapped by GAME (10,000 resamples): observations within a game are
correlated, so the game is the resampling unit. Verdict follows the
preregistered rule in section 6.6: main rho significantly above the control
arm (CI95 of the paired difference excludes 0) AND rho increasing in r AND
placebo near 0.

HOW TO READ THE OUTPUT
----------------------
Console prints per-checkpoint rho for all arms plus the paired V-minus-assets
contrast with CI95, and the verdict.
stat/judge/predictive.csv: one row per (game, checkpoint, player) -- the
    analysis input, also reused by any later re-analysis.
stat/judge/validation_results.csv: one row per reported number, keyed
    analysis=预测力 / 安慰剂.

This script replays games but does NOT score decisions: the cost is ~2 s/game,
independent of evaluate.py's per-decision tables.

Usage from the repository root:
    .venv/Scripts/python.exe stat/judge/predictive.py [--games N] [--placebo-games N]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import value as value_module
from replay_tools import iter_decision_points

from monopoly_agent_battle.game.board_data.classic_us_40 import BOARD_BY_POSITION

ROOT = Path(__file__).resolve().parent.parent.parent
RUNS = ROOT / "runs"
DATA_DIR = Path(__file__).resolve().parent / "data"
OUTPUT_DIR = Path(__file__).resolve().parent

FLOOR_EXPERIMENTS = ("sane_random", "greedy_script", "greedy_vs_sane_random")
CHECKPOINT_ROUNDS = tuple(range(5, 51, 5))
BOOT_RESAMPLES = 10_000
RNG_SEED = 20260924


def _terminal_net_worths(directory: Path) -> dict[str, int]:
    """Terminal net worth per player from result.json."""
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    properties = result["properties"]
    worths: dict[str, int] = {}
    for player_id, player in result["players"].items():
        total = int(player["cash"])
        for position in player["properties"]:
            space = BOARD_BY_POSITION[int(position)]
            state = properties[str(position)]
            total += space.price or 0
            total += (space.building_cost or 0) * int(state["building_level"])
            if state["mortgaged"]:
                total -= space.price or 0
        worths[player_id] = total
    return worths


def game_checkpoints(directory: Path) -> list[tuple[str, int, str, float, float]]:
    """Replay one game, returning (game, round, player_id, V, M_assets) rows."""
    worths = _terminal_net_worths(directory)
    rows: list[tuple[str, int, str, float, float]] = []
    seen_rounds: set[int] = set()
    for point in iter_decision_points(directory):
        complete = point.complete_rounds
        if complete not in CHECKPOINT_ROUNDS or complete in seen_rounds:
            continue
        seen_rounds.add(complete)
        state = point.engine.state
        for player_id, player in state.players.items():
            if player.bankrupt:
                continue
            breakdown = value_module.evaluate(state, player_id)
            rows.append(
                (
                    directory.name,
                    complete,
                    player_id,
                    breakdown.total,
                    float(breakdown.m_assets),
                )
            )
        if len(seen_rounds) == len(CHECKPOINT_ROUNDS):
            break
    return [(name, r, pid, v, m, float(worths.get(pid, 0))) for name, r, pid, v, m in rows]


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rho via average ranks (ties averaged), numpy-only."""
    if len(x) < 3:
        return float("nan")
    rx = _average_ranks(x)
    ry = _average_ranks(y)
    sx = rx - rx.mean()
    sy = ry - ry.mean()
    denom = float(np.sqrt(sx @ sx) * np.sqrt(sy @ sy))
    return float(sx @ sy / denom) if denom > 0 else float("nan")


def _average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values))
    sorted_values = values[order]
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and sorted_values[end] == sorted_values[start]:
            end += 1
        ranks[order[start:end]] = (start + end - 1) / 2.0 + 1.0
        start = end
    return ranks


def collect(
    experiments: tuple[str, ...],
    games_per_experiment: int | None,
    skip: int = 0,
    shard: tuple[int, int] | None = None,
) -> list[tuple[str, int, str, float, float, float]]:
    """Replay the requested floor games and gather checkpoint rows.

    ``skip``/``shard`` mirror evaluate.py: shards write partial row files and
    are combined with ``--combine N`` before analysis.
    """
    rows: list[tuple[str, int, str, float, float, float]] = []
    for experiment in experiments:
        root = RUNS / experiment
        directories = sorted(
            d for d in root.iterdir() if d.is_dir() and (d / "events.jsonl").exists()
        )
        if skip:
            directories = directories[skip:]
        if games_per_experiment is not None:
            directories = directories[:games_per_experiment]
        if shard is not None:
            shard_index, shard_count = shard
            directories = [
                d for position, d in enumerate(directories) if position % shard_count == shard_index
            ]
        for done, directory in enumerate(directories, start=1):
            rows.extend(game_checkpoints(directory))
            if done % 100 == 0 or done == len(directories):
                print(f"  {experiment}: {done}/{len(directories)} games", flush=True)
    return rows


def write_rows(rows: list[tuple], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["game", "checkpoint_round", "player_id", "v_total", "v_m_assets", "terminal_net_worth"]
        )
        writer.writerows(rows)


def read_rows(path: Path) -> list[tuple[str, int, str, float, float, float]]:
    with path.open(encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        return [
            (game, int(round_), player, float(v), float(m), float(w))
            for game, round_, player, v, m, w in reader
        ]


def evaluate_arms(
    rows: list[tuple[str, int, str, float, float, float]],
    *,
    placebo: bool,
    seed: int,
) -> dict[int, dict[str, float]]:
    """Per-checkpoint Spearman for V and for M_assets, optionally placebo-shuffled."""
    rng = np.random.default_rng(seed)
    games = np.array([row[0] for row in rows])
    players = np.array([row[2] for row in rows])
    rounds = np.array([row[1] for row in rows])
    v = np.array([row[3] for row in rows])
    m = np.array([row[4] for row in rows])
    worth = np.array([row[5] for row in rows])
    if placebo:
        # Shuffle terminal worth BETWEEN players of different games (section
        # 6.6): every player keeps their own V but is labelled with another
        # player's terminal worth. rho must collapse to 0.
        identity = np.char.add(np.char.add(games, "|"), players)
        unique_players, first = np.unique(identity, return_index=True)
        player_worths = worth[first]
        permuted = rng.permutation(player_worths)
        worth_of = dict(zip(unique_players, permuted, strict=True))
        worth = np.array([worth_of[key] for key in identity])
    out: dict[int, dict[str, float]] = {}
    for checkpoint in CHECKPOINT_ROUNDS:
        mask = rounds == checkpoint
        if mask.sum() < 10:
            continue
        out[checkpoint] = {
            "v": _spearman(v[mask], worth[mask]),
            "assets": _spearman(m[mask], worth[mask]),
        }
    return out


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    sx = x - x.mean()
    sy = y - y.mean()
    denom = float(np.sqrt(sx @ sx) * np.sqrt(sy @ sy))
    return float(sx @ sy / denom) if denom > 0 else float("nan")


def _paired_contrast(
    rows: list[tuple[str, int, str, float, float, float]], resamples: int
) -> tuple[float, float, float]:
    """Game-cluster bootstrap of mean_r rho_V(r) - rho_assets(r).

    Speed note: ranks are computed ONCE per checkpoint on the full sample;
    bootstrap resamples then correlate subsets of those fixed ranks instead of
    re-ranking 91k rows 10,000 times (the naive version takes hours in pure
    Python). The point estimate is the exact Spearman contrast; the bootstrap
    distribution uses the standard fixed-rank approximation.
    """
    games_arr = np.array([row[0] for row in rows])
    rounds = np.array([row[1] for row in rows])
    v = np.array([row[3] for row in rows])
    m = np.array([row[4] for row in rows])
    w = np.array([row[5] for row in rows])

    checkpoints: list[int] = []
    rank_v: list[np.ndarray] = []
    rank_m: list[np.ndarray] = []
    rank_w: list[np.ndarray] = []
    per_game_indices: list[dict[str, np.ndarray]] = []
    for checkpoint in CHECKPOINT_ROUNDS:
        mask = np.where(rounds == checkpoint)[0]
        if len(mask) < 10:
            continue
        checkpoints.append(checkpoint)
        rank_v.append(_average_ranks(v[mask]))
        rank_m.append(_average_ranks(m[mask]))
        rank_w.append(_average_ranks(w[mask]))
        game_groups: dict[str, list[int]] = {}
        for position, game in enumerate(games_arr[mask]):
            game_groups.setdefault(game, []).append(position)
        per_game_indices.append(
            {game: np.array(positions) for game, positions in game_groups.items()}
        )

    games = sorted({row[0] for row in rows})

    def contrast(sample: list[str]) -> float:
        diffs = []
        for c_index in range(len(checkpoints)):
            groups = per_game_indices[c_index]
            idx = np.concatenate([groups[game] for game in sample if game in groups])
            if len(idx) < 10:
                continue
            diffs.append(
                _pearson(rank_v[c_index][idx], rank_w[c_index][idx])
                - _pearson(rank_m[c_index][idx], rank_w[c_index][idx])
            )
        return float(np.mean(diffs)) if diffs else float("nan")

    point = contrast(games)
    rng = np.random.default_rng(RNG_SEED)
    boot = [
        contrast([games[i] for i in rng.integers(0, len(games), size=len(games))])
        for _ in range(resamples)
    ]
    boot = [b for b in boot if not np.isnan(b)]
    return point, float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))


def analyze(rows: list[tuple[str, int, str, float, float, float]]) -> int:
    """Run the three arms, the paired contrast and the preregistered verdict."""
    print(f"{len(rows)} (game, checkpoint, player) rows")
    main_arm = evaluate_arms(rows, placebo=False, seed=RNG_SEED)
    placebo_arm = evaluate_arms(rows, placebo=True, seed=RNG_SEED + 1)

    print("\ncheckpoint | rho_V | rho_assets | placebo_V")
    increasing = True
    v_values = [main_arm[c]["v"] for c in sorted(main_arm)]
    for index in range(len(v_values) - 1):
        if v_values[index + 1] < v_values[index] - 0.02:
            increasing = False
    for checkpoint in sorted(main_arm):
        placebo_v = placebo_arm.get(checkpoint, {}).get("v", float("nan"))
        print(
            f"  r={checkpoint:3d}   | {main_arm[checkpoint]['v']:+.3f} | "
            f"{main_arm[checkpoint]['assets']:+.3f} | {placebo_v:+.3f}"
        )

    print("\n=== paired contrast: mean_r (rho_V - rho_assets) ===", flush=True)
    point, low, high = _paired_contrast(rows, BOOT_RESAMPLES)
    print(f"contrast = {point:+.4f}  CI95=[{low:+.4f}, {high:+.4f}]")

    placebo_max = (
        max(abs(arm["v"]) for arm in placebo_arm.values()) if placebo_arm else float("nan")
    )
    placebo_ok = bool(np.isfinite(placebo_max) and placebo_max < 0.1)
    verdict = "PASS" if (low > 0 and increasing and placebo_ok) else "FAIL"
    print(f"\nrho increasing in r: {increasing}; placebo |rho|max = {placebo_max:.3f}")
    print(f"VERDICT (section 6.6 test 1): {verdict}")

    write_rows(rows, OUTPUT_DIR / "predictive.csv")

    validation_path = OUTPUT_DIR / "validation_results.csv"
    write_header = not validation_path.exists()
    with validation_path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        if write_header:
            writer.writerow(
                ["analysis", "arm_or_scope", "checkpoint", "estimate", "ci_low", "ci_high", "n"]
            )
        for checkpoint in sorted(main_arm):
            writer.writerow(["预测力", "V", checkpoint, main_arm[checkpoint]["v"], "", "", ""])
            writer.writerow(
                ["预测力", "M_assets", checkpoint, main_arm[checkpoint]["assets"], "", "", ""]
            )
            writer.writerow(
                ["安慰剂", "V-shuffled", checkpoint, placebo_arm[checkpoint]["v"], "", "", ""]
            )
        writer.writerow(["预测力", "V-minus-assets", "mean", point, low, high, len(rows)])
    print(f"wrote {OUTPUT_DIR / 'predictive.csv'} and appended validation_results.csv")
    return 0 if verdict == "PASS" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=None, help="cap per experiment (dev)")
    parser.add_argument("--skip", type=int, default=0, help="skip first N games per experiment")
    parser.add_argument(
        "--shard", type=str, default=None, metavar="K/N", help="collect shard K of N"
    )
    parser.add_argument(
        "--combine",
        type=int,
        default=None,
        metavar="N",
        help="combine checkpoint-row shards 0..N-1 and analyze",
    )
    args = parser.parse_args()

    if args.combine is not None:
        rows: list[tuple[str, int, str, float, float, float]] = []
        for shard_index in range(args.combine):
            path = DATA_DIR / f"predictive_rows__shard{shard_index}of{args.combine}.csv"
            if not path.exists():
                print(f"missing shard rows: {path}")
                return 1
            rows.extend(read_rows(path))
        print(f"combined {args.combine} shards -> {len(rows)} rows")
        return analyze(rows)

    shard: tuple[int, int] | None = None
    if args.shard is not None:
        try:
            shard_index, shard_count = (int(part) for part in args.shard.split("/"))
        except ValueError:
            parser.error("--shard must look like 0/4")
        if not (0 <= shard_index < shard_count):
            parser.error("--shard requires 0 <= K < N")
        shard = (shard_index, shard_count)

    print("=== collecting checkpoints (floor games only) ===", flush=True)
    rows = collect(FLOOR_EXPERIMENTS, args.games, skip=args.skip, shard=shard)
    if shard is not None:
        path = DATA_DIR / f"predictive_rows__shard{shard[0]}of{shard[1]}.csv"
        write_rows(rows, path)
        print(f"wrote {path} -- {len(rows)} rows (combine with --combine {shard[1]})")
        return 0
    return analyze(rows)


if __name__ == "__main__":
    raise SystemExit(main())
