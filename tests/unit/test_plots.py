from __future__ import annotations

import json
from pathlib import Path

import pytest

from monopoly_agent_battle.reporting.plots import (
    PlotGenerationError,
    _apply_bankruptcy_zeros,
    _bankruptcy_rounds,
    read_series,
    write_run_curves,
)


def _write_digest(run: Path, header: str, rows: list[str]) -> None:
    (run / "llm_digest.csv").write_text(header + "".join(rows), encoding="utf-8-sig", newline="\n")


_FULL_HEADER = "轮次,玩家,当前玩家净资产,当前玩家现金持有量\n"


def _write_run_artifacts(
    run: Path,
    players: list[tuple[str, int]],
    events: list[tuple[str, dict[str, object]]],
) -> None:
    run.mkdir(parents=True, exist_ok=True)
    (run / "config.json").write_text(
        json.dumps({"config": {"players": [{"player_id": p, "seat": s} for p, s in players]}}),
        encoding="utf-8",
    )
    lines = [
        json.dumps({"event_id": i, "event_type": kind, "payload": payload})
        for i, (kind, payload) in enumerate(events, 1)
    ]
    (run / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def test_bankruptcy_rounds_count_complete_rounds_by_seat_wrap(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_run_artifacts(
        run,
        players=[("a", 1), ("b", 2), ("c", 3)],
        events=[
            ("turn_started", {"player_id": "a"}),
            ("turn_started", {"player_id": "b"}),
            ("turn_started", {"player_id": "c"}),
            ("turn_started", {"player_id": "a"}),
            ("turn_started", {"player_id": "b"}),
            ("player_bankrupt", {"player_id": "b"}),
            ("turn_started", {"player_id": "c"}),
            ("turn_started", {"player_id": "a"}),
            ("turn_started", {"player_id": "c"}),
        ],
    )

    assert _bankruptcy_rounds(run) == {"b": 1}


def test_bankruptcy_rounds_returns_empty_without_artifacts(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()

    assert _bankruptcy_rounds(run) == {}


def test_bankruptcy_zeros_force_series_to_zero_from_bankruptcy_round(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_run_artifacts(
        run,
        players=[("a", 1), ("b", 2), ("c", 3)],
        events=[
            ("turn_started", {"player_id": "a"}),
            ("turn_started", {"player_id": "b"}),
            ("turn_started", {"player_id": "c"}),
            ("turn_started", {"player_id": "a"}),
            ("turn_started", {"player_id": "b"}),
            ("player_bankrupt", {"player_id": "b"}),
            ("turn_started", {"player_id": "c"}),
            ("turn_started", {"player_id": "a"}),
            ("turn_started", {"player_id": "c"}),
        ],
    )
    _write_digest(
        run,
        _FULL_HEADER,
        [
            "0,a,1500,100\n",
            "0,b,800,50\n",
            "0,c,1600,300\n",
            "1,a,1550,120\n",
            "1,b,850,40\n",
            "1,c,1700,400\n",
            "2,a,1600,140\n",
            "2,c,1750,450\n",
            "3,a,1650,160\n",
            "3,c,1800,500\n",
        ],
    )
    net, cash = read_series(run / "llm_digest.csv")
    rounds = sorted({item for values in (*net.values(), *cash.values()) for item in values})

    _apply_bankruptcy_zeros(net, cash, rounds, _bankruptcy_rounds(run))

    assert net["b"][0] == 800
    assert cash["b"][0] == 50
    assert net["b"][1] == 0
    assert net["b"][2] == 0
    assert net["b"][3] == 0
    assert cash["b"][3] == 0
    assert net["a"] == {0: 1500, 1: 1550, 2: 1600, 3: 1650}
    assert net["c"] == {0: 1600, 1: 1700, 2: 1750, 3: 1800}


def test_write_run_curves_zeroes_bankrupt_player_with_run_artifacts(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_run_artifacts(
        run,
        players=[("a", 1), ("b", 2)],
        events=[
            ("turn_started", {"player_id": "a"}),
            ("turn_started", {"player_id": "b"}),
            ("turn_started", {"player_id": "a"}),
            ("turn_started", {"player_id": "b"}),
            ("player_bankrupt", {"player_id": "b"}),
            ("turn_started", {"player_id": "a"}),
        ],
    )
    _write_digest(
        run,
        _FULL_HEADER,
        [
            "0,a,1500,100\n",
            "0,b,800,50\n",
            "1,a,1550,120\n",
            "1,b,850,40\n",
            "2,a,1600,140\n",
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
