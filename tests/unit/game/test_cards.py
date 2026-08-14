from pathlib import Path

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.commands import Mortgage, RollDice
from monopoly_agent_battle.domain.models import (
    JailStatus,
    OngoingEffect,
    OngoingEffectKind,
    TurnPhase,
)
from monopoly_agent_battle.game.engine import GameEngine


def make_engine(tmp_path: Path, cash: int = 1500) -> GameEngine:
    return GameEngine(
        GameConfig(
            game_id="card-game",
            experiment_id="card-experiment",
            seed=1,
            players=(
                PlayerConfig(player_id="a", seat=1),
                PlayerConfig(player_id="b", seat=2),
                PlayerConfig(player_id="c", seat=3),
            ),
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


def test_community_move_to_go_uses_settlement_queue_and_disables_build(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.community_chest_draw_pile = ["community-go"]
    engine.state.players["a"].position = 15
    set_dice(engine, [1, 1])

    events = engine.execute(RollDice("a"))

    assert engine.state.players["a"].position == 0
    assert engine.state.players["a"].cash == 1700
    assert engine.state.settlement_operations == []
    assert [event.event_type for event in events].count("player_moved") == 2
    assert any(
        event.event_type == "settlement_operation_completed" and event.payload["kind"] == "move"
        for event in events
    )
    assert engine.state.community_chest_discard_pile == ["community-go"]


def test_community_move_to_go_preserves_extra_roll_after_double(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.community_chest_draw_pile = ["community-go"]
    engine.state.players["a"].position = 15
    set_dice(engine, [1, 1])

    engine.execute(RollDice("a"))

    assert engine.state.players["a"].position == 0
    assert engine.state.turn_phase is TurnPhase.ROLLING


def test_rent_waiver_is_automatic_and_preserves_extra_roll_after_double(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    engine.state.players["a"].rent_waivers = 1
    engine.state.players["b"].properties.add(3)
    engine.state.properties[3].owner_id = "b"
    set_dice(engine, [1, 1])

    events = engine.execute(RollDice("a"))

    assert engine.state.players["a"].rent_waivers == 0
    assert engine.state.players["a"].cash == 1500
    assert engine.state.players["b"].cash == 1500
    assert engine.state.settlement_operations == []
    assert engine.state.turn_phase is TurnPhase.ROLLING
    assert any(event.event_type == "rent_waiver_used" for event in events)


def test_rent_waiver_is_not_consumed_when_rent_is_frozen(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    engine.state.players["a"].rent_waivers = 1
    engine.state.players["b"].properties.add(3)
    engine.state.properties[3].owner_id = "b"
    engine.state.ongoing_effects.append(
        OngoingEffect(
            kind=OngoingEffectKind.RENT_FREEZE,
            source_player_id="b",
            remaining_turns=1,
            activation_turn=0,
            color_group="brown",
        )
    )
    set_dice(engine, [1, 1])

    events = engine.execute(RollDice("a"))

    assert engine.state.players["a"].rent_waivers == 1
    assert engine.state.players["a"].cash == 1500
    assert engine.state.players["b"].cash == 1500
    assert any(event.event_type == "rent_frozen" for event in events)
    assert not any(event.event_type == "rent_waiver_used" for event in events)


def test_community_cash_card_is_drawn_discarded_and_resolved(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.community_chest_draw_pile = ["community-bank-error"]
    engine.state.players["a"].position = 0
    set_dice(engine, [1, 1])

    events = engine.execute(RollDice("a"))

    assert engine.state.players["a"].cash == 1700
    assert engine.state.community_chest_discard_pile == ["community-bank-error"]
    assert engine.state.settlement_operations == []
    assert {event.event_type for event in events} >= {
        "card_drawn",
        "cash_received",
        "card_discarded",
    }


def test_community_get_out_of_jail_card_is_held_outside_deck(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.community_chest_draw_pile = ["community-jail-free"]
    engine.state.players["a"].position = 0
    set_dice(engine, [1, 1])

    engine.execute(RollDice("a"))

    assert engine.state.players["a"].community_get_out_of_jail_cards == ["community-jail-free"]
    assert engine.state.community_chest_discard_pile == []
    assert engine.state.settlement_operations == []


def test_birthday_blocks_noncurrent_payer_then_resumes_cardholder(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.community_chest_draw_pile = ["community-birthday"]
    engine.state.players["a"].position = 0
    engine.state.players["b"].cash = 5
    engine.state.players["b"].properties.add(5)
    engine.state.properties[5].owner_id = "b"
    set_dice(engine, [1, 1])

    engine.execute(RollDice("a"))

    assert engine.state.current_player_id == "b"
    assert engine.state.turn_phase is TurnPhase.PAYMENT_RESOLUTION
    events = engine.execute(Mortgage("b", 5))

    assert engine.state.current_player_id == "a"
    assert engine.state.turn_phase is TurnPhase.ASSET_MANAGEMENT
    assert engine.state.players["a"].cash == 1520
    assert engine.state.players["b"].cash == 195
    assert engine.state.players["c"].cash == 1490
    assert engine.state.settlement_operations == []
    assert any(event.event_type == "payment_made" for event in events)


def test_birthday_bankruptcy_keeps_later_payers_and_restores_cardholder_turn(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.state.community_chest_draw_pile = ["community-birthday"]
    engine.state.players["a"].position = 0
    engine.state.players["b"].cash = 5
    set_dice(engine, [1, 1])

    events = engine.execute(RollDice("a"))

    assert engine.state.players["b"].bankrupt
    assert engine.state.players["a"].cash == 1510
    assert engine.state.players["c"].cash == 1490
    assert engine.state.settlement_operations == []
    assert engine.state.current_player_id == "a"
    assert engine.state.turn_phase is TurnPhase.ASSET_MANAGEMENT
    cancelled = [event for event in events if event.event_type == "settlement_operation_cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].payload["reason"] == "payer_bankrupt"
    assert any(
        event.event_type == "payment_made" and event.payload["payer_id"] == "c" for event in events
    )


def test_community_jail_card_sends_player_to_jail(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.community_chest_draw_pile = ["community-jail"]
    engine.state.players["a"].position = 0
    set_dice(engine, [1, 1])

    engine.execute(RollDice("a"))

    assert engine.state.players["a"].position == 10
    assert engine.state.players["a"].jail_status is JailStatus.WAITING
    assert engine.state.turn_phase is TurnPhase.TURN_COMPLETE
