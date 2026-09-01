"""Readable single-game reports built from persisted run artifacts."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


class ReportError(ValueError):
    """Raised when a run directory cannot produce a trustworthy report."""


def build_single_game_report(run_directory: Path) -> dict[str, Any]:
    """Read a run directory and return a safe, aggregate-only report model."""
    run_directory = Path(run_directory)
    if not run_directory.is_dir():
        raise ReportError(f"run directory does not exist: {run_directory}")
    config_doc = _read_required_json(run_directory / "config.json")
    result = _read_required_json(run_directory / "result.json")
    config = config_doc.get("config")
    if not isinstance(config, dict):
        raise ReportError("config.json is missing object field 'config'")
    events = _read_jsonl(run_directory / "events.jsonl")
    decisions = _read_jsonl(run_directory / "decisions.jsonl")
    llm_calls = _read_jsonl(run_directory / "llm_calls.jsonl")
    runtime = _read_jsonl(run_directory / "runtime.jsonl")
    performance = _read_jsonl(run_directory / "performance.jsonl")
    players = result.get("players", {})
    if not isinstance(players, dict):
        raise ReportError("result.json field 'players' must be an object")
    event_counts = Counter(str(record.get("event_type", "unknown")) for record in events)
    runtime_counts = Counter(str(record.get("event_type", "unknown")) for record in runtime)
    player_rows = []
    for player_id, player in players.items():
        if not isinstance(player, dict):
            continue
        player_rows.append(
            {
                "player_id": str(player_id),
                "cash": player.get("cash"),
                "position": player.get("position"),
                "properties": len(player.get("properties", [])),
                "bankrupt": bool(player.get("bankrupt", False)),
                "survived_turns": player.get("survived_turns"),
            }
        )
    return {
        "run_directory": str(run_directory),
        "game_id": config.get("game_id"),
        "experiment_id": config.get("experiment_id"),
        "config_hash": config_doc.get("config_hash"),
        "rules_version": config.get("rules_version"),
        "status": result.get("status"),
        "end_reason": result.get("end_reason"),
        "validity_status": result.get("validity_status", "not_recorded"),
        "complete_rounds": result.get("complete_rounds"),
        "rankings": result.get("rankings", []),
        "players": player_rows,
        "event_counts": dict(sorted(event_counts.items())),
        "runtime_event_counts": dict(sorted(runtime_counts.items())),
        "decisions": {
            "total": len(decisions),
            "fallbacks": sum(bool(item.get("fallback")) for item in decisions),
            "non_llm": sum(item.get("controller_type") == "non_llm" for item in decisions),
        },
        "llm": _llm_stats(llm_calls),
        "performance_windows": len(performance),
        "source_files": sorted(path.name for path in run_directory.iterdir() if path.is_file()),
    }


def render_single_game_report(report: dict[str, Any]) -> str:
    """Render the aggregate report as concise human-readable Markdown."""
    lines = [
        f"# 单局结果：{report.get('game_id') or '未知'}",
        "",
        f"- 状态：`{report.get('status')}`",
        f"- 结束原因：`{report.get('end_reason')}`",
        f"- 有效性：`{report.get('validity_status')}`",
        f"- 完整回合：{report.get('complete_rounds')}",
        f"- 配置哈希：`{report.get('config_hash')}`",
        f"- 规则版本：`{report.get('rules_version')}`",
        "",
        "## 排名",
        "",
        "、".join(
            f"{index}. {player}" for index, player in enumerate(report.get("rankings", []), 1)
        )
        or "暂无排名",
        "",
        "## 玩家",
        "",
        "| 玩家 | 现金 | 位置 | 地产数 | 存活回合 | 破产 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for player in report.get("players", []):
        lines.append(
            f"| {player['player_id']} | {player['cash']} | {player['position']} | "
            f"{player['properties']} | {player['survived_turns']} | "
            f"{'是' if player['bankrupt'] else '否'} |"
        )
    llm = report["llm"]
    lines.extend(
        [
            "",
            "## 审计统计",
            "",
            (
                f"- 决策：{report['decisions']['total']}"
                f"（非 LLM：{report['decisions']['non_llm']}，"
                f"回退：{report['decisions']['fallbacks']}）"
            ),
            (
                f"- LLM 调用：{llm['calls']}，输入 token：{llm['input_tokens']}，"
                f"输出 token：{llm['output_tokens']}，思考 token：{llm['reasoning_tokens']}"
            ),
            (
                f"- LLM 错误：{llm['errors']}，工具调用：{llm['tool_calls']}，"
                f"工具失败：{llm['tool_failures']}，总耗时：{llm['duration_ms']} ms"
            ),
            f"- 绩效窗口：{report['performance_windows']}",
            "",
            "## 关键事件",
            "",
        ]
    )
    lines.extend(f"- `{name}`：{count}" for name, count in report["event_counts"].items())
    return "\n".join(lines) + "\n"


def write_single_game_report(run_directory: Path, output: Path | None = None) -> Path:
    """Build and write a Markdown report without changing source audit files."""
    report = build_single_game_report(run_directory)
    target = output or (Path(run_directory) / "summary.md")
    target.write_text(render_single_game_report(report), encoding="utf-8", newline="\n")
    return target


def _read_required_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ReportError(f"missing required artifact: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReportError(f"cannot read {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise ReportError(f"{path.name} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ReportError(f"{path.name}:{line_number} must be a JSON object")
            records.append(value)
    except (OSError, json.JSONDecodeError) as error:
        raise ReportError(f"cannot read {path.name}: {error}") from error
    return records


def _llm_stats(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "calls": len(records),
        "input_tokens": sum(_number(record, "input_tokens") for record in records),
        "output_tokens": sum(_number(record, "output_tokens") for record in records),
        "reasoning_tokens": sum(_number(record, "reasoning_tokens") for record in records),
        "duration_ms": sum(_number(record, "duration_ms") for record in records),
        "tool_calls": sum(_number(record, "tool_calls") for record in records),
        "tool_failures": sum(_number(record, "tool_failures") for record in records),
        "errors": sum(bool(record.get("error")) for record in records),
    }


def _number(record: dict[str, Any], key: str) -> int:
    value = record.get(key, 0)
    return int(value) if isinstance(value, (int, float)) else 0
