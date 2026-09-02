"""Unit tests for the condensed LLM reply digest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from monopoly_agent_battle.reporting.llm_digest import (
    LLMDigestError,
    build_llm_digest,
    write_llm_digest,
)


def _write_calls(run_directory: Path, records: list[dict[str, object]]) -> None:
    run_directory.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    (run_directory / "llm_calls.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _reply(option: str, reason: str, target: object | None = None) -> str:
    selected: dict[str, object] = {"option": option}
    if target is not None:
        selected["target"] = target
    return json.dumps({"selected_option": selected, "reason": reason}, ensure_ascii=False)


def test_missing_run_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(LLMDigestError):
        build_llm_digest(tmp_path / "nope")


def test_no_calls_renders_placeholder(tmp_path: Path) -> None:
    tmp_path.joinpath("run").mkdir()
    digest = build_llm_digest(tmp_path / "run")
    assert "没有 LLM 调用记录" in digest


def test_court_emperor_and_baseline_lines_are_bold(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_calls(
        run,
        [
            {
                "complete_rounds": 2,
                "caller_role": "tang.zhongshu",
                "response_summary": _reply("end_turn", "起草结束回合。"),
                "error": None,
            },
            {
                "complete_rounds": 2,
                "caller_role": "tang.emperor",
                "response_summary": _reply("end_turn", "皇帝裁决结束回合。"),
                "error": None,
            },
            {
                "complete_rounds": 3,
                "caller_role": "baseline-player",
                "response_summary": _reply("roll_dice", "掷骰。"),
                "error": None,
            },
        ],
    )
    digest = build_llm_digest(run)
    lines = [line for line in digest.splitlines() if line.startswith("- ")]
    assert len(lines) == 3
    # Middle official (zhongshu) is not a final decision -> not bold.
    assert lines[0].startswith("- 第 2 轮 · tang.zhongshu")
    assert not lines[0].startswith("- **")
    # Emperor is a final decision -> bold.
    assert lines[1].startswith("- **第 2 轮 · tang.emperor")
    assert lines[1].endswith("**")
    # Baseline player (no dot) is a final decision -> bold.
    assert lines[2].startswith("- **第 3 轮 · baseline-player")


def test_reason_is_truncated_to_400_chars(tmp_path: Path) -> None:
    run = tmp_path / "run"
    long_reason = "推" * 500
    _write_calls(
        run,
        [
            {
                "complete_rounds": 1,
                "caller_role": "a.emperor",
                "response_summary": _reply("end_turn", long_reason),
                "error": None,
            }
        ],
    )
    digest = build_llm_digest(run)
    assert "…[截断]" in digest
    # 400 kept chars + marker; the original 500-char run must not appear in full.
    assert "推" * 500 not in digest
    assert "推" * 400 in digest


def test_non_json_reply_shown_as_reason_without_option(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_calls(
        run,
        [
            {
                "complete_rounds": 1,
                "caller_role": "shang.great_priest",
                "response_summary": "天垂象，宜守不宜攻。",
                "error": None,
            }
        ],
    )
    digest = build_llm_digest(run)
    assert "（无选项）" in digest
    assert "天垂象" in digest


def test_failed_call_line(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_calls(
        run,
        [
            {
                "complete_rounds": 1,
                "caller_role": "a.emperor",
                "response_summary": None,
                "error": "connection reset",
            }
        ],
    )
    digest = build_llm_digest(run)
    assert "（调用失败）" in digest
    assert "connection reset" in digest


def test_option_target_is_rendered(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_calls(
        run,
        [
            {
                "complete_rounds": 4,
                "caller_role": "a.emperor",
                "response_summary": _reply("mortgage", "抵押筹钱。", target={"position": 5}),
                "error": None,
            }
        ],
    )
    digest = build_llm_digest(run)
    assert "目标：" in digest
    assert "position" in digest


def test_missing_round_renders_question_mark(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_calls(
        run,
        [
            {
                "complete_rounds": None,
                "caller_role": "a.emperor",
                "response_summary": _reply("end_turn", "结束。"),
                "error": None,
            }
        ],
    )
    digest = build_llm_digest(run)
    assert "第 ? 轮" in digest


def test_write_llm_digest_creates_default_file(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_calls(
        run,
        [
            {
                "complete_rounds": 1,
                "caller_role": "a.emperor",
                "response_summary": _reply("end_turn", "结束。"),
                "error": None,
            }
        ],
    )
    path = write_llm_digest(run)
    assert path == run / "llm_digest.md"
    assert path.read_text(encoding="utf-8").startswith("# LLM 回复精简")
