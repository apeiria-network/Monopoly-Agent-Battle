"""Persistent artifacts for a single auditable game run."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from monopoly_agent_battle.config.loader import config_hash
from monopoly_agent_battle.config.models import GameConfig
from monopoly_agent_battle.context.broadcast import render_event
from monopoly_agent_battle.domain.models import GameEvent


def utc_timestamp() -> str:
    """Return an RFC 3339 UTC timestamp with microsecond precision."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class RunArtifacts:
    """Own the append-only artifacts produced during one game run."""

    def __init__(self, run_directory: Path) -> None:
        self.run_directory = run_directory
        self._next_event_id = 1
        self._next_llm_call_id = 1
        self._llm_call_lock = Lock()

    @classmethod
    def create(cls, config: GameConfig) -> RunArtifacts:
        """Create an empty run directory and persist frozen configuration."""
        run_directory = config.output_directory / config.experiment_id / config.game_id
        run_directory.mkdir(parents=True, exist_ok=False)
        artifacts = cls(run_directory)
        artifacts.write_json(
            "config.json",
            {"config": config.model_dump(mode="json"), "config_hash": config_hash(config)},
        )
        return artifacts

    @classmethod
    def open_existing(cls, run_directory: Path) -> RunArtifacts:
        """Open an existing run and continue all append-only sequence counters."""
        if not run_directory.is_dir():
            raise FileNotFoundError(f"run directory does not exist: {run_directory}")
        artifacts = cls(run_directory)
        artifacts._next_event_id = _next_jsonl_id(run_directory / "events.jsonl", "event_id")
        artifacts._next_llm_call_id = _next_jsonl_id(run_directory / "llm_calls.jsonl", "call_id")
        return artifacts

    @property
    def next_event_id(self) -> int:
        """Return the next event identifier without advancing it."""
        return self._next_event_id

    def write_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Atomically replace the resumable checkpoint document."""
        temporary = self.run_directory / "checkpoint.json.tmp"
        document = dict(checkpoint)
        document["last_event_id"] = self._next_event_id - 1
        self.write_json("checkpoint.json.tmp", document)
        temporary.replace(self.run_directory / "checkpoint.json")

    def read_checkpoint(self) -> dict[str, Any]:
        """Read the latest checkpoint document."""
        return json.loads((self.run_directory / "checkpoint.json").read_text(encoding="utf-8"))

    def append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Append one sequenced event to the immutable event stream."""
        self.append_jsonl(
            "events.jsonl",
            {
                "event_id": self._next_event_id,
                "event_type": event_type,
                "occurred_at": utc_timestamp(),
                "payload": payload,
            },
        )
        self._next_event_id += 1

    def write_result(self, result: dict[str, Any]) -> None:
        self.write_json("result.json", result)

    def append_decision(self, record: dict[str, Any]) -> None:
        self.append_jsonl("decisions.jsonl", record)

    def append_llm_call(self, record: dict[str, Any]) -> None:
        with self._llm_call_lock:
            record = dict(record)
            record["call_id"] = self._next_llm_call_id
            self._next_llm_call_id += 1
            self.append_jsonl("llm_calls.jsonl", record)

    def append_runtime(self, event_type: str, payload: dict[str, Any]) -> None:
        self.append_jsonl(
            "runtime.jsonl",
            {"event_type": event_type, "occurred_at": utc_timestamp(), "payload": payload},
        )

    def append_performance(self, record: dict[str, Any]) -> None:
        self.append_jsonl("performance.jsonl", record)

    def append_game_broadcast(self, event: GameEvent, complete_round: int) -> None:
        sentence = render_event(event, None)
        if sentence is not None:
            self.append_text("game_broadcast.txt", f"[第{complete_round}轮] {sentence}")

    def append_text(self, filename: str, line: str) -> None:
        with (self.run_directory / filename).open("a", encoding="utf-8", newline="\n") as output:
            output.write(line + "\n")

    def append_jsonl(self, filename: str, record: dict[str, Any]) -> None:
        with (self.run_directory / filename).open("a", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def write_json(self, filename: str, document: dict[str, Any]) -> None:
        with (self.run_directory / filename).open("w", encoding="utf-8", newline="\n") as output:
            json.dump(document, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")


def _next_jsonl_id(path: Path, field: str) -> int:
    if not path.exists():
        return 1
    expected = 1
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON in {path.name}:{line_number}") from error
        value = record.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value != expected:
            raise ValueError(f"non-contiguous {field} in {path.name}:{line_number}")
        expected += 1
    return expected
