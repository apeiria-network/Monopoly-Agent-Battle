from copy import deepcopy
from pathlib import Path

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.commands import (
    DeclareBankruptcy,
    DiscardChanceCard,
    EndTurn,
    ResolveRent,
    RollDice,
    UseChanceCard,
)
from monopoly_agent_battle.domain.models import (
    GameEvent,
    JailStatus,
    OngoingEffectKind,
    TurnPhase,
)
from monopoly_agent_battle.game.engine import GameEngine, GameRuleError


def make_engine(tmp_path: Path) -> GameEngine:
    return GameEngine(
        GameConfig(
            game_id="chance-game",
            experiment_id="chance-experiment",
            seed=1,
            players=(
                PlayerConfig(player_id="a", seat=1),
                PlayerConfig(player_id="b", seat=2),
            ),
            max_complete_rounds=2,
            rules_version="classic-level0-v1",
            board_data_version="classic-us-40-v1",
            card_data_version="classic-cards-v1",
            output_directory=tmp_path,
        )
    )


def make_three_player_engine(tmp_path: Path) -> GameEngine:
    return GameEngine(
        GameConfig(
            game_id="chance-three-player-game",
            experiment_id="chance-experiment",
            seed=1,
            players=(
                PlayerConfig(player_id="a", seat=1),
                PlayerConfig(player_id="b", seat=2),
                PlayerConfig(player_id="c", seat=3),
            ),
            max_complete_rounds=5,
            rules_version="classic-level0-v1",
            board_data_version="classic-us-40-v1",
            card_data_version="classic-cards-v1",
            output_directory=tmp_path,
        )
    )


def give_card(engine: GameEngine, card_id: str) -> None:
    engine.state.players["a"].chance_cards.append(card_id)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT


def state_copy(engine: GameEngine):
    return deepcopy(engine.state)


def assert_rejected_atomically(
    engine: GameEngine,
    command: UseChanceCard,
    *,
    match: str,
) -> None:
    before = state_copy(engine)
    with pytest.raises(GameRuleError, match=match):
        engine.execute(command)
    assert engine.state == before


def test_rent_waiver_cards_stack_and_reject_other_players_turn(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].rent_waivers = 1
    give_card(engine, "chance-waiver")
    engine.state.players["a"].chance_cards.append("chance-waiver")

    engine.execute(UseChanceCard("a", "chance-waiver"))
    engine.execute(UseChanceCard("a", "chance-waiver"))

    assert engine.state.players["a"].rent_waivers == 5
    assert engine.state.chance_discard_pile == ["chance-waiver", "chance-waiver"]

    engine.state.players["a"].chance_cards.append("chance-waiver")
    engine.state.current_player_id = "b"
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    before = state_copy(engine)
    with pytest.raises(GameRuleError, match="command must be issued by the current player"):
        engine.execute(UseChanceCard("a", "chance-waiver"))
    assert engine.state == before


