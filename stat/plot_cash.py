"""Plot per-round cash lines for every player from a run's llm_digest.csv.

Usage:
    python stat/plot_cash.py <run_dir_or_csv> [-o OUTPUT.png] [--show]

X axis is the digest's 轮次 column (complete_rounds, 0 = first round in
progress). For every (round, player) pair the LAST row of that player in that
round is used — the player's latest recorded cash, the closest the digest
gets to "cash at end of round". A player who stopped appearing (line ends)
simply has no further data; intermediate missing rounds are forward-filled
so the line stays connected.
"""

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
# (matplotlib's own annotations carry Unknown kwargs under strict mode)

from __future__ import annotations

import argparse
import csv
import sys
from collections import OrderedDict
from pathlib import Path

_ROUND = "轮次"
_PLAYER = "玩家"
_CASH = "当前玩家现金持有量"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot per-round cash lines for every player from llm_digest.csv."
    )
    parser.add_argument(
        "run", help="run directory (containing llm_digest.csv) or the csv path itself"
    )
    parser.add_argument(
        "-o", "--output", help="output PNG path (default: <csv dir>/cash_by_round.png)"
    )
    parser.add_argument("--show", action="store_true", help="also open an interactive window")
    return parser.parse_args(argv)


def resolve_csv(run: str) -> Path:
    path = Path(run)
    if path.is_dir():
        return path / "llm_digest.csv"
    if path.is_file():
        return path
    raise SystemExit(f"input not found: {run}")


def read_series(csv_path: Path) -> OrderedDict[str, OrderedDict[int, int]]:
    """Return player -> (round -> last cash of that player in that round)."""
    if not csv_path.exists():
        raise SystemExit(f"llm_digest.csv not found: {csv_path}")
    series: OrderedDict[str, OrderedDict[int, int]] = OrderedDict()
    skipped_round = 0
    skipped_value = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            player = (row.get(_PLAYER) or "").strip()
            round_text = (row.get(_ROUND) or "").strip()
            cash_text = (row.get(_CASH) or "").strip()
            if not player:
                continue
            try:
                round_number = int(round_text)
            except ValueError:
                skipped_round += 1
                continue
            try:
                cash = int(cash_text)
            except ValueError:
                skipped_value += 1
                continue
            series.setdefault(player, OrderedDict())[round_number] = cash
    if skipped_round or skipped_value:
        print(
            f"skipped rows: {skipped_round} bad round, {skipped_value} empty cash",
            file=sys.stderr,
        )
    if not series:
        raise SystemExit(f"no usable rows in {csv_path}")
    return series


def build_xy(rounds: list[int], values: OrderedDict[int, int]) -> tuple[list[int], list[float]]:
    """Return (x, y) with intermediate gaps forward-filled; trailing gaps end the line."""
    x: list[int] = []
    y: list[float] = []
    last: float | None = None
    for round_number in rounds:
        if round_number in values:
            last = float(values[round_number])
        if last is None:
            continue
        x.append(round_number)
        y.append(last)
    return x, y


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    csv_path = resolve_csv(args.run)
    series = read_series(csv_path)
    rounds = sorted({round_number for values in series.values() for round_number in values})

    try:
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise SystemExit(
            "matplotlib is required: .venv\\Scripts\\python.exe -m pip install matplotlib"
        ) from error

    figure, axis = plt.subplots(figsize=(16, 6))
    for player, values in series.items():
        x, y = build_xy(rounds, values)
        if not x:
            continue
        axis.plot(x, y, marker="o", markersize=3, linewidth=1.6, label=player)

    axis.set_title(f"Cash by Round — {csv_path.parent.name}")
    axis.set_xlabel("Round (complete_rounds)")
    axis.set_ylabel("Cash")
    if len(rounds) <= 60:
        axis.set_xticks(range(rounds[0], rounds[-1] + 1))
    else:
        step = max(1, len(rounds) // 50)
        axis.set_xticks(rounds[::step])
    axis.grid(True, alpha=0.3)
    axis.legend(title="Player")
    figure.tight_layout()

    output = Path(args.output) if args.output else csv_path.parent / "cash_by_round.png"
    figure.savefig(output, dpi=150)
    print(output)
    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
