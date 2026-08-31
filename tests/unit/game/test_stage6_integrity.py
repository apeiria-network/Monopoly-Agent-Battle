"""Stage 6 integrity properties for deterministic Level 0 state."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.commands import EndTurn, RollDice
from monopoly_agent_battle.domain.models import (
    CardDeck,
    SettlementOperation,
    SettlementOperationKind,
    SettlementOperationStatus,
    TurnPhase,
)
from monopoly_agent_battle.game.board_data.classic_us_40 import BOARD
from monopoly_agent_battle.game.cards.classic_cards import (
    CARDS_BY_ID,
    CHANCE_CARDS,
    COMMUNITY_CHEST_CARDS,
)
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.runner import state_snapshot


def make_config(tmp_path: Path, *, seed: int = 1, players: int = 4) -> GameConfig:
    return GameConfig(
        game_id="integrity-game",
        experiment_id="integrity-experiment",
        seed=seed,
        players=tuple(
            PlayerConfig(player_id=chr(ord("a") + index), seat=index + 1)
            for index in range(players)
        ),
        max_complete_rounds=2,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )


def make_engine(tmp_path: Path, *, seed: int = 1, players: int = 4) -> GameEngine:
    return GameEngine(make_config(tmp_path, seed=seed, players=players))


def assert_ownership_bijection(engine: GameEngine) -> None:
    property_positions = {space.position for space in BOARD if space.is_property}
    assert set(engine.state.properties) == property_positions

    held_positions = [
        position for player in engine.state.players.values() for position in player.properties
    ]
    assert len(held_positions) == len(set(held_positions))

    for position, property_state in engine.state.properties.items():
        owners = {
            player.player_id
            for player in engine.state.players.values()
            if position in player.properties
        }
        expected: set[str] = set() if property_state.owner_id is None else {property_state.owner_id}
        assert owners == expected


def card_locations(engine: GameEngine) -> Counter[str]:
    locations: list[str] = [
        *engine.state.chance_draw_pile,
        *engine.state.chance_discard_pile,
        *engine.state.community_chest_draw_pile,
        *engine.state.community_chest_discard_pile,
    ]
    for player in engine.state.players.values():
        locations.extend(player.chance_cards)
        locations.extend(player.community_get_out_of_jail_cards)
    return Counter(locations)


def test_same_seed_has_identical_initial_state_and_decks(tmp_path: Path) -> None:
    first = make_engine(tmp_path, seed=2025)
    second = make_engine(tmp_path, seed=2025)

    assert first.state == second.state
    assert first.random.getstate() == second.random.getstate()


def test_same_seed_and_commands_reproduce_events_and_state(tmp_path: Path) -> None:
    first = make_engine(tmp_path, seed=2025, players=2)
    second = make_engine(tmp_path, seed=2025, players=2)

    for command in (RollDice("a"),):
        assert first.execute(command) == second.execute(command)
        assert first.state == second.state
        assert state_snapshot(first.state, "test") == state_snapshot(second.state, "test")


def test_four_player_seeded_turn_trace_is_reproducible(tmp_path: Path) -> None:
    first = make_engine(tmp_path, seed=77)
    second = make_engine(tmp_path, seed=77)

    for expected_player in ("a", "b", "c", "d"):
        assert first.state.current_player_id == expected_player
        assert second.state.current_player_id == expected_player
        while first.state.current_player_id == expected_player:
            assert first.state.turn_phase == second.state.turn_phase
            if first.state.turn_phase is TurnPhase.ROLLING:
                command = RollDice(expected_player)
            else:
                command = EndTurn(expected_player)
            assert first.execute(command) == second.execute(command)
            assert first.state == second.state


def test_card_catalog_has_32_unique_ids_and_correct_decks() -> None:
    cards = CHANCE_CARDS + COMMUNITY_CHEST_CARDS
    card_ids = [card.card_id for card in cards]

    assert len(CHANCE_CARDS) == 16
    assert len(COMMUNITY_CHEST_CARDS) == 16
    assert len(card_ids) == 32
    assert len(card_ids) == len(set(card_ids))
    assert len(CARDS_BY_ID) == 32
    assert set(CARDS_BY_ID) == set(card_ids)
    assert all(card.deck is CardDeck.CHANCE for card in CHANCE_CARDS)
    assert all(card.deck is CardDeck.COMMUNITY_CHEST for card in COMMUNITY_CHEST_CARDS)


def test_initial_card_locations_are_one_complete_partition(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    expected = Counter(card.card_id for card in CHANCE_CARDS + COMMUNITY_CHEST_CARDS)

    assert card_locations(engine) == expected


def test_ownership_bijection_holds_initially_and_after_purchase(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    assert_ownership_bijection(engine)

    engine.state.players["a"].position = 39
    engine.random.randint = lambda _low, _high: 1  # type: ignore[method-assign]
    engine.execute(RollDice("a"))

    assert_ownership_bijection(engine)
    assert engine.state.properties[1].owner_id == "a"


def set_nonproperty_holding(engine: GameEngine) -> None:
    engine.state.players["a"].properties.add(0)


def set_unowned_building(engine: GameEngine) -> None:
    engine.state.properties[1].building_level = 1


def set_unowned_mortgage(engine: GameEngine) -> None:
    engine.state.properties[1].mortgaged = True


def set_negative_cash(engine: GameEngine) -> None:
    engine.state.players["a"].cash = -1


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (set_nonproperty_holding, "non-property position"),
        (set_unowned_building, "unowned property has state"),
        (set_unowned_mortgage, "unowned property has state"),
        (set_negative_cash, "cash cannot be negative"),
    ],
)
def test_invariants_reject_invalid_core_state(
    tmp_path: Path,
    setup: Callable[[GameEngine], None],
    message: str,
) -> None:
    engine = make_engine(tmp_path)
    setup(engine)

    with pytest.raises(AssertionError, match=message):
        engine._validate_invariants()  # pyright: ignore[reportPrivateUsage]


def test_invariants_reject_ownership_mismatch(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].properties.add(1)

    with pytest.raises(AssertionError, match="property ownership mismatch"):
        engine._validate_invariants()  # pyright: ignore[reportPrivateUsage]


def test_invariants_reject_owned_property_missing_from_holdings(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.properties[1].owner_id = "a"

    with pytest.raises(AssertionError, match="owned property is absent"):
        engine._validate_invariants()  # pyright: ignore[reportPrivateUsage]


def test_invariants_reject_duplicate_settlement_operation_ids(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    operation = SettlementOperation(
        operation_id=1,
        kind=SettlementOperationKind.MOVE,
        player_id="a",
        source="test",
    )
    engine.state.settlement_operations = [operation, operation]

    with pytest.raises(AssertionError, match="operation IDs must be unique"):
        engine._validate_invariants()  # pyright: ignore[reportPrivateUsage]


def test_invariants_reject_payment_phase_without_blocked_payment(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.PAYMENT_RESOLUTION
    engine.state.settlement_operations = [
        SettlementOperation(
            operation_id=1,
            kind=SettlementOperationKind.PAYMENT,
            player_id="a",
            source="test",
            status=SettlementOperationStatus.PENDING,
            amount=100,
        )
    ]

    with pytest.raises(AssertionError, match="no blocked payment"):
        engine._validate_invariants()  # pyright: ignore[reportPrivateUsage]


def test_player_to_player_rent_keeps_total_cash_constant(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, players=2)
    engine.state.players["a"].position = 1
    engine.state.players["b"].properties.add(3)
    engine.state.properties[3].owner_id = "b"
    before = sum(player.cash for player in engine.state.players.values())
    engine.random.randint = lambda _low, _high: 1  # type: ignore[method-assign]

    engine.execute(RollDice("a"))

    after = sum(player.cash for player in engine.state.players.values())
    assert after == before


def test_player_to_bank_purchase_has_explicit_cash_event(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, players=2)
    engine.state.players["a"].position = 39
    engine.random.randint = lambda _low, _high: 1  # type: ignore[method-assign]

    events = engine.execute(RollDice("a"))
    purchase = next(event for event in events if event.event_type == "property_purchased")

    assert purchase.payload["player_id"] == "a"
    assert purchase.payload["price"] == 60
    assert engine.state.players["a"].cash == 1500 + 200 - 60