def test_bankruptcy_returns_held_cards_to_their_decks(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    player = engine.state.players["a"]
    player.cash = 0
    player.chance_cards.append("chance-build")
    player.community_get_out_of_jail_cards.append("community-jail-free")
    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.PAYMENT_RESOLUTION
    events: list[GameEvent] = []
    engine._queue_payment(  # pyright: ignore[reportPrivateUsage]
        player,
        1,
        None,
        "test_bankruptcy",
        TurnPhase.ASSET_MANAGEMENT,
        None,
        events,
    )
    engine._drain_settlement_operations(events)  # pyright: ignore[reportPrivateUsage]

    events.extend(engine.execute(DeclareBankruptcy("a")))

    assert player.bankrupt is True
    assert player.chance_cards == []
    assert player.community_get_out_of_jail_cards == []
    assert engine.state.chance_discard_pile == ["chance-build"]
    assert engine.state.community_chest_discard_pile == ["community-jail-free"]
    assert {event.event_type for event in events} >= {"card_discarded", "player_bankrupt"}


def test_chance_deck_reshuffles_its_discard_pile_before_drawing(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 5
    engine.state.chance_draw_pile = []
    engine.state.chance_discard_pile = ["chance-waiver"]
    engine.random.shuffle = lambda cards: None  # type: ignore[method-assign]

    events = engine.execute(RollDice("a"))

    assert engine.state.players["a"].chance_cards == ["chance-waiver"]
    assert engine.state.chance_draw_pile == []
    assert engine.state.chance_discard_pile == []
    assert any(event.event_type == "card_drawn" for event in events)


def test_community_deck_reshuffles_its_discard_pile_before_drawing(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 0
    engine.state.community_chest_draw_pile = []
    engine.state.community_chest_discard_pile = ["community-bank-error"]
    engine.random.shuffle = lambda cards: None  # type: ignore[method-assign]
    engine.random.randint = lambda _low, _high: 1  # type: ignore[method-assign]

    events = engine.execute(RollDice("a"))

    assert engine.state.players["a"].cash == 1700
    assert engine.state.community_chest_draw_pile == []
    assert engine.state.community_chest_discard_pile == ["community-bank-error"]
    assert any(event.event_type == "card_drawn" for event in events)


def test_rent_waiver_card_is_discarded_after_use(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    give_card(engine, "chance-waiver")

    events = engine.execute(UseChanceCard("a", "chance-waiver"))

    assert engine.state.players["a"].rent_waivers == 2
    assert engine.state.players["a"].chance_cards == []
    assert engine.state.chance_discard_pile == ["chance-waiver"]
    assert {event.event_type for event in events} >= {"chance_card_used", "card_discarded"}


def test_freeze_precedes_surge_and_surge_survives_freeze_expiry(tmp_path: Path) -> None:
    engine = make_three_player_engine(tmp_path)
    engine.state.players["a"].position = 20
    assign_street(engine, "b", 23)
    give_card(engine, "chance-surge")
    engine.execute(UseChanceCard("a", "chance-surge", target_color_group="red"))
    give_card(engine, "chance-freeze")
    engine.execute(UseChanceCard("a", "chance-freeze", target_color_group="red"))

    engine.state.players["c"].cash = 1500
    events = land_on(engine, "c", 23, (1, 2))
    assert engine.state.players["c"].cash == 1500
    assert engine.state.players["b"].cash == 1500
    assert not any(event.event_type == "payment_made" for event in events)

    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.advance_turn()
    engine.advance_turn()
    engine.advance_turn()
    engine.advance_turn()
    engine.advance_turn()
    engine.advance_turn()
    engine.advance_turn()
    assert any(
        effect.kind is OngoingEffectKind.RENT_SURGE for effect in engine.state.ongoing_effects
    )
    assert not any(
        effect.kind is OngoingEffectKind.RENT_FREEZE for effect in engine.state.ongoing_effects
    )

    events = land_on(engine, "c", 23, (1, 2))
    assert engine.state.players["c"].cash == 1464
    assert engine.state.players["b"].cash == 1536
    assert any(event.event_type == "payment_made" for event in events)


def test_color_effect_follows_property_after_card_purchase(tmp_path: Path) -> None:
    engine = make_three_player_engine(tmp_path)
    engine.state.players["a"].position = 22
    engine.state.players["a"].cash = 500
    assign_street(engine, "b", 23)
    give_card(engine, "chance-surge")
    engine.execute(UseChanceCard("a", "chance-surge", target_color_group="red"))
    give_card(engine, "chance-buy")
    engine.execute(UseChanceCard("a", "chance-buy", target_position=23))

    events = land_on(engine, "c", 23, (1, 2))
    assert engine.state.players["a"].cash == 206
    assert engine.state.players["c"].cash == 1464
    assert any(event.event_type == "payment_made" for event in events)


def test_alliance_receives_no_share_when_freeze_suppresses_rent(tmp_path: Path) -> None:
    engine = make_three_player_engine(tmp_path)
    engine.state.players["a"].position = 20
    engine.state.players["b"].position = 22
    assign_street(engine, "b", 23)
    give_card(engine, "chance-alliance")
    engine.execute(UseChanceCard("a", "chance-alliance", target_player_id="b"))
    give_card(engine, "chance-freeze")
    engine.execute(UseChanceCard("a", "chance-freeze", target_color_group="red"))

    events = land_on(engine, "c", 23, (1, 2))
    assert engine.state.players["a"].cash == 1500
    assert engine.state.players["b"].cash == 1500
    assert engine.state.players["c"].cash == 1500
    assert not any(event.event_type == "payment_made" for event in events)


@pytest.mark.parametrize(
    ("card_id", "player_position", "target_position"),
    [
        ("chance-steal", 10, 17),
        ("chance-tax", 20, 29),
        ("chance-equalize", 10, 16),
        ("chance-jail", 10, 20),
        ("chance-alliance", 10, 14),
    ],
)
def test_player_target_cards_reject_out_of_range_atomically(
    tmp_path: Path, card_id: str, player_position: int, target_position: int
) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = player_position
    engine.state.players["b"].position = target_position
    give_card(engine, card_id)

    assert_rejected_atomically(
        engine,
        UseChanceCard("a", card_id, target_player_id="b"),
        match="target player is not legal",
    )


@pytest.mark.parametrize(
    ("card_id", "position", "color_group"),
    [
        ("chance-angel", 0, "light_blue"),
        ("chance-monster", 10, "yellow"),
        ("chance-surge", 0, "light_blue"),
        ("chance-freeze", 10, "yellow"),
    ],
)
def test_color_cards_reject_out_of_range_atomically(
    tmp_path: Path, card_id: str, position: int, color_group: str
) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = position
    give_card(engine, card_id)

    assert_rejected_atomically(
        engine,
        UseChanceCard("a", card_id, target_color_group=color_group),
        match="target color group is out of range",
    )


def test_jail_card_resets_progress_without_go_salary(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 30
    engine.state.players["b"].position = 39
    engine.state.players["b"].cash = 500
    engine.state.players["b"].jail_roll_attempts = 2
    give_card(engine, "chance-jail")

    engine.execute(UseChanceCard("a", "chance-jail", target_player_id="b"))

    assert engine.state.players["b"].position == 10
    assert engine.state.players["b"].jail_status is JailStatus.WAITING
    assert engine.state.players["b"].jail_roll_attempts == 0
    assert engine.state.players["b"].cash == 500


def test_jail_card_requires_a_player_in_range(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    give_card(engine, "chance-jail")
    engine.state.players["b"].position = 9

    engine.execute(UseChanceCard("a", "chance-jail", target_player_id="b"))

    assert engine.state.players["b"].position == 10
    assert engine.state.players["b"].jail_status is JailStatus.WAITING


def test_invalid_chance_target_does_not_consume_card(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    give_card(engine, "chance-jail")
    engine.state.players["b"].position = 10

    with pytest.raises(GameRuleError, match="target player is not legal"):
        engine.execute(UseChanceCard("a", "chance-jail", target_player_id="b"))

    assert engine.state.players["a"].chance_cards == ["chance-jail"]
    assert engine.state.chance_discard_pile == []


def test_angel_hotel_cap_and_monster_level_transitions(tmp_path: Path) -> None:
    engine = make_three_player_engine(tmp_path)
    engine.state.players["a"].position = 20
    assign_street(engine, "a", 21, level=4)
    assign_street(engine, "b", 23, level=5)
    give_card(engine, "chance-angel")

    engine.execute(UseChanceCard("a", "chance-angel", target_color_group="red"))

    assert engine.state.properties[21].building_level == 5
    assert engine.state.properties[23].building_level == 5
    assert engine.state.properties[24].owner_id is None
    assert engine.state.properties[24].building_level == 0

    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.properties[21].building_level = 3
    engine.state.properties[23].building_level = 1
    assign_street(engine, "c", 24, level=5)
    give_card(engine, "chance-monster")
    engine.execute(UseChanceCard("a", "chance-monster", target_color_group="red"))

    assert engine.state.properties[21].building_level == 2
    assert engine.state.properties[23].building_level == 0
    assert engine.state.properties[24].building_level == 4


def test_angel_and_monster_change_only_target_color_group(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    engine.state.properties[3].owner_id = "b"
    engine.state.players["b"].properties.add(3)
    give_card(engine, "chance-angel")

    engine.execute(UseChanceCard("a", "chance-angel", target_color_group="brown"))

    assert engine.state.properties[1].building_level == 1
    assert engine.state.properties[3].building_level == 1
    give_card(engine, "chance-monster")
    engine.execute(UseChanceCard("a", "chance-monster", target_color_group="brown"))
    assert engine.state.properties[1].building_level == 0
    assert engine.state.properties[3].building_level == 0


def test_equalize_cash_even_total_is_conserved(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].cash = 100
    engine.state.players["b"].cash = 300
    engine.state.players["b"].position = 5
    give_card(engine, "chance-equalize")

    events = engine.execute(UseChanceCard("a", "chance-equalize", target_player_id="b"))

    assert engine.state.players["a"].cash == 200
    assert engine.state.players["b"].cash == 200
    assert not any(event.event_type == "cash_rounding_adjusted" for event in events)


def test_equalize_cash_rounds_both_shares_and_records_bank_adjustment(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].cash = 100
    engine.state.players["b"].cash = 101
    engine.state.players["b"].position = 5
    give_card(engine, "chance-equalize")

    events = engine.execute(UseChanceCard("a", "chance-equalize", target_player_id="b"))

    assert engine.state.players["a"].cash == 101
    assert engine.state.players["b"].cash == 101
    adjustment = next(event for event in events if event.event_type == "cash_rounding_adjusted")
    assert adjustment.payload == {"reason": "chance-equalize", "amount": 1, "source": "bank"}
    assert engine.state.chance_discard_pile == ["chance-equalize"]


def test_nuclear_card_resets_streets_around_special_center(tmp_path: Path) -> None:
    engine = make_three_player_engine(tmp_path)
    engine.state.players["a"].position = 9
    assign_street(engine, "b", 9, level=1)
    assign_street(engine, "c", 11, level=1)
    give_card(engine, "chance-nuclear")
    engine.random.randint = lambda _low, _high: 1  # type: ignore[method-assign]

    engine.execute(UseChanceCard("a", "chance-nuclear"))

    for position in (9, 11):
        assert engine.state.properties[position].owner_id is None
        assert engine.state.properties[position].building_level == 0
        assert engine.state.properties[position].mortgaged is False
    assert 9 not in engine.state.players["b"].properties
    assert 11 not in engine.state.players["c"].properties


def test_nuclear_card_rejects_wrong_phase_before_rolling_die(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].chance_cards.append("chance-nuclear")
    calls = 0

    def count_die(_low: int, _high: int) -> int:
        nonlocal calls
        calls += 1
        return 1

    engine.random.randint = count_die  # type: ignore[method-assign]
    before = state_copy(engine)
    with pytest.raises(GameRuleError, match="asset management phase"):
        engine.execute(UseChanceCard("a", "chance-nuclear"))

    assert calls == 0
    assert engine.state == before


def test_nuclear_card_resets_only_streets_and_removes_ownership(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    engine.state.properties[1].owner_id = "b"
    engine.state.properties[1].building_level = 2
    engine.state.players["b"].properties.add(1)
    give_card(engine, "chance-nuclear")
    engine.random.randint = lambda _low, _high: 1  # type: ignore[method-assign]

    engine.execute(UseChanceCard("a", "chance-nuclear"))

    assert engine.state.properties[1].owner_id is None
    assert engine.state.properties[1].building_level == 0
    assert 1 not in engine.state.players["b"].properties


def assign_street(engine: GameEngine, player_id: str, position: int, *, level: int = 0) -> None:
    engine.state.properties[position].owner_id = player_id
    engine.state.properties[position].building_level = level
    engine.state.players[player_id].properties.add(position)


@pytest.mark.parametrize(
    ("caster_cash", "target_cash", "expected_tax"),
    [(500, 3400, 1190), (100, 101, 35)],
)
def test_tax_card_acceptance_amounts(
    tmp_path: Path,
    caster_cash: int,
    target_cash: int,
    expected_tax: int,
) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 20
    engine.state.players["b"].position = 22
    engine.state.players["a"].cash = caster_cash
    engine.state.players["b"].cash = target_cash
    give_card(engine, "chance-tax")

    engine.execute(UseChanceCard("a", "chance-tax", target_player_id="b"))

    assert engine.state.players["a"].cash == caster_cash + expected_tax
    assert engine.state.players["b"].cash == target_cash - expected_tax
    assert engine.state.chance_discard_pile == ["chance-tax"]


def test_tax_and_vacate_cards_transfer_cash_and_reset_property(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    engine.state.players["b"].position = 3
    engine.state.players["b"].cash = 100
    give_card(engine, "chance-tax")

    engine.execute(UseChanceCard("a", "chance-tax", target_player_id="b"))

    assert engine.state.players["a"].cash == 1535
    assert engine.state.players["b"].cash == 65
    give_card(engine, "chance-vacate")
    assign_street(engine, "b", 3)

    engine.execute(UseChanceCard("a", "chance-vacate", target_position=3))

    assert engine.state.players["b"].cash == 125
    assert engine.state.properties[3].owner_id is None
    assert 3 not in engine.state.players["b"].properties


def test_vacate_rejects_mortgaged_street_atomically(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 15
    assign_street(engine, "b", 16)
    engine.state.properties[16].mortgaged = True
    give_card(engine, "chance-vacate")

    assert_rejected_atomically(
        engine,
        UseChanceCard("a", "chance-vacate", target_position=16),
        match="vacant unmortgaged street",
    )


def test_swap_property_requires_two_eligible_streets(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    assign_street(engine, "a", 1)
    assign_street(engine, "b", 3)
    give_card(engine, "chance-swap-property")

    engine.execute(
        UseChanceCard("a", "chance-swap-property", target_position=3, secondary_target_position=1)
    )

    assert engine.state.properties[1].owner_id == "b"
    assert engine.state.properties[3].owner_id == "a"
    assert engine.state.players["a"].properties == {3}
    assert engine.state.players["b"].properties == {1}


def test_swap_property_rejects_when_caster_has_no_vacant_street(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 10
    assign_street(engine, "a", 9, level=1)
    assign_street(engine, "b", 13)
    give_card(engine, "chance-swap-property")

    assert_rejected_atomically(
        engine,
        UseChanceCard("a", "chance-swap-property", target_position=13, secondary_target_position=9),
        match="vacant and unmortgaged",
    )


def test_swap_buildings_exchanges_hotel_and_empty_street(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 10
    assign_street(engine, "a", 11, level=5)
    assign_street(engine, "b", 8)
    give_card(engine, "chance-swap-buildings")

    engine.execute(
        UseChanceCard("a", "chance-swap-buildings", target_position=8, secondary_target_position=11)
    )

    assert engine.state.properties[11].building_level == 0
    assert engine.state.properties[8].building_level == 5
    assert engine.state.properties[11].owner_id == "a"
    assert engine.state.properties[8].owner_id == "b"


def test_swap_buildings_allows_unrestricted_owned_second_street(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    assign_street(engine, "b", 3, level=2)
    assign_street(engine, "a", 39, level=5)
    give_card(engine, "chance-swap-buildings")

    engine.execute(
        UseChanceCard("a", "chance-swap-buildings", target_position=3, secondary_target_position=39)
    )

    assert engine.state.properties[3].building_level == 5
    assert engine.state.properties[39].building_level == 2
    assert engine.state.properties[3].owner_id == "b"
    assert engine.state.properties[39].owner_id == "a"


def test_swap_buildings_rejects_non_street_atomically(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 10
    assign_street(engine, "a", 9, level=1)
    engine.state.properties[5].owner_id = "b"
    engine.state.players["b"].properties.add(5)
    give_card(engine, "chance-swap-buildings")

    assert_rejected_atomically(
        engine,
        UseChanceCard("a", "chance-swap-buildings", target_position=5, secondary_target_position=9),
        match="target must be a street",
    )


def test_buy_property_acceptance_price_and_range_rejection(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 10
    engine.state.players["a"].cash = 500
    engine.state.players["b"].cash = 100
    assign_street(engine, "b", 11)
    give_card(engine, "chance-buy")

    engine.execute(UseChanceCard("a", "chance-buy", target_position=11))

    assert engine.state.players["a"].cash == 290
    assert engine.state.players["b"].cash == 310
    assert engine.state.properties[11].owner_id == "a"
    assert engine.state.properties[11].building_level == 0
    assert engine.state.properties[11].mortgaged is False

    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 10
    assign_street(engine, "b", 13)
    give_card(engine, "chance-buy")
    assert_rejected_atomically(
        engine,
        UseChanceCard("a", "chance-buy", target_position=13),
        match="target street is out of range",
    )


def test_buy_property_returns_card_when_cash_is_insufficient(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 3
    engine.state.players["a"].cash = 89
    assign_street(engine, "b", 3)
    give_card(engine, "chance-buy")

    with pytest.raises(GameRuleError, match="insufficient cash"):
        engine.execute(UseChanceCard("a", "chance-buy", target_position=3))

    assert engine.state.turn_phase is TurnPhase.ASSET_MANAGEMENT
    assert engine.state.players["a"].chance_cards == ["chance-buy"]
    assert engine.state.chance_discard_pile == []
    assert engine.state.players["a"].cash == 89
    assert engine.state.players["b"].cash == 1500
    assert engine.state.properties[3].owner_id == "b"
    assert engine.state.settlement_operations == []


def test_buy_property_and_build_card_change_state_without_normal_build_limits(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 3
    engine.state.players["a"].cash = 100
    assign_street(engine, "b", 3)
    give_card(engine, "chance-buy")

    engine.execute(UseChanceCard("a", "chance-buy", target_position=3))

    assert engine.state.players["a"].cash == 10
    assert engine.state.players["b"].cash == 1590
    assert engine.state.properties[3].owner_id == "a"
    give_card(engine, "chance-build")

    engine.execute(UseChanceCard("a", "chance-build", target_position=3))

    assert engine.state.properties[3].building_level == 1
    assert engine.state.properties[3].mortgaged is False


def test_build_card_upgrades_four_houses_to_hotel_for_free(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 10
    engine.state.players["a"].cash = 200
    assign_street(engine, "a", 13, level=4)
    give_card(engine, "chance-build")

    engine.execute(UseChanceCard("a", "chance-build", target_position=13))

    assert engine.state.properties[13].building_level == 5
    assert engine.state.players["a"].cash == 200
    assert engine.state.chance_discard_pile == ["chance-build"]


def test_build_card_rejects_when_no_owned_street_is_available(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 11
    assign_street(engine, "b", 8)
    assign_street(engine, "b", 9)
    assign_street(engine, "b", 13)
    assign_street(engine, "b", 14)
    give_card(engine, "chance-build")

    assert_rejected_atomically(
        engine,
        UseChanceCard("a", "chance-build", target_position=13),
        match="target must be an owned street",
    )


def test_build_card_rejects_mortgaged_street_without_consuming_card(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    assign_street(engine, "a", 3)
    engine.state.properties[3].mortgaged = True
    give_card(engine, "chance-build")

    with pytest.raises(GameRuleError, match="must be unmortgaged"):
        engine.execute(UseChanceCard("a", "chance-build", target_position=3))

    assert engine.state.properties[3].building_level == 0
    assert engine.state.properties[3].mortgaged is True
    assert engine.state.players["a"].chance_cards == ["chance-build"]
    assert engine.state.chance_discard_pile == []
    assert engine.state.settlement_operations == []


def test_build_card_rejects_hotel_without_consuming_card(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    assign_street(engine, "a", 3, level=5)
    give_card(engine, "chance-build")

    with pytest.raises(GameRuleError, match="already has a hotel"):
        engine.execute(UseChanceCard("a", "chance-build", target_position=3))

    assert engine.state.players["a"].chance_cards == ["chance-build"]


def test_steal_card_transfers_selected_card_only_after_successful_die(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["b"].position = 6
    engine.state.players["b"].chance_cards.append("chance-build")
    give_card(engine, "chance-steal")
    engine.random.randint = lambda _low, _high: 4  # type: ignore[method-assign]

    events = engine.execute(
        UseChanceCard("a", "chance-steal", target_player_id="b", stolen_card_id="chance-build")
    )

    assert engine.state.players["a"].chance_cards == ["chance-build"]
    assert engine.state.players["b"].chance_cards == []
    assert engine.state.chance_discard_pile == ["chance-steal"]
    assert {event.event_type for event in events} >= {"card_die_rolled", "chance_card_stolen"}


def test_steal_card_failure_keeps_the_card_in_hand(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["b"].position = 6
    engine.state.players["b"].chance_cards.append("chance-build")
    give_card(engine, "chance-steal")
    engine.random.randint = lambda _low, _high: 3  # type: ignore[method-assign]

    engine.execute(
        UseChanceCard("a", "chance-steal", target_player_id="b", stolen_card_id="chance-build")
    )

    assert engine.state.players["a"].chance_cards == ["chance-steal"]
    assert engine.state.players["b"].chance_cards == ["chance-build"]
    assert engine.state.chance_discard_pile == []


def test_ongoing_color_effects_reset_and_expire_after_source_turns(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.config = engine.config.model_copy(update={"max_complete_rounds": 5})
    engine.state.players["a"].position = 1
    give_card(engine, "chance-surge")

    engine.execute(UseChanceCard("a", "chance-surge", target_color_group="brown"))

    effect = engine.state.ongoing_effects[0]
    assert effect.kind is OngoingEffectKind.RENT_SURGE
    assert effect.remaining_turns == 3
    engine.advance_turn()
    engine.advance_turn()
    engine.advance_turn()
    engine.advance_turn()
    engine.advance_turn()
    engine.advance_turn()
    engine.advance_turn()

    assert engine.state.ongoing_effects == []


def land_on(
    engine: GameEngine, player_id: str, position: int, dice: tuple[int, int]
) -> list[GameEvent]:
    player = engine.state.players[player_id]
    player.position = (position - sum(dice)) % 40
    engine.state.current_player_id = player_id
    engine.state.turn_phase = TurnPhase.ROLLING
    values = iter(dice)
    engine.random.randint = lambda _low, _high: next(values)  # type: ignore[method-assign]
    return engine.execute(RollDice(player_id))


def test_rent_waiver_can_be_used_or_declined(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    assign_street(engine, "b", 3)
    engine.state.players["a"].rent_waivers = 2

    events = land_on(engine, "a", 3, (1, 2))

    assert engine.state.turn_phase is TurnPhase.CARD_RESOLUTION
    assert {event.event_type for event in events} >= {"rent_waiver_offered"}
    events = engine.execute(ResolveRent("a", True))
    assert engine.state.players["a"].cash == 1500
    assert engine.state.players["b"].cash == 1500
    assert engine.state.players["a"].rent_waivers == 1
    assert engine.state.turn_phase is TurnPhase.ASSET_MANAGEMENT
    assert {event.event_type for event in events} == {"rent_waiver_used"}

    land_on(engine, "a", 3, (1, 2))
    engine.execute(ResolveRent("a", False))
    assert engine.state.players["a"].cash == 1496
    assert engine.state.players["b"].cash == 1504
    assert engine.state.players["a"].rent_waivers == 1


def test_freeze_surge_and_alliance_modify_rent(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    engine.state.players["b"].position = 1
    assign_street(engine, "a", 1)
    give_card(engine, "chance-surge")
    engine.execute(UseChanceCard("a", "chance-surge", target_color_group="brown"))

    land_on(engine, "b", 1, (3, 4))

    assert engine.state.players["a"].cash == 1504
    assert engine.state.players["b"].cash == 1696
    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    give_card(engine, "chance-freeze")
    engine.execute(UseChanceCard("a", "chance-freeze", target_color_group="brown"))
    land_on(engine, "b", 1, (3, 4))
    assert engine.state.players["a"].cash == 1504
    assert engine.state.players["b"].cash == 1896

    engine.state.ongoing_effects.clear()
    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    give_card(engine, "chance-alliance")
    engine.execute(UseChanceCard("a", "chance-alliance", target_player_id="b"))
    land_on(engine, "b", 1, (3, 4))
    assert engine.state.players["a"].cash == 1505
    assert engine.state.players["b"].cash == 2095


def test_ongoing_color_effect_resets_without_stacking(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 1
    give_card(engine, "chance-surge")
    engine.execute(UseChanceCard("a", "chance-surge", target_color_group="brown"))
    effect = engine.state.ongoing_effects[0]
    effect.remaining_turns = 1
    give_card(engine, "chance-surge")

    events = engine.execute(UseChanceCard("a", "chance-surge", target_color_group="brown"))

    assert len(engine.state.ongoing_effects) == 1
    assert effect.remaining_turns == 3
    assert {event.event_type for event in events} >= {"ongoing_effect_reset"}


def test_alliance_splits_even_rent_without_bank_adjustment(tmp_path: Path) -> None:
    engine = make_three_player_engine(tmp_path)
    engine.state.players["a"].position = 10
    engine.state.players["b"].position = 13
    assign_street(engine, "a", 1)
    give_card(engine, "chance-alliance")
    engine.execute(UseChanceCard("a", "chance-alliance", target_player_id="b"))
    engine.state.players["a"].cash = 1500
    engine.state.players["b"].cash = 1500
    engine.state.players["c"].cash = 1500

    events = land_on(engine, "c", 1, (1, 2))

    assert engine.state.players["a"].cash == 1501
    assert engine.state.players["b"].cash == 1501
    assert engine.state.players["c"].cash == 1698
    payments = [
        event
        for event in events
        if event.event_type == "payment_made" and event.payload["payer_id"] == "c"
    ]
    assert [event.payload["amount"] for event in payments] == [1, 1]
    assert not any(event.event_type == "alliance_rent_rounding_adjusted" for event in events)


def test_alliance_rounds_both_odd_rent_shares_and_bank_adjusts(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.players["a"].position = 34
    engine.state.players["b"].position = 37
    assign_street(engine, "b", 37)
    give_card(engine, "chance-alliance")
    engine.execute(UseChanceCard("a", "chance-alliance", target_player_id="b"))
    engine.state.players["a"].cash = 1500
    engine.state.players["b"].cash = 1500

    events = land_on(engine, "a", 37, (1, 2))

    assert engine.state.players["a"].cash == 1483
    assert engine.state.players["b"].cash == 1518
    assert any(event.event_type == "alliance_rent_rounding_adjusted" for event in events)


def test_hand_limit_requires_explicit_discard_before_turn_end(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.players["a"].chance_cards.extend(
        ["chance-build", "chance-buy", "chance-jail", "chance-tax", "chance-waiver"]
    )

    with pytest.raises(GameRuleError, match="hand limit"):
        engine.execute(EndTurn("a"))

    engine.execute(DiscardChanceCard("a", "chance-waiver"))
    engine.execute(EndTurn("a"))

    assert engine.state.players["a"].chance_cards == [
        "chance-build",
        "chance-buy",
        "chance-jail",
        "chance-tax",
    ]
    assert engine.state.chance_discard_pile == ["chance-waiver"]
