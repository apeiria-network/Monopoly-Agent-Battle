"""CSV digest of a run's LLM replies, one row per real LLM call.

Rows join ``decisions.jsonl`` (round, player, executed command, state snapshot)
with per-call reply data. Court decisions carry a ``court_trace`` whose entries
mirror the real calls (``success`` / ``connection_error``) plus
``validation_error`` markers that reject the preceding call of the same role.
Baseline players' calls come from ``llm_calls.jsonl`` and are matched to their
decisions in order using each decision's attempt count
(1 + validation_retries + connection_retries). Decisions served by no LLM call
at all (random baselines) still emit one row parsed from their recorded
``attempted_response``, with fields lacking a reply left empty.

Columns (all values are strings):

    轮次, 玩家, 发言者, reason, 选项, target, 最终执行命令,
    当前玩家净资产, 当前玩家持有机会卡数, 是否是最终决策者, 是否报错回复

A court emperor (caller role ending in ``.emperor``) or a plain baseline
player (role without a ``.``) is the final decision maker. Replies are parsed
with the protocol's lenient fence-stripping parser; free-text or advice-only
replies leave the 选项 column empty. The ``target`` column carries the reply's
raw ``selected_option.target`` value (strings as-is, other JSON shapes
serialized). Boolean columns render ``True``/``False``. The file is written
UTF-8 with BOM so Excel opens the Chinese header without an import wizard.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, cast

from monopoly_agent_battle.decision.protocol import strip_code_fence

COLUMNS: tuple[str, ...] = (
    "轮次",
    "玩家",
    "发言者",
    "reason",
    "选项",
    "target",
    "最终执行命令",
    "当前玩家净资产",
    "当前玩家持有机会卡数",
    "是否是最终决策者",
    "是否报错回复",
)

_REAL_OUTCOMES = frozenset({"success", "connection_error"})


class LLMDigestError(ValueError):
    """Raised when a run directory cannot produce an LLM digest."""


def build_llm_digest(run_directory: Path) -> str:
    """Read the run artifacts and render the per-call CSV digest text."""
    run_directory = Path(run_directory)
    if not run_directory.is_dir():
        raise LLMDigestError(f"run directory does not exist: {run_directory}")
    decisions = _read_jsonl(run_directory / "decisions.jsonl")
    calls = _read_jsonl(run_directory / "llm_calls.jsonl")
    if calls and not decisions:
        raise LLMDigestError("decisions.jsonl is required to digest llm_calls.jsonl")
    queue = _CallQueue(calls)
    rows: list[list[str]] = []
    for record in decisions:
        rows.extend(_decision_rows(record, queue))
    rows.extend(_leftover_rows(queue))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue()


def write_llm_digest(run_directory: Path, output: Path | None = None) -> Path:
    """Render the digest and write it to a CSV file, returning its path."""
    run_directory = Path(run_directory)
    target = output if output is not None else run_directory / "llm_digest.csv"
    target.write_text(build_llm_digest(run_directory), encoding="utf-8-sig", newline="\n")
    return target


class _CallQueue:
    """Per-player FIFO queues over ``llm_calls.jsonl`` records."""

    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self._by_player: dict[str, list[dict[str, Any]]] = {}
        for call in calls:
            player = _call_player(str(call.get("caller_role") or ""))
            self._by_player.setdefault(player, []).append(call)
        self._index: dict[str, int] = {player: 0 for player in self._by_player}

    def take(self, player: str, count: int) -> list[dict[str, Any]]:
        """Consume up to ``count`` not-yet-assigned calls of ``player``."""
        queue = self._by_player.get(player, [])
        start = self._index.get(player, 0)
        taken = queue[start : start + max(count, 0)]
        self._index[player] = start + len(taken)
        return taken

    def leftovers(self) -> list[dict[str, Any]]:
        """Return every call that no decision consumed (should stay empty)."""
        remaining: list[dict[str, Any]] = []
        for player, queue in self._by_player.items():
            remaining.extend(queue[self._index.get(player, 0) :])
        return remaining


def _decision_rows(record: dict[str, Any], queue: _CallQueue) -> list[list[str]]:
    request = cast(dict[str, Any], record.get("request") or {})
    player = str(request.get("player_id") or "")
    context = _decision_context(record)
    trace_calls = _trace_calls(record)
    if trace_calls:
        rows = _trace_rows(trace_calls, context)
        # The real trace calls mirror one llm_calls record each; skip them so
        # later decisions of this player keep aligning with the queue.
        queue.take(player, sum(1 for entry in trace_calls if _outcome(entry) in _REAL_OUTCOMES))
        return rows
    attempts = 1 + _count(record, "validation_retries") + _count(record, "connection_retries")
    fallback = bool(record.get("fallback"))
    calls = queue.take(player, attempts)
    if not calls:
        # No LLM calls serve this decision: random baselines synthesize a
        # protocol-valid reply without any model. Emit one decision-level row
        # from the recorded response; fields without a reply stay empty.
        reason, option, target = _parse_reply(record.get("attempted_response"))
        return [
            [
                context.round_label,
                player,
                player,
                reason,
                option,
                target,
                context.executed_command,
                context.net_worth,
                context.chance_card_count,
                _true_false(True),
                _true_false(fallback),
            ]
        ]
    rows: list[list[str]] = []
    for index, call in enumerate(calls):
        error = call.get("error")
        is_last = index == len(calls) - 1
        rejected = bool(error) or not is_last or fallback
        reason, option, target = _parse_reply(call.get("response_summary"))
        if error:
            reason = str(error)
        rows.append(
            [
                context.round_label,
                player,
                str(call.get("caller_role") or player),
                reason,
                option,
                target,
                context.executed_command,
                context.net_worth,
                context.chance_card_count,
                _true_false(_is_final_decision(str(call.get("caller_role") or ""))),
                _true_false(rejected),
            ]
        )
    return rows


def _leftover_rows(queue: _CallQueue) -> list[list[str]]:
    rows: list[list[str]] = []
    for call in queue.leftovers():
        reason, option, target = _parse_reply(call.get("response_summary"))
        error = call.get("error")
        if error:
            reason = str(error)
        caller = str(call.get("caller_role") or "")
        round_value = call.get("complete_rounds")
        rows.append(
            [
                str(round_value) if isinstance(round_value, int) else "?",
                _call_player(caller),
                caller,
                reason,
                option,
                target,
                "",
                "",
                "",
                _true_false(_is_final_decision(caller)),
                _true_false(bool(error)),
            ]
        )
    return rows


class _DecisionContext:
    """Per-decision values repeated on every call row of that decision."""

    def __init__(
        self,
        round_label: str,
        executed_command: str,
        net_worth: str,
        chance_card_count: str,
    ) -> None:
        self.round_label = round_label
        self.executed_command = executed_command
        self.net_worth = net_worth
        self.chance_card_count = chance_card_count


def _decision_context(record: dict[str, Any]) -> _DecisionContext:
    request = cast(dict[str, Any], record.get("request") or {})
    player = str(request.get("player_id") or "")
    visible = cast(dict[str, Any], request.get("visible_state") or {})
    round_value = request.get("complete_rounds")
    executed = cast(dict[str, Any], record.get("executed_command") or {})
    return _DecisionContext(
        str(round_value) if isinstance(round_value, int) else "?",
        str(executed.get("command_type") or ""),
        str(_visible_net_worth(visible, player)),
        str(_visible_chance_card_count(visible)),
    )


def _trace_rows(trace_calls: list[dict[str, Any]], context: _DecisionContext) -> list[list[str]]:
    rows: list[list[str]] = []
    last_row_by_role: dict[str, list[str]] = {}
    for entry in trace_calls:
        caller = str(entry.get("caller_role") or "")
        role = str(entry.get("role") or "")
        outcome = _outcome(entry)
        if outcome not in _REAL_OUTCOMES:
            if outcome == "validation_error":
                rejected_row = last_row_by_role.get(role)
                if rejected_row is not None:
                    rejected_row[-1] = "True"
            continue
        reason, option, target = _parse_reply(entry.get("content"))
        if outcome == "connection_error":
            reason = str(entry.get("error") or reason)
        row = [
            context.round_label,
            _call_player(caller),
            caller,
            reason,
            option,
            target,
            context.executed_command,
            context.net_worth,
            context.chance_card_count,
            _true_false(_is_final_decision(caller)),
            _true_false(outcome == "connection_error"),
        ]
        rows.append(row)
        if role:
            last_row_by_role[role] = row
    return rows


def _trace_calls(record: dict[str, Any]) -> list[dict[str, Any]]:
    trace_value = record.get("court_trace")
    if not isinstance(trace_value, dict):
        return []
    trace = cast(dict[str, Any], trace_value)
    calls_value = trace.get("calls")
    if not isinstance(calls_value, list):
        return []
    return [
        cast(dict[str, Any], entry)
        for entry in cast(list[object], calls_value)
        if isinstance(entry, dict)
    ]


def _visible_net_worth(visible: dict[str, Any], player_id: str) -> int:
    """Recompute the engine's net worth formula from the decision snapshot."""
    your_state = cast(dict[str, Any], visible.get("your_state") or {})
    total = int(your_state.get("cash") or 0)
    board_value = visible.get("board")
    if not isinstance(board_value, list):
        return total
    for space_value in cast(list[object], board_value):
        if not isinstance(space_value, dict):
            continue
        space = cast(dict[str, Any], space_value)
        if space.get("owner_id") != player_id:
            continue
        price = int(space.get("price") or 0)
        total += price
        total += int(space.get("building_cost") or 0) * int(space.get("building_level") or 0)
        if space.get("mortgaged"):
            total -= price
    return total


