"""Experiment 2 entry: floor-calibrated null test for runs/court-vs-baseline.

Loads the 64 court-vs-baseline games (商/秦/唐/明 × 16, seeds 101-164), runs the
shared floor test (floor_test.py) per court and pooled, and exports
stat/cvb_analysis.csv. Method and interpretation guide: see floor_test.py.
Deprecated/superseded games live in runs/court-vs-baseline/deprecate/ and are
excluded by directory filtering.

Run from the repository root:
    .venv/Scripts/python.exe stat/cvb_analysis.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from floor_test import load_floors, load_game, report, write_csv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CVB_DIR = ROOT / "runs" / "court-vs-baseline"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "stat" / "cvb_analysis.csv",
        help="CSV output path (default: stat/cvb_analysis.csv)",
    )
    args = parser.parse_args()

    games = [
        load_game(gdir)
        for gdir in sorted(CVB_DIR.iterdir())
        if gdir.is_dir() and gdir.name != "deprecate" and (gdir / "result.json").exists()
    ]
    valid = [g for g in games if g["validity"] == "valid"]
    print(f"loaded {len(games)} games, valid={len(valid)}")

    floors = load_floors()
    rows: list[dict[str, object]] = []
    for group in ("shang", "qin", "tang", "ming"):
        gs = [g for g in valid if g["group"] == group]
        report(group, [g["seat"] for g in gs], sum(g["points"] for g in gs), floors, rows)
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
