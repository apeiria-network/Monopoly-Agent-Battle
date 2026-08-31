"""Stage 6 complex settlement termination-path integration tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.commands import RollDice
from monopoly_agent_battle.domain.models import EndReason, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.runner import state_snapshot


def _config(output_directory: Path, player_count: int = 3) -> GameConfig:
    return GameConfig(
        game_id="stage6-settlement-terminal",
        experiment_id="stage6-settlement-terminal",
        seed=2,
        players=tuple(
            PlayerConfig(player_id=chr(ord("a") + seat), seat=seat + 1)
            for seat in range(player_count)
        ),
        max_complete_rounds=50,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=output_directory,
    )


def _set_dice(engine: GameEngine, values: list[int]) -> None:
    iterator = iter(values)
    engine.random.randint = lambda _low, _high: next(iterator)  # type: ignore[method-assign]


def test_birthday_chain_bankruptcy_continues_and_ends_with_clean_queue(tmp_path: Path) -> None:
    config = _config(tmp_path)
    engine = GameEngine(config)
    engine.state.community_chest_draw_pile = ["community-birthday"]
    engine.state.players["a"].position = 0
    engine.state.players["b"].cash = 5
    engine.state.players["c"].cash = 5
    _set_dice(engine, [1, 1])

    events = engine.execute(RollDice("a"))

    assert engine.state.players["b"].bankrupt
    assert engine.state.players["c"].bankrupt
    assert engine.state.finished
    assert engine.state.end_reason is EndReason.LAST_SURVIVOR
    assert engine.state.settlement_operations == []
    assert sum(event.event_type == "settlement_operation_cancelled" for event in events) == 2
    assert any(event.event_type == "game_finished" for event in events)


def test_round_limit_terminal_state_has_no_pending_settlement(tmp_path: Path) -> None:
    config = _config(tmp_path, player_count=2).model_copy(update={"max_complete_rounds": 1})
    engine = GameEngine(config)
    for player_id in ("a", "b"):
        engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
        from monopoly_agent_battle.domain.commands import EndTurn

        engine.execute(EndTurn(player_id))

    assert engine.state.finished
    assert engine.state.end_reason is EndReason.ROUND_LIMIT
    assert engine.state.settlement_operations == []
    assert engine.state.turn_phase is TurnPhase.ASSET_MANAGEMENT
    snapshot: dict[str, Any] = state_snapshot(engine.state, "completed")
    assert snapshot["settlement_operations"] == []
