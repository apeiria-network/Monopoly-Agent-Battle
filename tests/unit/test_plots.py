from __future__ import annotations

from pathlib import Path

import pytest

from monopoly_agent_battle.reporting.plots import (
    PlotGenerationError,
    read_series,
    write_run_curves,
)


def _write_digest(run: Path, header: str, rows: list[str]) -> None:
    (run / "llm_digest.csv").write_text(header + "".join(rows), encoding="utf-8-sig", newline="\n")


_FULL_HEADER = "轮次,玩家,当前玩家净资产,当前玩家现金持有量\n"


def test_write_run_curves_generates_both_charts(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write_digest(
        run,
        _FULL_HEADER,
        [
            "0,a,1520,20\n",
            "0,b,1500,500\n",
            "1,a,1440,40\n",
            "1,b,1560,600\n",
        ],
    )

    outputs = write_run_curves(run)

    assert [path.name for path in outputs] == [
        "cash_by_round.png",
        "net_worth_and_cash_by_round.png",
    ]
    for path in outputs:
        assert path.exists()
        assert path.stat().st_size > 0


def test_write_run_curves_returns_empty_without_digest(tmp_path: Path) -> None:
    assert write_run_curves(tmp_path) == []


def test_write_run_curves_rejects_digest_without_usable_rows(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write_digest(run, _FULL_HEADER, [])

    with pytest.raises(PlotGenerationError, match="no usable series"):
        write_run_curves(run)


def test_write_run_curves_skips_combined_chart_without_net_worth(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write_digest(
        run,
        "轮次,玩家,当前玩家现金持有量\n",
        [
            "0,a,20\n",
            "1,a,40\n",
        ],
    )

    outputs = write_run_curves(run)

    assert [path.name for path in outputs] == ["cash_by_round.png"]
    assert outputs[0].stat().st_size > 0


def test_write_run_curves_keeps_last_value_of_a_round(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _write_digest(
        run,
        _FULL_HEADER,
        [
            "0,a,1500,100\n",
            "0,a,1520,120\n",
        ],
    )

    net, cash = read_series(run / "llm_digest.csv")

    assert net["a"][0] == 1520
    assert cash["a"][0] == 120
