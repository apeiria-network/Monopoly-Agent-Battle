"""Condensed, human-readable Markdown digest of a run's LLM replies.

Each line summarizes one LLM call from ``llm_calls.jsonl`` as:

    第 {round} 轮 · {caller} · 决策：{option} · {reason}

A caller whose role is a final decision maker — a court emperor (role ending in
``.emperor``) or a plain baseline player (role without a ``.``) — is rendered in
**bold**. The reply reason is truncated to a fixed character budget.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

_REASON_CHAR_LIMIT = 400


class LLMDigestError(ValueError):
    """Raised when a run directory cannot produce an LLM digest."""


def build_llm_digest(run_directory: Path) -> str:
    """Read ``llm_calls.jsonl`` and render a one-line-per-call Markdown digest."""
    run_directory = Path(run_directory)
    if not run_directory.is_dir():
        raise LLMDigestError(f"run directory does not exist: {run_directory}")
    calls_path = run_directory / "llm_calls.jsonl"
    records = _read_jsonl(calls_path)
    lines = [f"# LLM 回复精简：{run_directory.name}", ""]
    if not records:
        lines.append("（本局没有 LLM 调用记录。）")
        return "\n".join(lines) + "\n"
    for record in records:
        lines.append(_render_line(record))
    return "\n".join(lines) + "\n"


def write_llm_digest(run_directory: Path, output: Path | None = None) -> Path:
    """Render the digest and write it to a Markdown file, returning its path."""
    run_directory = Path(run_directory)
    target = output if output is not None else run_directory / "llm_digest.md"
    target.write_text(build_llm_digest(run_directory), encoding="utf-8", newline="\n")
    return target


def _render_line(record: dict[str, Any]) -> str:
    caller = str(record.get("caller_role") or "未知")
    round_label = _round_label(record.get("complete_rounds"))
    option, reason = _extract_option_and_reason(record)
    final = _is_final_decision(caller)
    body = f"第 {round_label} 轮 · {caller} · 决策：{option} · {reason}"
    return f"- **{body}**" if final else f"- {body}"


def _round_label(value: object) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else "?"


def _is_final_decision(caller_role: str) -> bool:
    """A court emperor or a plain baseline player produces the final decision."""
    return caller_role.endswith(".emperor") or "." not in caller_role


def _extract_option_and_reason(record: dict[str, Any]) -> tuple[str, str]:
    """Parse the recorded raw reply into a display option and truncated reason."""
    error = record.get("error")
    if error:
        return "（调用失败）", _truncate(str(error))
    summary = record.get("response_summary")
    if not isinstance(summary, str):
        return "（无回复）", ""
    try:
        document = json.loads(summary)
    except (json.JSONDecodeError, TypeError):
        # Not all callers reply with option JSON (e.g. the Shang oracle);
        # show the raw reply as the reason with no option.
        return "（无选项）", _truncate(summary)
    if not isinstance(document, dict):
        return "（无选项）", _truncate(summary)
    typed = cast(dict[str, Any], document)
    return _format_option(typed.get("selected_option")), _truncate(_reason_text(typed))


def _format_option(selected: object) -> str:
    if isinstance(selected, dict):
        typed = cast(dict[str, Any], selected)
        option = typed.get("option")
        target = typed.get("target")
        option_text = str(option) if option is not None else "（未指定）"
        if target is not None:
            return f"{option_text}（目标：{json.dumps(target, ensure_ascii=False)}）"
        return option_text
    if selected is None:
        return "（无选项）"
    return str(selected)


def _reason_text(document: dict[str, Any]) -> str:
    for key in ("reason", "reasoning"):
        value = document.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _truncate(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _REASON_CHAR_LIMIT:
        return collapsed
    return collapsed[:_REASON_CHAR_LIMIT] + "…[截断]"


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
                raise LLMDigestError(f"{path.name}:{line_number} must be a JSON object")
            records.append(cast(dict[str, Any], value))
    except (OSError, json.JSONDecodeError) as error:
        raise LLMDigestError(f"cannot read {path.name}: {error}") from error
    return records
