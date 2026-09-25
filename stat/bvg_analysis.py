"""Baseline-vs-greedy (single llm_baseline vs 3 greedy_script) floor-calibrated test.

Design: Courts-Battle-config-details.md section 7.3.2, "experiment 9B" ("can a
single LLM beat 3 hard-coded greedy_script opponents"). 16 games, seeds
101-116, one baseline seat + 3 greedy_script seats per game, every
(seat, model) combination of the 4x4 Latin square covered exactly once (see
configs/experiments/baseline-vs-greedy/generate_configs.py).

Null: the section-8 greedy_script 800-game floor's per-seat point
distribution -- "focus player vs 3 equally-strong greedy_script opponents".
The sane_random floor is reported alongside only as a robustness cross-check
(it does NOT match this game's actual opponent composition, so it is not the
primary judgment floor); per section 8.5.2 the primary floor for this
experiment is greedy_script specifically, since that is what the baseline
actually played against.

Run from the repository root:
    .venv/Scripts/python.exe stat/bvg_analysis.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from floor_test import load_floors, report, write_csv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BVG_DIR = ROOT / "runs" / "baseline-vs-greedy"
POINTS = (3, 2, 1, 0)


def load_bvg_game(gdir: Path) -> dict[str, Any]:
    """Per-game baseline seat, rank, points, and model letter."""
    result: dict[str, Any] = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
    config: dict[str, Any] = json.loads((gdir / "config.json").read_text(encoding="utf-8"))[
        "config"
    ]
    players = {p["player_id"]: p for p in config["players"]}
    baseline = players["baseline"]
    model_name = config["model_profiles"][baseline["model_profile"]]["model"]
    rank_of = {pid: i + 1 for i, pid in enumerate(result["rankings"])}
    return {
        "game_id": gdir.name,
        "seat": baseline["seat"],
        "rank": rank_of["baseline"],
        "points": POINTS[rank_of["baseline"] - 1],
        "validity": result["validity_status"],
        "model": model_name,
    }


def main() -> None:
    games = [
        load_bvg_game(gdir)
        for gdir in sorted(BVG_DIR.iterdir())
        if gdir.is_dir() and (gdir / "result.json").exists()
    ]
    valid = [g for g in games if g["validity"] == "valid"]
    print(f"loaded {len(games)} games, valid={len(valid)}")
    for g in valid:
        print(
            f"  {g['game_id']}: seat={g['seat']} model={g['model']} "
            f"rank={g['rank']} points={g['points']}"
        )

    floors = load_floors()
    rows: list[dict[str, object]] = []
    total = sum(g["points"] for g in valid)
    seats = [g["seat"] for g in valid]
    report("baseline_vs_greedy", seats, total, floors, rows)

    # Per-model breakdown (observational, not separately powered -- 4 games each)
    print("\n--- per-model breakdown (n=4 each, observational only) ---")
    for letter_model in sorted({g["model"] for g in valid}):
        gs = [g for g in valid if g["model"] == letter_model]
        pts = sum(g["points"] for g in gs)
        print(f"  {letter_model}: n={len(gs)} total_points={pts} mean={pts/len(gs):.2f}")

    write_csv(rows, ROOT / "stat" / "bvg_analysis.csv")


if __name__ == "__main__":
    main()
