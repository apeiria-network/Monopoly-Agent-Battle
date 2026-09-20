"""Experiment 3 entry: floor-calibrated null test for runs/fe-vs-baseline.

Loads the 16 flat-ensemble games (FE01-FE16, seeds 101-116 mirroring the first 16
court-vs-baseline games), runs the shared floor test (floor_test.py) on fe_all and
per base model, and exports stat/fe_analysis.csv. Method and interpretation guide:
see floor_test.py. Read fe_all first — the fe_base_* rows have only 4 games each
(detection threshold ±4 points) and are barely informative. Superseded failed
attempts live in runs/fe-vs-baseline/deprecate/ and are excluded.

Run from the repository root:
    .venv/Scripts/python.exe stat/fe_analysis.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from floor_test import load_floors, load_game, report, write_csv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FE_DIR = ROOT / "runs" / "fe-vs-baseline"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "stat" / "fe_analysis.csv",
        help="CSV output path (default: stat/fe_analysis.csv)",
    )
    args = parser.parse_args()

    games = [
        load_game(gdir)
        for gdir in sorted(FE_DIR.iterdir())
        if gdir.is_dir() and gdir.name != "deprecate" and (gdir / "result.json").exists()
    ]
    valid = [g for g in games if g["validity"] == "valid"]
    print(f"loaded {len(games)} FE games, valid={len(valid)}")

    floors = load_floors()
    rows: list[dict[str, object]] = []
    report(
        "fe_all",
        [g["seat"] for g in valid],
        sum(g["points"] for g in valid),
        floors,
        rows,
    )

    by_model: dict[str, list[dict[str, Any]]] = {}
    for g in valid:
        by_model.setdefault(g["base_model"], []).append(g)
    for model in sorted(by_model):
        gs = by_model[model]
        report(
            f"fe_base_{model}",
            [g["seat"] for g in gs],
            sum(g["points"] for g in gs),
            floors,
            rows,
        )
    write_csv(rows, args.csv)


if __name__ == "__main__":
    main()