def _visible_chance_card_count(visible: dict[str, Any]) -> int:
    your_state = cast(dict[str, Any], visible.get("your_state") or {})
    cards_value = your_state.get("chance_cards")
    if not isinstance(cards_value, list):
        return 0
    return len(cast(list[object], cards_value))


def _parse_reply(raw: object) -> tuple[str, str, str]:
    """Parse a reply into (reason, option, target); free text keeps no option.

    Raw-text fallbacks land in the reason column with the outer Markdown code
    fence (```` ```json ... ``` ````) already unwrapped. The target column
    carries the reply's raw ``selected_option.target`` value.
    """
    if not isinstance(raw, str) or not raw:
        return "", "", ""
    text = strip_code_fence(raw)
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text, "", ""
    if not isinstance(document, dict):
        return text, "", ""
    typed = cast(dict[str, Any], document)
    reason = ""
    for key in ("reason", "reasoning"):
        value = typed.get(key)
        if isinstance(value, str) and value:
            reason = value
            break
    if not reason:
        # The counsellor-style reply carries no top-level reason; compose one
        # from its per-officer assessments instead.
        reason = _assessments_reason(typed)
    if not reason:
        reason = text
    option = ""
    target = ""
    selected_value = typed.get("selected_option")
    if isinstance(selected_value, dict):
        selected = cast(dict[str, Any], selected_value)
        option = str(selected.get("option") or "")
        target = _render_target(selected.get("target"))
    return reason, option, target


