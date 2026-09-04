from pathlib import Path

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.commands import (
    EndTurn,
    Mortgage,
    PayJailFine,
    RollDice,
    SellBuilding,
    UseCommunityGetOutOfJailCard,
)
from monopoly_agent_battle.domain.models import EndReason, JailStatus, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine, GameRuleError


def make_engine(tmp_path: Path, *, player_count: int = 2, rounds: int = 2) -> GameEngine:
    players = tuple(
        PlayerConfig(player_id=f"p{seat}", seat=seat) for seat in range(1, player_count + 1)
    )
    return GameEngine(
        GameConfig(
            game_id="jail-endgame",
            experiment_id="test",
            seed=1,
            players=players,
            max_complete_rounds=rounds,
            rules_version="classic-level0-v1",
            board_data_version="classic-us-40-v1",
            card_data_version="classic-cards-v1",
            output_directory=tmp_path,
        )
    )


def set_dice(engine: GameEngine, values: list[int]) -> None:
    iterator = iter(values)
    engine.random.randint = lambda _low, _high: next(iterator)  # type: ignore[method-assign]


def test_jail_wait_turn_consumes_no_dice(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    player = engine.state.players["p1"]
    player.jail_status = JailStatus.WAITING

    events = engine.execute(RollDice("p1"))

    assert [event.event_type for event in events] == ["jail_wait_completed"]
    assert player.jail_status is JailStatus.ROLLING
    assert engine.state.turn_phase is TurnPhase.ASSET_MANAGEMENT


def test_jail_wait_turn_rejects_fine_and_card(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    player = engine.state.players["p1"]
    player.jail_status = JailStatus.WAITING
    player.community_get_out_of_jail_cards.append("community-jail-free")

    with pytest.raises(GameRuleError, match="jail fine is not payable"):
        engine.execute(PayJailFine("p1"))
    with pytest.raises(GameRuleError, match="wait turn"):
        engine.execute(UseCommunityGetOutOfJailCard("p1", "community-jail-free"))
    assert player.jail_status is JailStatus.WAITING


def test_jail_fine_returns_player_to_rolling_phase(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    player = engine.state.players["p1"]
    player.jail_status = JailStatus.ROLLING

    engine.execute(PayJailFine("p1"))

    assert player.cash == 1450
    assert player.jail_status is JailStatus.FREE
    assert engine.state.turn_phase is TurnPhase.ROLLING


def test_jail_doubles_release_moves_without_extra_roll(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    player = engine.state.players["p1"]
    player.jail_status = JailStatus.ROLLING
    set_dice(engine, [3, 3])

    engine.execute(RollDice("p1"))

    assert player.jail_status is JailStatus.FREE
    assert player.position == 6
    assert engine.state.turn_phase is TurnPhase.ASSET_MANAGEMENT


def test_third_jail_failure_can_mortgage_then_move(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    player = engine.state.players["p1"]
    player.cash = 10
    player.jail_status = JailStatus.ROLLING
    player.jail_roll_attempts = 2
    player.properties.add(5)
    engine.state.properties[5].owner_id = "p1"
    set_dice(engine, [1, 2])

    engine.execute(RollDice("p1"))
    assert engine.state.turn_phase is TurnPhase.PAYMENT_RESOLUTION
    events = engine.execute(Mortgage("p1", 5))

    assert player.jail_status is JailStatus.FREE
    assert player.position == 3
    assert engine.state.turn_phase is TurnPhase.ASSET_MANAGEMENT
    assert {event.event_type for event in events} >= {"payment_made", "player_moved"}


def test_selling_hotel_downgrades_to_four_houses(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    player = engine.state.players["p1"]
    player.properties.add(1)
    engine.state.properties[1].owner_id = "p1"
    engine.state.properties[1].building_level = 5
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT

    engine.execute(SellBuilding("p1", 1))

    assert engine.state.properties[1].building_level == 4
    assert player.cash == 1525


def test_round_limit_uses_round_snapshot(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, player_count=3, rounds=1)
    for player_id in ("p1", "p2", "p3"):
        engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
        engine.execute(EndTurn(player_id))

    assert engine.state.finished
    assert engine.result().end_reason is EndReason.ROUND_LIMIT
    assert engine.state.complete_rounds == 1


def test_last_survivor_emits_end_event_after_bankruptcy(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["p2"].bankrupt = True
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT

    events = engine.execute(EndTurn("p1"))

    assert engine.state.finished
    assert engine.result().end_reason is EndReason.LAST_SURVIVOR
    assert events[-1].event_type == "game_finished"
