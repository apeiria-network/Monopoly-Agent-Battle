"""Seat-calibrated significance test: does flat_ensemble (FE) beat baselines?

Same method as cvb_analysis.py (Courts-Battle-config-details.md §8.5) applied to
runs/fe-vs-baseline (experiment 3, FE01-FE16, seeds 101-116 mirroring the first 16
court-vs-baseline games). The 4 "*.invalid" directories are superseded failed
attempts and are excluded; the 16 canonical games are all valid. Exports an
English CSV.

Run from the repository root:
    .venv/Scripts/python.exe stat/fe_advantage_test.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cvb_analysis import load_floors, load_game, report, write_csv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FE_DIR = ROOT / "runs" / "fe-vs-baseline"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "stat" / "fe_advantage_test.csv",
        help="CSV output path (default: stat/fe_advantage_test.csv)",
    )
    args = parser.parse_args()

    games: list[dict[str, Any]] = []
    for gdir in sorted(FE_DIR.iterdir()):
        if not gdir.is_dir() or gdir.name.endswith(".invalid"):
            continue
        games.append(load_game(gdir))
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