def _render_target(value: object) -> str:
    """Render a raw target field: strings as-is, other JSON shapes serialized."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _assessments_reason(document: dict[str, Any]) -> str:
    """Compose ``officer·judgement：reason`` pieces from an assessments reply."""
    assessments_value = document.get("assessments")
    if not isinstance(assessments_value, list):
        return ""
    pieces: list[str] = []
    for item_value in cast(list[object], assessments_value):
        if not isinstance(item_value, dict):
            continue
        item = cast(dict[str, Any], item_value)
        officer = str(item.get("officer_id") or "")
        judgement = str(item.get("judgement") or "")
        text = str(item.get("reason") or "")
        if not officer and not text:
            continue
        pieces.append(f"{officer}·{judgement}：{text}" if judgement else f"{officer}：{text}")
    return "；".join(pieces)


def _call_player(caller_role: str) -> str:
    return caller_role.split(".", 1)[0]


def _is_final_decision(caller_role: str) -> bool:
    """A court emperor or a plain baseline player produces the final decision."""
    return caller_role.endswith(".emperor") or "." not in caller_role


def _true_false(value: bool) -> str:
    return "True" if value else "False"


def _outcome(entry: dict[str, Any]) -> str:
    return str(entry.get("outcome") or "")


def _count(record: dict[str, Any], key: str) -> int:
    value = record.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


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
