"""Seat-calibrated significance test: does flat_ensemble (FE) beat baselines?

Same method as court_advantage_test.py (Courts-Battle-config-details.md §8.5) applied
to runs/fe-vs-baseline (experiment 3, FE01-FE16, seeds 101-116 mirroring the first 16
court-vs-baseline games). The 4 "*.invalid" directories are superseded failed attempts
and are excluded; the 16 canonical games are all valid. Exports an English CSV.

Run from the repository root:
    .venv/Scripts/python.exe stat/fe_advantage_test.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from court_advantage_test import report, seat_moments  # noqa: E402
from cvb_analysis import load_floor, load_game  # noqa: E402

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
    for g in valid:
        print(
            f"{g['game_id']} seat{g['court_seat']} base={g['emperor_model']} "
            f"rank={g['court_rank']} pts={g['court_points']} nw={g['court_net_worth']} "
            f"surv={g['court_survived']} calls={g['llm_calls']} fb={g['llm_fallbacks']}"
        )

    sane_seats, _ = load_floor(ROOT / "runs" / "sane_random")
    greedy_seats, _ = load_floor(ROOT / "runs" / "greedy_script")
    floors = {
        "sane_random": (*seat_moments(sane_seats), sane_seats),
        "greedy_script": (*seat_moments(greedy_seats), greedy_seats),
    }

    rows: list[dict[str, object]] = []
    seats = [g["court_seat"] for g in valid]
    total = sum(g["court_points"] for g in valid)
    report("fe_all", seats, total, floors, rows)

    by_model: dict[str, list[dict[str, Any]]] = {}
    for g in valid:
        by_model.setdefault(g["emperor_model"], []).append(g)
    for model in sorted(by_model):
        gs = by_model[model]
        report(
            f"fe_base_{model}",
            [g["court_seat"] for g in gs],
            sum(g["court_points"] for g in gs),
            floors,
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
