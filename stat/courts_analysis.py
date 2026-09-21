"""Experiment 1 entry: floor-calibrated null test for runs/4-courts-battle (混战).

Loads the 16 melee games (game-001..016, seeds 101-116, 商/秦/唐/明 rotating seats),
extracts all four courts' seats and points per game, and runs the shared floor test
(floor_test.py) per court. Each court covers every seat exactly 4 times, so the
calibrated expectation is exactly 24.00 per court. Method and interpretation guide:
see floor_test.py. Exports stat/courts_analysis.csv.

Caveats: the four per-court tests are NOT independent — within a game the four
courts' points sum to 6 by construction, so one court's overperformance is another's
underperformance. A pooled "all_courts" row is therefore meaningless (it is always
exactly 96 = expectation) and intentionally omitted. The floor environment (4 floor
players) also differs from the melee environment (4 courts); read results as "does
this court beat a luck player dropped into its seat", not as court-vs-court evidence
(for that, see pl_strength.py). Deprecated games live in runs/4-courts-battle/
deprecate/ and "*.bak*" siblings and are excluded.

Run from the repository root:
    .venv/Scripts/python.exe stat/courts_analysis.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from floor_test import POINTS, load_floors, report, write_csv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MELEE_DIR = ROOT / "runs" / "4-courts-battle"


def load_melee_game(gdir: Path) -> list[dict[str, Any]]:
    """One row per court: group, seat, points, validity."""
    result: dict[str, Any] = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
    config: dict[str, Any] = json.loads((gdir / "config.json").read_text(encoding="utf-8"))[
        "config"
    ]
    seat_of = {p["player_id"]: p["seat"] for p in config["players"]}
    return [
        {
            "game_id": gdir.name,
            "group": str(pid).split("-")[0],
            "seat": seat_of[pid],
            "points": POINTS[i],
            "validity": result["validity_status"],
        }
        for i, pid in enumerate(result["rankings"])
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "stat" / "courts_analysis.csv",
        help="CSV output path (default: stat/courts_analysis.csv)",
    )
    args = parser.parse_args()

    rows_in: list[dict[str, Any]] = []
    for gdir in sorted(MELEE_DIR.iterdir()):
        if not gdir.is_dir() or not gdir.name.startswith("game-") or "." in gdir.name:
            continue
        if (gdir / "result.json").exists():
            rows_in.extend(load_melee_game(gdir))
    valid = [r for r in rows_in if r["validity"] == "valid"]
    print(f"loaded {len(rows_in) // 4} games, valid court-rows={len(valid)}")

    floors = load_floors()
    rows: list[dict[str, object]] = []
    for group in ("shang", "qin", "tang", "ming"):
        gs = [r for r in valid if r["group"] == group]
        report(group, [r["seat"] for r in gs], sum(r["points"] for r in gs), floors, rows)
    write_csv(rows, args.csv)


if __name__ == "__main__":
    main()
