"""Persistent artifacts for a single auditable game run."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from monopoly_agent_battle.config.loader import config_hash
from monopoly_agent_battle.config.models import GameConfig


def utc_timestamp() -> str:
    """Return an RFC 3339 UTC timestamp with microsecond precision."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class RunArtifacts:
    """Own the append-only artifacts produced during one game run."""

    def __init__(self, run_directory: Path) -> None:
        self.run_directory = run_directory
        self._next_event_id = 1

    @classmethod
    def create(cls, config: GameConfig) -> RunArtifacts:
        """Create an empty run directory and persist frozen configuration."""
        run_directory = config.output_directory / config.experiment_id / config.game_id
        run_directory.mkdir(parents=True, exist_ok=False)
        artifacts = cls(run_directory)
        artifacts.write_json(
            "config.json",
            {
                "config": config.model_dump(mode="json"),
                "config_hash": config_hash(config),
            },
        )
        return artifacts

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
        """Write the current result snapshot."""
        self.write_json("result.json", result)

    def append_decision(self, record: dict[str, Any]) -> None:
        """Append one auditable decision request, response, and execution record."""
        self.append_jsonl("decisions.jsonl", record)

    def append_runtime(self, event_type: str, payload: dict[str, Any]) -> None:
        """Append a private runtime event that must not be exposed to controllers."""
        self.append_jsonl(
            "runtime.jsonl",
            {"event_type": event_type, "occurred_at": utc_timestamp(), "payload": payload},
        )

    def append_jsonl(self, filename: str, record: dict[str, Any]) -> None:
        """Append one JSON object terminated by exactly one newline."""
        path = self.run_directory / filename
        with path.open("a", encoding="utf-8", newline="\n") as output_file:
            output_file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            output_file.write("\n")

    def write_json(self, filename: str, document: dict[str, Any]) -> None:
        """Write a formatted JSON document under the run directory."""
        path = self.run_directory / filename
        with path.open("w", encoding="utf-8", newline="\n") as output_file:
            json.dump(document, output_file, ensure_ascii=False, indent=2, sort_keys=True)
            output_file.write("\n")
