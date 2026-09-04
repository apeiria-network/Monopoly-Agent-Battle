"""Unit tests for the CSV LLM reply digest."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from monopoly_agent_battle.reporting.llm_digest import (
    COLUMNS,
    LLMDigestError,
    build_llm_digest,
    write_llm_digest,
)


def _write_jsonl(run_directory: Path, name: str, records: list[dict[str, object]]) -> None:
    run_directory.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    (run_directory / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_decisions(run_directory: Path, records: list[dict[str, object]]) -> None:
    _write_jsonl(run_directory, "decisions.jsonl", records)


def _write_calls(run_directory: Path, records: list[dict[str, object]]) -> None:
    _write_jsonl(run_directory, "llm_calls.jsonl", records)


def _reply(option: str, reason: str, target: object | None = None) -> str:
    selected: dict[str, object] = {"option": option}
    if target is not None:
        selected["target"] = target
    return json.dumps({"selected_option": selected, "reason": reason}, ensure_ascii=False)


def _decision(
    player_id: str,
    complete_rounds: int,
    *,
    executed_command: str = "EndTurn",
    visible_state: dict[str, object] | None = None,
    validation_retries: int = 0,
    connection_retries: int = 0,
    fallback: bool = False,
) -> dict[str, object]:
    return {
        "request": {
            "player_id": player_id,
            "complete_rounds": complete_rounds,
            "visible_state": visible_state or _visible_state(player_id),
        },
        "executed_command": {"command_type": executed_command, "command": {"player_id": player_id}},
        "validation_retries": validation_retries,
        "connection_retries": connection_retries,
        "fallback": fallback,
    }


def _visible_state(player_id: str) -> dict[str, object]:
    board: list[dict[str, object]] = []
    for position, price, building_cost, level, mortgaged, owner in (
        (1, 60, 50, 2, False, player_id),
        (3, 60, 50, 0, True, player_id),
        (5, 200, 0, 0, False, "other"),
    ):
        board.append(
            {
                "position": position,
                "price": price,
                "building_cost": building_cost,
                "building_level": level,
                "mortgaged": mortgaged,
                "owner_id": owner,
            }
        )
    return {
        "board": board,
        "your_state": {
            "player_id": player_id,
            "cash": 1500,
            "chance_cards": ["chance-build", "chance-tax"],
        },
    }


def _rows(run_directory: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(build_llm_digest(run_directory))))


def test_missing_run_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(LLMDigestError):
        build_llm_digest(tmp_path / "nope")


def test_no_calls_renders_header_only(tmp_path: Path) -> None:
    tmp_path.joinpath("run").mkdir()
    digest = build_llm_digest(tmp_path / "run")
    assert digest.splitlines() == [",".join(COLUMNS)]


def test_calls_without_decisions_raise(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_calls(run, [{"caller_role": "a", "response_summary": _reply("end_turn", "结束。")}])
    with pytest.raises(LLMDigestError):
        build_llm_digest(run)


def test_baseline_decision_row_values(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_decisions(run, [_decision("baseline-1", 2, executed_command="RollDice")])
    _write_calls(
        run,
        [
            {
                "complete_rounds": 2,
                "caller_role": "baseline-1",
                "response_summary": _reply("roll_dice", "掷骰推进。"),
                "error": None,
            }
        ],
    )
    rows = _rows(run)
    assert len(rows) == 1
    row = rows[0]
    assert row["轮次"] == "2"
    assert row["玩家"] == "baseline-1"
    assert row["发言者"] == "baseline-1"
    assert row["reason"] == "掷骰推进。"
    assert row["选项"] == "roll_dice"
    assert row["最终执行命令"] == "RollDice"
    # cash 1500 + 60 (pos 1) + 50*2 (buildings) + 60 - 60 (mortgaged pos 3) = 1660
    assert row["当前玩家净资产"] == "1660"
    assert row["当前玩家持有机会卡数"] == "2"
    assert row["是否是最终决策者"] == "True"
    assert row["是否报错回复"] == "False"
    assert row["target"] == ""


def test_target_column_renders_raw_values(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_decisions(
        run,
        [
            _decision("baseline-1", 1),
            _decision("baseline-1", 2),
            _decision("baseline-1", 3),
        ],
    )
    _write_calls(
        run,
        [
            {
                "caller_role": "baseline-1",
                "response_summary": _reply(
                    "use_chance_card-chance-tax", "查税。", target="baseline-2"
                ),
                "error": None,
            },
            {
                "caller_role": "baseline-1",
                "response_summary": _reply("use_chance_card-chance-build", "建房。", target=6),
                "error": None,
            },
            {
                "caller_role": "baseline-1",
                "response_summary": _reply("mortgage", "抵押。", target={"position": 5}),
                "error": None,
            },
        ],
    )
    rows = _rows(run)
    assert [row["target"] for row in rows] == ["baseline-2", "6", '{"position": 5}']


def test_code_fenced_reply_is_parsed(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_decisions(run, [_decision("baseline-1", 1)])
    fenced = "```json\n" + _reply("end_turn", "围栏内的回复。") + "\n```"
    _write_calls(
        run,
        [{"caller_role": "baseline-1", "response_summary": fenced, "error": None}],
    )
    rows = _rows(run)
    assert rows[0]["选项"] == "end_turn"
    assert rows[0]["reason"] == "围栏内的回复。"


def test_fenced_reply_without_reason_strips_fence(tmp_path: Path) -> None:
    run = tmp_path / "run"
    fenced = '```json\n{"selected_option": {"option": "end_turn"}}\n```'
    _write_decisions(run, [_decision("baseline-1", 1)])
    _write_calls(
        run,
        [{"caller_role": "baseline-1", "response_summary": fenced, "error": None}],
    )
    rows = _rows(run)
    assert rows[0]["选项"] == "end_turn"
    assert "```" not in rows[0]["reason"]
    assert rows[0]["reason"].startswith("{")
    assert rows[0]["reason"].endswith("}")


def test_validation_retry_attempts_are_error_rows(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_decisions(run, [_decision("baseline-1", 1, validation_retries=2)])
    _write_calls(
        run,
        [
            {
                "caller_role": "baseline-1",
                "response_summary": "不是 JSON",
                "error": None,
            },
            {
                "caller_role": "baseline-1",
                "response_summary": _reply("buy", "目标非法", target={"position": 99}),
                "error": None,
            },
            {
                "caller_role": "baseline-1",
                "response_summary": _reply("end_turn", "最终改走结束回合。"),
                "error": None,
            },
        ],
    )
    rows = _rows(run)
    assert len(rows) == 3
    assert [row["是否报错回复"] for row in rows] == ["True", "True", "False"]
    assert rows[2]["选项"] == "end_turn"


def test_fallback_decision_marks_all_attempts_as_errors(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_decisions(
        run,
        [_decision("baseline-1", 1, validation_retries=1, fallback=True)],
    )
    _write_calls(
        run,
        [
            {"caller_role": "baseline-1", "response_summary": "坏回复", "error": None},
            {"caller_role": "baseline-1", "response_summary": "还是坏", "error": None},
        ],
    )
    rows = _rows(run)
    assert [row["是否报错回复"] for row in rows] == ["True", "True"]


def test_connection_error_row_shows_error_reason(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_decisions(
        run,
        [_decision("baseline-1", 1, connection_retries=1, fallback=True)],
    )
    _write_calls(
        run,
        [
            {"caller_role": "baseline-1", "response_summary": None, "error": "connection reset"},
            {"caller_role": "baseline-1", "response_summary": None, "error": "timeout"},
        ],
    )
    rows = _rows(run)
    assert [row["是否报错回复"] for row in rows] == ["True", "True"]
    assert rows[0]["reason"] == "connection reset"


def test_court_trace_rows_and_final_decision_flag(tmp_path: Path) -> None:
    run = tmp_path / "run"
    decision = _decision("qin-court", 3, executed_command="UseChanceCard")
    decision["court_trace"] = {
        "court": "qin",
        "decision_id": "decision-x",
        "calls": [
            {
                "role": "chancellor",
                "caller_role": "qin-court.chancellor",
                "outcome": "success",
                "content": _reply("use_chance_card-chance-tax", "查税收钱。"),
            },
            {
                "role": "emperor",
                "caller_role": "qin-court.emperor",
                "outcome": "success",
                "content": "```json\n"
                + _reply("use_chance_card-chance-tax", "批准查税。", target="baseline-1")
                + "\n```",
            },
        ],
    }
    _write_decisions(run, [decision])
    _write_calls(
        run,
        [
            {
                "caller_role": "qin-court.chancellor",
                "response_summary": _reply("use_chance_card-chance-tax", "查税收钱。"),
                "error": None,
            },
            {
                "caller_role": "qin-court.emperor",
                "response_summary": _reply("use_chance_card-chance-tax", "批准查税。"),
                "error": None,
            },
        ],
    )
    rows = _rows(run)
    assert len(rows) == 2
    assert rows[0]["发言者"] == "qin-court.chancellor"
    assert rows[0]["是否是最终决策者"] == "False"
    assert rows[1]["发言者"] == "qin-court.emperor"
    assert rows[1]["是否是最终决策者"] == "True"
    assert rows[1]["选项"] == "use_chance_card-chance-tax"
    assert rows[1]["target"] == "baseline-1"
    assert rows[0]["target"] == ""
    assert all(row["最终执行命令"] == "UseChanceCard" for row in rows)
    assert all(row["是否报错回复"] == "False" for row in rows)


def test_trace_validation_error_marks_preceding_call(tmp_path: Path) -> None:
    run = tmp_path / "run"
    decision = _decision("qin-court", 1)
    decision["court_trace"] = {
        "court": "qin",
        "decision_id": "decision-x",
        "calls": [
            {
                "role": "emperor",
                "caller_role": "qin-court.emperor",
                "outcome": "success",
                "content": "目标非法的回复",
            },
            {
                "role": "emperor",
                "caller_role": "qin-court.emperor",
                "outcome": "validation_error",
                "content": "目标非法的回复",
                "error": "target value is not legal for this option",
            },
            {
                "role": "emperor",
                "caller_role": "qin-court.emperor",
                "outcome": "success",
                "content": _reply("end_turn", "改走结束回合。"),
            },
        ],
    }
    _write_decisions(run, [decision])
    _write_calls(
        run,
        [
            {"caller_role": "qin-court.emperor", "response_summary": "目标非法的回复"},
            {
                "caller_role": "qin-court.emperor",
                "response_summary": _reply("end_turn", "改走结束回合。"),
            },
        ],
    )
    rows = _rows(run)
    # The validation_error marker is not a separate call: two real rows only.
    assert len(rows) == 2
    assert [row["是否报错回复"] for row in rows] == ["True", "False"]


def test_free_text_reply_keeps_reason_without_option(tmp_path: Path) -> None:
    run = tmp_path / "run"
    decision = _decision("shang", 1)
    decision["court_trace"] = {
        "court": "shang",
        "decision_id": "decision-x",
        "calls": [
            {
                "role": "great_priest",
                "caller_role": "shang.great_priest",
                "outcome": "success",
                "content": "天垂象，宜守不宜攻。",
            }
        ],
    }
    _write_decisions(run, [decision])
    _write_calls(
        run, [{"caller_role": "shang.great_priest", "response_summary": "天垂象，宜守不宜攻。"}]
    )
    rows = _rows(run)
    assert rows[0]["reason"] == "天垂象，宜守不宜攻。"
    assert rows[0]["选项"] == ""


def test_assessments_reply_composes_reason(tmp_path: Path) -> None:
    run = tmp_path / "run"
    content = json.dumps(
        {
            "assessments": [
                {"officer_id": "chancellor", "judgement": "agree", "reason": "丞相赞同。"},
                {"officer_id": "grand_marshal", "judgement": "disagree", "reason": "太尉反对。"},
            ]
        },
        ensure_ascii=False,
    )
    decision = _decision("qin-court", 1)
    decision["court_trace"] = {
        "court": "qin",
        "decision_id": "decision-x",
        "calls": [
            {
                "role": "imperial_counsellor",
                "caller_role": "qin-court.imperial_counsellor",
                "outcome": "success",
                "content": content,
            }
        ],
    }
    _write_decisions(run, [decision])
    _write_calls(
        run,
        [{"caller_role": "qin-court.imperial_counsellor", "response_summary": content}],
    )
    rows = _rows(run)
    assert rows[0]["reason"] == "chancellor·agree：丞相赞同。；grand_marshal·disagree：太尉反对。"
    assert rows[0]["选项"] == ""


def test_reason_with_commas_and_newlines_survives_csv(tmp_path: Path) -> None:
    run = tmp_path / "run"
    tricky = '第一点，含逗号\n换行 "引号" 结尾'
    _write_decisions(run, [_decision("baseline-1", 1)])
    _write_calls(
        run,
        [{"caller_role": "baseline-1", "response_summary": _reply("end_turn", tricky)}],
    )
    rows = _rows(run)
    assert rows[0]["reason"] == tricky


def test_missing_round_renders_question_mark(tmp_path: Path) -> None:
    run = tmp_path / "run"
    decision = _decision("baseline-1", 1)
    decision["request"]["complete_rounds"] = None  # type: ignore[index]
    _write_decisions(run, [decision])
    _write_calls(
        run,
        [{"caller_role": "baseline-1", "response_summary": _reply("end_turn", "结束。")}],
    )
    rows = _rows(run)
    assert rows[0]["轮次"] == "?"


def test_write_llm_digest_creates_csv_with_bom(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_decisions(run, [_decision("baseline-1", 1)])
    _write_calls(
        run,
        [{"caller_role": "baseline-1", "response_summary": _reply("end_turn", "结束。")}],
    )
    path = write_llm_digest(run)
    assert path == run / "llm_digest.csv"
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "结束。".encode() in raw
