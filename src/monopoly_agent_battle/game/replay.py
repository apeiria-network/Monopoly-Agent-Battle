"""Replay verification for deterministic scripted game artifacts."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, cast

from monopoly_agent_battle.config.models import GameConfig
from monopoly_agent_battle.domain.commands import (
    Build,
    DeclareBankruptcy,
    DiscardChanceCard,
    EndTurn,
    Mortgage,
    PayJailFine,
    RedeemMortgage,
    ResolveRent,
    RollDice,
    SelectStolenChanceCard,
    SellBuilding,
    UseChanceCard,
    UseCommunityGetOutOfJailCard,
)
from monopoly_agent_battle.domain.models import GameEvent
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.runner import state_snapshot


class ReplayVerificationError(ValueError):
    """Raised when persisted events cannot reproduce a result snapshot."""


_NON_STATE_RESULT_KEYS = frozenset(
    {"llm_calls", "reconnect_events", "decision_fallbacks", "validity_status"}
)


def verify_run(run_directory: Path) -> None:
    """Replay persisted commands and compare their events and state snapshot."""
    config_document = _read_json(run_directory / "config.json")
    result = _read_json(run_directory / "result.json")
    records = _read_events(run_directory / "events.jsonl")
    _validate_event_ids(records)
    commands = [record for record in records if record["event_type"] == "command_executed"]
    expected_events = [
        GameEvent(record["event_type"], record["payload"])
        for record in records
        if record["event_type"] != "command_executed"
    ]
    engine = GameEngine(GameConfig.model_validate(config_document["config"]))
    dice = deque(
        value
        for event in expected_events
        if event.event_type in {"dice_rolled", "card_die_rolled"}
        for value in (
            cast(tuple[int, int], event.payload["dice"])
            if event.event_type == "dice_rolled"
            else (cast(int, event.payload["die"]),)
        )
    )
    engine.random.randint = lambda _low, _high: dice.popleft()  # type: ignore[method-assign]
    replayed: list[GameEvent] = []
    for record in commands:
        replayed.extend(engine.execute(_command_from_record(record["payload"])))
    if _canonical_events(replayed) != _canonical_events(expected_events):
        raise ReplayVerificationError("replayed events differ from events.jsonl")
    snapshot = state_snapshot(engine.state, str(result["status"]))
    state_result = {
        key: value for key, value in result.items() if key not in _NON_STATE_RESULT_KEYS
    }
    if snapshot != state_result:
        raise ReplayVerificationError("replayed state differs from result.json")


def _canonical_events(events: list[GameEvent]) -> list[tuple[str, str]]:
    return [
        (event.event_type, json.dumps(event.payload, ensure_ascii=False, sort_keys=True))
        for event in events
    ]


def _command_from_record(payload: dict[str, Any]):
    command_type = str(payload["command_type"])
    command = payload["command"]
    player_id = str(command["player_id"])
    if command_type == "RollDice":
        return RollDice(player_id)
    if command_type == "Build":
        return Build(player_id, int(command["position"]))
    if command_type == "SellBuilding":
        return SellBuilding(player_id, int(command["position"]))
    if command_type == "Mortgage":
        return Mortgage(player_id, int(command["position"]))
    if command_type == "RedeemMortgage":
        return RedeemMortgage(player_id, int(command["position"]))
    if command_type == "PayJailFine":
        return PayJailFine(player_id)
    if command_type == "ResolveRent":
        return ResolveRent(player_id, bool(command["use_waiver"]))
    if command_type == "EndTurn":
        return EndTurn(player_id)
    if command_type == "DeclareBankruptcy":
        return DeclareBankruptcy(player_id)
    if command_type == "DiscardChanceCard":
        return DiscardChanceCard(player_id, str(command["card_id"]))
    if command_type == "SelectStolenChanceCard":
        return SelectStolenChanceCard(player_id, str(command["card_id"]))
    if command_type == "UseChanceCard":
        return UseChanceCard(
            player_id,
            str(command["card_id"]),
            command.get("target_player_id"),
            command.get("target_position"),
            command.get("target_color_group"),
            command.get("secondary_target_position"),
        )
    if command_type == "UseCommunityGetOutOfJailCard":
        return UseCommunityGetOutOfJailCard(player_id, str(command["card_id"]))
    raise ReplayVerificationError(f"unsupported command type: {command_type}")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _validate_event_ids(records: list[dict[str, Any]]) -> None:
    for expected, record in enumerate(records, start=1):
        if record.get("event_id") != expected:
            raise ReplayVerificationError(f"expected event_id {expected}")
