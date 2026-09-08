"""Auto-generated curve plots for finished runs, drawn from llm_digest.csv.

``write_run_curves`` writes ``cash_by_round.png`` and
``net_worth_and_cash_by_round.png`` next to the digest: per-player cash lines,
plus net worth (solid) and cash (dashed) rendered in the same colour per
player. The charts match the manual ``stat/plot_cash.py`` and
``stat/plot_net_worth_and_cash.py`` renderings. matplotlib is imported lazily;
environments without it get ``PlotGenerationError`` so callers can treat
plotting as best-effort and never fail a finished run over a chart.
"""

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
from __future__ import annotations

import csv
from collections import OrderedDict
from pathlib import Path
from typing import Any

_ROUND = "轮次"
_PLAYER = "玩家"
_NET_WORTH = "当前玩家净资产"
_CASH = "当前玩家现金持有量"

Series = OrderedDict[str, OrderedDict[int, int]]


class PlotGenerationError(RuntimeError):
    """Raised when curve plots cannot be generated for a run."""


def write_run_curves(run_directory: Path) -> list[Path]:
    """Write the run's curve charts next to its llm_digest.csv.

    Returns the written PNG paths; an empty list means the run has no digest.
    Raises ``PlotGenerationError`` when matplotlib is missing or the digest
    carries no usable series.
    """
    csv_path = run_directory / "llm_digest.csv"
    if not csv_path.exists():
        return []
    figure_cls = _figure_class()
    net, cash = read_series(csv_path)
    rounds = sorted({item for values in (*net.values(), *cash.values()) for item in values})
    outputs: list[Path] = []
    if cash:
        outputs.append(_plot_cash(csv_path, rounds, cash, figure_cls))
    if net and cash:
        outputs.append(_plot_net_worth_and_cash(csv_path, rounds, net, cash, figure_cls))
    if not outputs:
        raise PlotGenerationError(f"no usable series in {csv_path}")
    return outputs


def _figure_class() -> Any:
    try:
        from matplotlib.figure import Figure
    except ImportError as error:
        raise PlotGenerationError("matplotlib is not installed") from error
    return Figure


def read_series(csv_path: Path) -> tuple[Series, Series]:
    """Return (net worth series, cash series); each maps player -> round -> last value."""
    net: Series = OrderedDict()
    cash: Series = OrderedDict()
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            player = (row.get(_PLAYER) or "").strip()
            if not player:
                continue
            try:
                round_number = int((row.get(_ROUND) or "").strip())
            except ValueError:
                continue
            _store_last(net, player, round_number, row.get(_NET_WORTH))
            _store_last(cash, player, round_number, row.get(_CASH))
    return net, cash


def _store_last(series: Series, player: str, round_number: int, raw: str | None) -> None:
    try:
        series.setdefault(player, OrderedDict())[round_number] = int((raw or "").strip())
    except ValueError:
        return


def _build_xy(rounds: list[int], values: OrderedDict[int, int]) -> tuple[list[int], list[float]]:
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


def _configure_axis(axis: Any, rounds: list[int]) -> None:
    if len(rounds) <= 60:
        axis.set_xticks(range(rounds[0], rounds[-1] + 1))
    else:
        step = max(1, len(rounds) // 50)
        axis.set_xticks(rounds[::step])
    axis.grid(True, alpha=0.3)


def _plot_cash(csv_path: Path, rounds: list[int], cash: Series, figure_cls: Any) -> Path:
    figure = figure_cls(figsize=(16, 6))
    axis = figure.subplots()
    for player, values in cash.items():
        x, y = _build_xy(rounds, values)
        if x:
            axis.plot(x, y, marker="o", markersize=3, linewidth=1.6, label=player)
    axis.set_title(f"Cash by Round — {csv_path.parent.name}")
    axis.set_xlabel("Round (complete_rounds)")
    axis.set_ylabel("Cash")
    _configure_axis(axis, rounds)
    axis.legend(title="Player")
    figure.tight_layout()
    output = csv_path.parent / "cash_by_round.png"
    figure.savefig(output, dpi=150)
    return output


def _plot_net_worth_and_cash(
    csv_path: Path, rounds: list[int], net: Series, cash: Series, figure_cls: Any
) -> Path:
    figure = figure_cls(figsize=(16, 6))
    axis = figure.subplots()
    players = list(dict.fromkeys([*net, *cash]))
    for player in players:
        color = None
        x, y = _build_xy(rounds, net.get(player) or OrderedDict())
        if x:
            lines = axis.plot(
                x, y, marker="o", markersize=3, linewidth=1.6, label=f"{player} net worth"
            )
            color = lines[0].get_color()
        x, y = _build_xy(rounds, cash.get(player) or OrderedDict())
        if x:
            axis.plot(x, y, linestyle="--", linewidth=1.4, label=f"{player} cash", color=color)
    axis.set_title(f"Net Worth vs Cash by Round — {csv_path.parent.name}")
    axis.set_xlabel("Round (complete_rounds)")
    axis.set_ylabel("Net Worth / Cash")
    _configure_axis(axis, rounds)
    axis.legend(title="Player (metric)", ncol=2)
    figure.tight_layout()
    output = csv_path.parent / "net_worth_and_cash_by_round.png"
    figure.savefig(output, dpi=150)
    return output
