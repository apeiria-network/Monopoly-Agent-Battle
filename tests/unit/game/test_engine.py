from pathlib import Path

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.commands import Build, EndTurn, Mortgage, RedeemMortgage, RollDice
from monopoly_agent_battle.domain.models import JailStatus, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine, GameRuleError


def make_engine(tmp_path: Path, *, seed: int = 1, cash: int = 1500) -> GameEngine:
    return GameEngine(
        GameConfig(
            game_id="game",
            experiment_id="experiment",
            seed=seed,
            players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
            initial_cash=cash,
            max_complete_rounds=2,
            rules_version="classic-level0-v1",
            board_data_version="classic-us-40-v1",
            card_data_version="classic-cards-v1",
            output_directory=tmp_path,
        )
    )


def set_dice(engine: GameEngine, values: list[int]) -> None:
    iterator = iter(values)
    engine.random.randint = lambda _low, _high: next(iterator)  # type: ignore[method-assign]


def test_roll_moves_collects_go_and_forces_property_purchase(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 39
    set_dice(engine, [1, 1])

    events = engine.execute(RollDice("a"))

    assert engine.state.players["a"].position == 1
    assert engine.state.players["a"].cash == 1640
    assert engine.state.properties[1].owner_id == "a"
    assert {event.event_type for event in events} >= {"go_salary_collected", "property_purchased"}


def test_rent_uses_complete_color_group_bonus_and_mortgage_blocks_it(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    for position in (1, 3):
        engine.state.properties[position].owner_id = "a"
        engine.state.players["a"].properties.add(position)
    engine.state.players["b"].position = 39
    engine.state.current_player_id = "b"
    set_dice(engine, [1, 1])

    engine.execute(RollDice("b"))
    assert engine.state.players["a"].cash == 1504
    assert engine.state.players["b"].cash == 1696

    engine.state.players["b"].position = 39
    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.execute(Mortgage("a", 1))
    engine.execute(EndTurn("a"))
    set_dice(engine, [1, 1])
    engine.execute(RollDice("b"))
    assert engine.state.players["a"].cash == 1564
    assert engine.state.players["b"].cash == 1896


def test_build_requires_dice_landing_on_owned_street(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)

    with pytest.raises(GameRuleError, match="asset management"):
        engine.execute(Build("a", 1))

    engine.state.players["a"].position = 38
    set_dice(engine, [1, 2])
    engine.execute(RollDice("a"))
    engine.execute(Build("a", 1))
    assert engine.state.properties[1].building_level == 1


def test_third_consecutive_doubles_sends_player_to_jail(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    set_dice(engine, [2, 2, 3, 3, 4, 4])
    engine.execute(RollDice("a"))
    engine.execute(RollDice("a"))
    events = engine.execute(RollDice("a"))

    assert engine.state.players["a"].position == 10
    assert engine.state.players["a"].jail_status is JailStatus.WAITING
    assert events[-1].payload["reason"] == "third_doubles"


def test_redeem_mortgage_charges_ten_percent_interest(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.execute(Mortgage("a", 1))
    engine.execute(RedeemMortgage("a", 1))

    assert engine.state.players["a"].cash == 1494
    assert not engine.state.properties[1].mortgaged
