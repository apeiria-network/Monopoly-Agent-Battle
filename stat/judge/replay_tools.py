"""Replay helpers: rebuild engine states at every recorded decision point.

WHAT THIS PROVIDES
------------------
Section 6.5 requires each decision to be scored on the exact engine state that
existed when the agent chose. This module re-executes a run's recorded commands
and yields a live ``GameEngine`` positioned immediately BEFORE each command,
together with the command actually taken.

Two things it deliberately does NOT do:

* It never calls ``build_decision_request``. Section 6.5 notes that the
  request builder clones and discards a full engine per candidate purely to
  test legality; the scorer needs the post-state anyway, so candidates are
  cloned, executed once, and kept. Skipping request construction is also the
  single biggest cost saving in the section-6.7 budget.
* It does not re-verify the run. ``game/replay.py: verify_run`` already does
  that; re-running it per game would double the replay cost. The caller is
  expected to use runs that have passed verification.

DICE DISCIPLINE
---------------
Live runs consume the engine's seeded RNG, interleaved with deck reshuffles, so
a natural re-execution reproduces them exactly. That is what we do. We do NOT
force-feed recorded dice: feeding would desynchronise the shuffle stream (the
defect recorded on develop_board.md as the "replay verifier dual-track" fix).
If a replay diverges, the game is reported as unusable rather than silently
scored on a wrong state.

WHAT COUNTS AS A DECISION POINT
-------------------------------
Every recorded command is yielded. Filtering to the section-6.4 valid set
(candidates >= 2, no fallback, etc.) is the caller's job, because the candidate
count is only known once candidates are enumerated.

HOW TO READ THE OUTPUT
----------------------
``iter_decision_points`` yields ``DecisionPoint(engine, command, index,
player_id, phase, complete_rounds)``. The engine is the LIVE object being
advanced -- clone it before mutating, and do not hold references across
iterations. ``index`` is the 0-based position in the recorded command sequence
and is the stable join key against decisions.jsonl ordering.

Library only, no main.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from monopoly_agent_battle.config.models import GameConfig
from monopoly_agent_battle.domain.commands import GameCommand
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine, GameRuleError
from monopoly_agent_battle.game.replay import _command_from_record


class ReplayDivergence(RuntimeError):
    """Raised when recorded commands cannot be re-executed on a fresh engine."""


@dataclass(slots=True)
class DecisionPoint:
    """One recorded command, with the engine state that preceded it."""

    engine: GameEngine
    command: GameCommand
    index: int
    player_id: str
    phase: TurnPhase
    complete_rounds: int


def load_commands(directory: Path) -> tuple[GameConfig, list[GameCommand]]:
    """Return the frozen config and the recorded command sequence for a run."""
    config_document = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    config = GameConfig.model_validate(config_document["config"])
    commands: list[GameCommand] = []
    for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        record: dict[str, Any] = json.loads(line)
        if record.get("event_type") == "command_executed":
            commands.append(_command_from_record(record["payload"]))
    return config, commands


def iter_decision_points(directory: Path) -> Iterator[DecisionPoint]:
    """Replay a run, yielding the engine state before each recorded command."""
    config, commands = load_commands(directory)
    engine = GameEngine(config)
    for index, command in enumerate(commands):
        yield DecisionPoint(
            engine=engine,
            command=command,
            index=index,
            player_id=engine.state.current_player_id,
            phase=engine.state.turn_phase,
            complete_rounds=engine.state.complete_rounds,
        )
        try:
            engine.execute(command)
        except GameRuleError as error:  # pragma: no cover - divergence is exceptional
            raise ReplayDivergence(
                f"{directory.name}: command {index} ({type(command).__name__}) rejected: {error}"
            ) from error
