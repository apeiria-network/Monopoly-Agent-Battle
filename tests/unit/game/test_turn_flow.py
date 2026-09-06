from pathlib import Path

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.commands import EndTurn, Mortgage, RollDice
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine, GameRuleError


def make_engine(
    tmp_path: Path,
    cash: int = 1500,
    players: tuple[str, ...] = ("a", "b"),
) -> GameEngine:
    config = GameConfig(
        game_id="flow-game",
        experiment_id="flow-experiment",
        seed=1,
        players=tuple(
            PlayerConfig(player_id=player_id, seat=seat)
            for seat, player_id in enumerate(players, 1)
        ),
        initial_cash=cash,
        max_complete_rounds=2,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )
    return GameEngine(config)


def set_dice(engine: GameEngine, values: list[int]) -> None:
    iterator = iter(values)
    engine.random.randint = lambda _low, _high: next(iterator)  # type: ignore[method-assign]


def test_doubles_require_another_roll_before_turn_end(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 39
    set_dice(engine, [1, 1])
    engine.execute(RollDice("a"))

    assert engine.state.turn_phase is TurnPhase.ROLLING
    with pytest.raises(GameRuleError, match="turn cannot end"):
        engine.execute(EndTurn("a"))


def test_non_double_enters_asset_management_then_advances_turn(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    set_dice(engine, [1, 2])
    engine.execute(RollDice("a"))

    assert engine.state.turn_phase is TurnPhase.ASSET_MANAGEMENT
    engine.execute(EndTurn("a"))
    assert engine.state.current_player_id == "b"
    assert engine.state.turn_phase is TurnPhase.ROLLING


def test_payment_shortfall_can_be_resolved_by_mortgaging(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, cash=10)
    engine.state.properties[5].owner_id = "a"
    engine.state.players["a"].properties.add(5)
    engine.state.players["a"].position = 2
    set_dice(engine, [1, 1])

    events = engine.execute(RollDice("a"))

    assert events[-1].event_type == "payment_required"
    assert engine.state.turn_phase is TurnPhase.PAYMENT_RESOLUTION
    events = engine.execute(Mortgage("a", 5))
    assert engine.state.settlement_operations == []
    assert engine.state.turn_phase is TurnPhase.ROLLING
    assert {event.event_type for event in events} >= {"property_mortgaged", "payment_made"}


def test_insolvent_payer_is_declared_bankrupt_automatically(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, cash=10)
    engine.state.players["a"].position = 2
    set_dice(engine, [1, 1])

    events = engine.execute(RollDice("a"))

    assert engine.state.players["a"].bankrupt
    assert engine.state.finished
    assert {event.event_type for event in events} >= {"player_bankrupt", "game_finished"}


def test_mid_turn_bankruptcy_ends_turn_via_end_turn_and_game_continues(tmp_path: Path) -> None:
    """A current player bankrupt mid-turn is closed by EndTurn; others continue."""
    engine = make_engine(tmp_path, cash=1500, players=("a", "b", "c", "d"))
    # b owns Baltic (3); a sits next to it with nothing to liquidate.
    engine.state.properties[3].owner_id = "b"
    engine.state.players["b"].properties.add(3)
    engine.state.players["a"].cash = 0
    engine.state.players["a"].position = 2
    set_dice(engine, [1, 0])

    events = engine.execute(RollDice("a"))

    assert engine.state.players["a"].bankrupt
    assert not engine.state.finished
    assert engine.state.turn_phase is TurnPhase.TURN_COMPLETE
    assert engine.state.current_player_id == "a"
    assert {event.event_type for event in events} >= {"player_bankrupt"}

    # Every other command from the corpse is still rejected.
    with pytest.raises(GameRuleError, match="bankrupt player cannot act"):
        engine.execute(RollDice("a"))

    close_events = engine.execute(EndTurn("a"))

    assert engine.state.current_player_id == "b"
    assert engine.state.turn_phase is TurnPhase.ROLLING
    assert {event.event_type for event in close_events} >= {"turn_ended", "turn_started"}
    assert engine.state.completed_round_player_ids == {"a"}
    assert not engine.state.finished
