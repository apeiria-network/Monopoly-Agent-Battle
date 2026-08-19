"""Manual test: render history broadcast for all whitelist events.

Run from the repository root:
    .venv/Scripts/python.exe tests/manual/render_history_broadcast.py

This script runs 8+ controlled scenarios to demonstrate broadcast coverage.
Each scenario is isolated and focuses on triggering specific events.
Full output is written to tests/manual/history_broadcast_report.txt

Strategy: Use direct state manipulation (learned from test_chance_cards.py)
to create precise conditions for each event type, avoiding complex phase management.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.broadcast import WHITELIST, render_event
from monopoly_agent_battle.domain.commands import (
    EndTurn,
    Mortgage,
    PayJailFine,
    RedeemMortgage,
    RollDice,
    SelectStolenChanceCard,
    SellBuilding,
    UseChanceCard,
)
from monopoly_agent_battle.domain.models import GameEvent, JailStatus, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine


class EventCollector:
    """Collect events across scenarios and track coverage."""

    def __init__(self) -> None:
        self.all_events: list[tuple[str, str, str | None, int]] = []
        self.counts: Counter[str] = Counter()

    def collect(
        self, scenario_name: str, events: list[GameEvent], viewer_id: str | None, engine: GameEngine
    ) -> None:
        """Collect and render events for a scenario."""
        round_number = engine.state.complete_rounds
        for event in events:
            broadcast = render_event(event, viewer_id)
            self.all_events.append((scenario_name, event.event_type, broadcast, round_number))
            if broadcast is not None:
                self.counts[event.event_type] += 1

    def write_report(self, path: Path) -> None:
        """Write full event log to file."""
        with open(path, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write("HISTORY BROADCAST MANUAL TEST REPORT\n")
            f.write("=" * 80 + "\n\n")

            current_scenario = None
            for scenario, _event_type, broadcast, round_num in self.all_events:
                if broadcast is None:
                    continue  # Skip exempt events entirely

                if scenario != current_scenario:
                    f.write(f"\n{'=' * 80}\n")
                    f.write(f"{scenario}\n")
                    f.write(f"{'=' * 80}\n\n")
                    current_scenario = scenario

                f.write(f"[第{round_num}轮] {broadcast}\n")

            # Coverage summary
            f.write(f"\n{'=' * 80}\n")
            f.write("EVENT COVERAGE SUMMARY\n")
            f.write(f"{'=' * 80}\n\n")

            for event_type in sorted(WHITELIST):
                count = self.counts[event_type]
                status = "[OK]" if count >= 2 else "[FAIL]"
                f.write(f"{status} {event_type:50s} {count:3d} occurrences\n")

            total = len(WHITELIST)
            covered = sum(1 for et in WHITELIST if self.counts[et] >= 2)
            f.write(f"\nCovered: {covered}/{total} events with >=2 occurrences\n")

    def verify_coverage(self, min_count: int = 2) -> bool:
        """Check if all whitelist events have >= min_count occurrences."""
        missing = [et for et in WHITELIST if self.counts[et] < min_count]
        if missing:
            print(f"\n[FAIL] Missing coverage for {len(missing)} events:")
            for et in sorted(missing):
                print(f"  - {et}: {self.counts[et]} occurrences")
            return False
        return True


def set_dice(engine: GameEngine, dice_values: list[int]) -> None:
    """Inject controlled dice sequence into engine RNG."""
    iterator = iter(dice_values)
    engine.random.randint = lambda _low, _high: next(iterator)  # type: ignore


def create_config(game_id: str, directory: str, seed: int = 42) -> GameConfig:
    """Create base config for scenarios."""
    return GameConfig(
        game_id=game_id,
        experiment_id="manual-4b",
        seed=seed,
        players=(
            PlayerConfig(player_id="a", seat=1),
            PlayerConfig(player_id="b", seat=2),
            PlayerConfig(player_id="c", seat=3),
            PlayerConfig(player_id="d", seat=4),
        ),
        initial_cash=1500,
        max_complete_rounds=50,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=Path(directory),
    )


def give_card(engine: GameEngine, player_id: str, card_id: str) -> None:
    """Inject a chance card into player's hand and set to ASSET_MANAGEMENT phase."""
    engine.state.players[player_id].chance_cards.append(card_id)
    engine.state.current_player_id = player_id
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT


def assign_property(engine: GameEngine, player_id: str, position: int, level: int = 0) -> None:
    """Assign property ownership with building level."""
    engine.state.properties[position].owner_id = player_id
    engine.state.properties[position].building_level = level
    engine.state.properties[position].mortgaged = False
    engine.state.players[player_id].properties.add(position)


def scenario_1_basic_flow(directory: str, collector: EventCollector) -> None:
    """Basic turns: dice_rolled, player_moved, turn_started, turn_ended,
    property_purchased, go_salary_collected, payment_made (rent)."""
    config = create_config("s1", directory, seed=1)
    engine = GameEngine(config)

    set_dice(
        engine,
        [
            2,
            1,  # a rolls 3, buys position 3
            5,
            4,  # b rolls 9, buys position 9
            6,
            6,  # c rolls 12 (doubles), continues
            3,
            2,  # c rolls 5 more = 17 total
            2,
            1,  # d rolls 3
            # Round 2
            6,
            6,  # a rolls 12, passes GO
            2,
            1,  # b rolls 3
            2,
            1,  # c rolls 3, lands on a's property (pays rent)
        ],
    )

    try:
        collector.collect("Scenario 1: Basic Flow", engine.execute(RollDice("a")), "a", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(EndTurn("a")), "a", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(EndTurn("d")), "d", engine)
        # Round 2
        collector.collect("Scenario 1: Basic Flow", engine.execute(RollDice("a")), "a", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(EndTurn("a")), "a", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 1: Basic Flow", engine.execute(EndTurn("c")), "c", engine)
    except Exception as e:
        print(f"  Scenario 1 error: {e}")


def scenario_2_jail_mechanics(directory: str, collector: EventCollector) -> None:
    """Jail: player_jailed (third doubles), jail_roll_failed, jail_released (doubles)."""
    config = create_config("s2", directory, seed=2)
    engine = GameEngine(config)

    # Third doubles -> jail
    set_dice(engine, [3, 3, 2, 2, 5, 5])
    try:
        collector.collect("Scenario 2: Jail", engine.execute(RollDice("a")), "a", engine)
        collector.collect("Scenario 2: Jail", engine.execute(RollDice("a")), "a", engine)
        events = engine.execute(RollDice("a"))  # Jailed
        collector.collect("Scenario 2: Jail", events, "a", engine)

        # After jail, need to advance turn manually
        engine.advance_turn()

        # Other players take turns
        set_dice(engine, [1, 2, 2, 1, 3, 2])
        collector.collect("Scenario 2: Jail", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 2: Jail", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 2: Jail", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 2: Jail", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 2: Jail", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 2: Jail", engine.execute(EndTurn("d")), "d", engine)

        # a tries to roll out (fails)
        set_dice(engine, [2, 3])
        collector.collect("Scenario 2: Jail", engine.execute(RollDice("a")), "a", engine)
        collector.collect("Scenario 2: Jail", engine.execute(EndTurn("a")), "a", engine)

        # Skip b, c, d turns
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 2: Jail", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 2: Jail", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 2: Jail", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 2: Jail", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 2: Jail", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 2: Jail", engine.execute(EndTurn("d")), "d", engine)

        # a rolls doubles (released)
        set_dice(engine, [4, 4, 1, 0])
        collector.collect("Scenario 2: Jail", engine.execute(RollDice("a")), "a", engine)
        collector.collect("Scenario 2: Jail", engine.execute(RollDice("a")), "a", engine)
        collector.collect("Scenario 2: Jail", engine.execute(EndTurn("a")), "a", engine)
    except Exception as e:
        print(f"  Scenario 2 error: {e}")


def scenario_3_jail_pay_fine(directory: str, collector: EventCollector) -> None:
    """Jail released by paying fine."""
    config = create_config("s3", directory, seed=3)
    engine = GameEngine(config)

    # Put a in jail manually
    engine.state.players["a"].jail_status = JailStatus.ROLLING
    engine.state.players["a"].position = 10
    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.ROLLING

    try:
        # Pay fine to get out
        collector.collect("Scenario 3: Jail Fine", engine.execute(PayJailFine("a")), "a", engine)
        # Must still roll to move
        set_dice(engine, [2, 3])
        collector.collect("Scenario 3: Jail Fine", engine.execute(RollDice("a")), "a", engine)
        collector.collect("Scenario 3: Jail Fine", engine.execute(EndTurn("a")), "a", engine)
    except Exception as e:
        print(f"  Scenario 3 error: {e}")


def scenario_4_building_ops(directory: str, collector: EventCollector) -> None:
    """Building: building_added (auto), building_sold, building_level_changed."""
    config = create_config("s4", directory, seed=4)
    engine = GameEngine(config)

    # Give a the brown group
    assign_property(engine, "a", 1, level=1)
    assign_property(engine, "a", 3, level=1)
    engine.state.players["a"].cash = 2000

    # Use monster card (reduces building level)
    give_card(engine, "a", "chance-monster")
    try:
        collector.collect(
            "Scenario 4: Buildings",
            engine.execute(UseChanceCard("a", "chance-monster", target_color_group="brown")),
            "a",
            engine,
        )
        collector.collect("Scenario 4: Buildings", engine.execute(EndTurn("a")), "a", engine)

        # Cycle back to a
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 4: Buildings", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 4: Buildings", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 4: Buildings", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 4: Buildings", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 4: Buildings", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 4: Buildings", engine.execute(EndTurn("d")), "d", engine)

        # a lands on own property -> auto-build
        engine.state.players["a"].position = 0
        set_dice(engine, [1, 0])
        collector.collect("Scenario 4: Buildings", engine.execute(RollDice("a")), "a", engine)

        # Sell a building
        collector.collect(
            "Scenario 4: Buildings", engine.execute(SellBuilding("a", 1)), "a", engine
        )
        collector.collect("Scenario 4: Buildings", engine.execute(EndTurn("a")), "a", engine)
    except Exception as e:
        print(f"  Scenario 4 error: {e}")


def scenario_5_chance_steal_tax(directory: str, collector: EventCollector) -> None:
    """Chance cards: card_die_rolled, chance_card_stolen, cash_tax_transferred."""
    config = create_config("s5", directory, seed=5)
    engine = GameEngine(config)

    # Setup steal card: b within range, has a card
    engine.state.players["a"].position = 0
    engine.state.players["b"].position = 5
    engine.state.players["b"].chance_cards.append("chance-build")
    engine.state.players["a"].cash = 2000

    give_card(engine, "a", "chance-steal")
    engine.random.randint = lambda _low, _high: 4  # type: ignore  # Success

    try:
        events = engine.execute(UseChanceCard("a", "chance-steal", target_player_id="b"))
        collector.collect("Scenario 5: Steal/Tax", events, "a", engine)
        events = engine.execute(SelectStolenChanceCard("a", "chance-build"))
        collector.collect("Scenario 5: Steal/Tax", events, "a", engine)
        collector.collect("Scenario 5: Steal/Tax", engine.execute(EndTurn("a")), "a", engine)

        # Cycle back
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 5: Steal/Tax", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 5: Steal/Tax", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 5: Steal/Tax", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 5: Steal/Tax", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 5: Steal/Tax", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 5: Steal/Tax", engine.execute(EndTurn("d")), "d", engine)

        # Tax card
        engine.state.players["a"].position = 20
        engine.state.players["c"].position = 22
        engine.state.players["c"].cash = 1000
        give_card(engine, "a", "chance-tax")
        events = engine.execute(UseChanceCard("a", "chance-tax", target_player_id="c"))
        collector.collect("Scenario 5: Steal/Tax", events, "a", engine)
        collector.collect("Scenario 5: Steal/Tax", engine.execute(EndTurn("a")), "a", engine)
    except Exception as e:
        print(f"  Scenario 5 error: {e}")


def scenario_6_chance_equalize_buy(directory: str, collector: EventCollector) -> None:
    """Chance: cash_equalized, property_purchased_from_player."""
    config = create_config("s6", directory, seed=6)
    engine = GameEngine(config)

    # Equalize card
    engine.state.players["a"].position = 1
    engine.state.players["b"].position = 5
    engine.state.players["a"].cash = 2000
    engine.state.players["b"].cash = 500
    give_card(engine, "a", "chance-equalize")

    try:
        events = engine.execute(UseChanceCard("a", "chance-equalize", target_player_id="b"))
        collector.collect("Scenario 6: Equalize/Buy", events, "a", engine)
        collector.collect("Scenario 6: Equalize/Buy", engine.execute(EndTurn("a")), "a", engine)

        # Cycle back
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 6: Equalize/Buy", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 6: Equalize/Buy", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 6: Equalize/Buy", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 6: Equalize/Buy", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 6: Equalize/Buy", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 6: Equalize/Buy", engine.execute(EndTurn("d")), "d", engine)

        # Buy card
        assign_property(engine, "c", 6)
        engine.state.players["a"].position = 3
        engine.state.players["a"].cash = 300
        give_card(engine, "a", "chance-buy")
        events = engine.execute(UseChanceCard("a", "chance-buy", target_position=6))
        collector.collect("Scenario 6: Equalize/Buy", events, "a", engine)
        collector.collect("Scenario 6: Equalize/Buy", engine.execute(EndTurn("a")), "a", engine)
    except Exception as e:
        print(f"  Scenario 6 error: {e}")


def scenario_7_chance_waiver_nuke(directory: str, collector: EventCollector) -> None:
    """Chance: rent_waivers_granted, rent_waiver_used, property_reset."""
    config = create_config("s7", directory, seed=7)
    engine = GameEngine(config)

    # Waiver card
    assign_property(engine, "b", 3)
    give_card(engine, "a", "chance-waiver")

    try:
        events = engine.execute(UseChanceCard("a", "chance-waiver"))
        collector.collect("Scenario 7: Waiver/Nuke", events, "a", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(EndTurn("a")), "a", engine)

        # Cycle and land on b's property to use waiver
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(EndTurn("d")), "d", engine)

        # a lands on b's property
        engine.state.players["a"].position = 0
        set_dice(engine, [1, 2])
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 7: Waiver/Nuke", events, "a", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(EndTurn("a")), "a", engine)

        # Cycle back
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 7: Waiver/Nuke", engine.execute(EndTurn("d")), "d", engine)

        # Nuclear card
        assign_property(engine, "c", 9, level=1)
        give_card(engine, "a", "chance-nuclear")
        engine.state.players["a"].position = 1
        engine.random.randint = lambda _low, _high: 1  # type: ignore
        events = engine.execute(UseChanceCard("a", "chance-nuclear"))
        collector.collect("Scenario 7: Waiver/Nuke", events, "a", engine)
    except Exception as e:
        print(f"  Scenario 7 error: {e}")


def scenario_8_chance_vacate_ongoing(directory: str, collector: EventCollector) -> None:
    """Chance: property_vacated, ongoing_effect_created, ongoing_effect_expired."""
    config = create_config("s8", directory, seed=8)
    engine = GameEngine(config)

    # Vacate card
    assign_property(engine, "b", 16)
    engine.state.players["a"].position = 15
    give_card(engine, "a", "chance-vacate")

    try:
        events = engine.execute(UseChanceCard("a", "chance-vacate", target_position=16))
        collector.collect("Scenario 8: Vacate/Ongoing", events, "a", engine)
        collector.collect("Scenario 8: Vacate/Ongoing", engine.execute(EndTurn("a")), "a", engine)

        # Cycle back
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 8: Vacate/Ongoing", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 8: Vacate/Ongoing", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 8: Vacate/Ongoing", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 8: Vacate/Ongoing", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 8: Vacate/Ongoing", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 8: Vacate/Ongoing", engine.execute(EndTurn("d")), "d", engine)

        # Alliance card (ongoing effect)
        give_card(engine, "a", "chance-alliance")
        events = engine.execute(UseChanceCard("a", "chance-alliance", target_player_id="b"))
        collector.collect("Scenario 8: Vacate/Ongoing", events, "a", engine)
        collector.collect("Scenario 8: Vacate/Ongoing", engine.execute(EndTurn("a")), "a", engine)

        # Advance 4 complete rounds to expire effect
        for _ in range(4):
            set_dice(engine, [1, 0, 1, 0, 1, 0, 1, 0])
            collector.collect(
                "Scenario 8: Vacate/Ongoing", engine.execute(RollDice("b")), "b", engine
            )
            collector.collect(
                "Scenario 8: Vacate/Ongoing", engine.execute(EndTurn("b")), "b", engine
            )
            collector.collect(
                "Scenario 8: Vacate/Ongoing", engine.execute(RollDice("c")), "c", engine
            )
            collector.collect(
                "Scenario 8: Vacate/Ongoing", engine.execute(EndTurn("c")), "c", engine
            )
            collector.collect(
                "Scenario 8: Vacate/Ongoing", engine.execute(RollDice("d")), "d", engine
            )
            collector.collect(
                "Scenario 8: Vacate/Ongoing", engine.execute(EndTurn("d")), "d", engine
            )
            collector.collect(
                "Scenario 8: Vacate/Ongoing", engine.execute(RollDice("a")), "a", engine
            )
            collector.collect(
                "Scenario 8: Vacate/Ongoing", engine.execute(EndTurn("a")), "a", engine
            )
    except Exception as e:
        print(f"  Scenario 8 error: {e}")


def scenario_9_mortgage_bankruptcy(directory: str, collector: EventCollector) -> None:
    """Mortgage: property_mortgaged, mortgage_redeemed, player_bankrupt."""
    config = create_config("s9", directory, seed=9)
    engine = GameEngine(config)

    # Setup properties
    assign_property(engine, "a", 1)
    assign_property(engine, "a", 3)
    engine.state.players["a"].cash = 500
    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT

    try:
        # Mortgage
        events = engine.execute(Mortgage("a", 1))
        collector.collect("Scenario 9: Mortgage/Bankruptcy", events, "a", engine)
        collector.collect(
            "Scenario 9: Mortgage/Bankruptcy", engine.execute(EndTurn("a")), "a", engine
        )

        # Cycle back
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 9: Mortgage/Bankruptcy", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect(
            "Scenario 9: Mortgage/Bankruptcy", engine.execute(EndTurn("b")), "b", engine
        )
        collector.collect(
            "Scenario 9: Mortgage/Bankruptcy", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect(
            "Scenario 9: Mortgage/Bankruptcy", engine.execute(EndTurn("c")), "c", engine
        )
        collector.collect(
            "Scenario 9: Mortgage/Bankruptcy", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect(
            "Scenario 9: Mortgage/Bankruptcy", engine.execute(EndTurn("d")), "d", engine
        )

        # Redeem mortgage
        engine.state.players["a"].cash = 200
        set_dice(engine, [1, 0])
        collector.collect(
            "Scenario 9: Mortgage/Bankruptcy", engine.execute(RollDice("a")), "a", engine
        )
        events = engine.execute(RedeemMortgage("a", 1))
        collector.collect("Scenario 9: Mortgage/Bankruptcy", events, "a", engine)
        collector.collect(
            "Scenario 9: Mortgage/Bankruptcy", engine.execute(EndTurn("a")), "a", engine
        )

        # Setup bankruptcy: b has low cash, lands on expensive property
        assign_property(engine, "c", 6, level=3)
        engine.state.players["b"].cash = 10
        engine.state.players["b"].position = 5

        set_dice(engine, [1, 0])
        events = engine.execute(RollDice("b"))
        collector.collect("Scenario 9: Mortgage/Bankruptcy", events, "b", engine)
    except Exception as e:
        print(f"  Scenario 9 error: {e}")


def scenario_10_card_drawn_community(directory: str, collector: EventCollector) -> None:
    """Community chest: card_drawn, cash_received."""
    config = create_config("s10", directory, seed=10)
    engine = GameEngine(config)

    # Land on community chest (position 2)
    set_dice(engine, [1, 1, 1, 1, 1, 1])

    try:
        collector.collect(
            "Scenario 10: Community Chest", engine.execute(RollDice("a")), "a", engine
        )
        collector.collect("Scenario 10: Community Chest", engine.execute(EndTurn("a")), "a", engine)
        collector.collect(
            "Scenario 10: Community Chest", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect("Scenario 10: Community Chest", engine.execute(EndTurn("b")), "b", engine)
        collector.collect(
            "Scenario 10: Community Chest", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect("Scenario 10: Community Chest", engine.execute(EndTurn("c")), "c", engine)
    except Exception as e:
        print(f"  Scenario 10 error: {e}")


def scenario_11_game_finish(directory: str, collector: EventCollector) -> None:
    """Game finish: game_finished."""
    config = create_config("s11", directory, seed=11)
    config = config.model_copy(update={"max_complete_rounds": 2})
    engine = GameEngine(config)

    try:
        # Play through to finish
        for _ in range(8):  # 2 rounds × 4 players
            set_dice(engine, [1, 0])
            player_id = engine.state.current_player_id
            collector.collect(
                "Scenario 11: Game Finish", engine.execute(RollDice(player_id)), player_id, engine
            )
            if not engine.state.finished:
                collector.collect(
                    "Scenario 11: Game Finish",
                    engine.execute(EndTurn(player_id)),
                    player_id,
                    engine,
                )
            if engine.state.finished:
                break
    except Exception as e:
        print(f"  Scenario 11 error: {e}")


def scenario_12_auto_build_skip(directory: str, collector: EventCollector) -> None:
    """Building: automatic_build_skipped_insufficient_cash."""
    config = create_config("s12", directory, seed=12)
    engine = GameEngine(config)

    # Give a the brown group but low cash
    assign_property(engine, "a", 1, level=1)
    assign_property(engine, "a", 3, level=1)
    engine.state.players["a"].cash = 10  # Too low to build
    engine.state.players["a"].position = 0

    set_dice(engine, [1, 0])
    try:
        # Land on own property, should skip build
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 12: Auto-build Skip", events, "a", engine)
        collector.collect("Scenario 12: Auto-build Skip", engine.execute(EndTurn("a")), "a", engine)

        # Do it again for 2nd occurrence
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 12: Auto-build Skip", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect("Scenario 12: Auto-build Skip", engine.execute(EndTurn("b")), "b", engine)
        collector.collect(
            "Scenario 12: Auto-build Skip", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect("Scenario 12: Auto-build Skip", engine.execute(EndTurn("c")), "c", engine)
        collector.collect(
            "Scenario 12: Auto-build Skip", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect("Scenario 12: Auto-build Skip", engine.execute(EndTurn("d")), "d", engine)

        engine.state.players["a"].position = 0
        engine.state.players["a"].cash = 5
        set_dice(engine, [1, 0])
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 12: Auto-build Skip", events, "a", engine)
    except Exception as e:
        print(f"  Scenario 12 error: {e}")


def scenario_13_repeat_coverage(directory: str, collector: EventCollector) -> None:
    """Repeat scenarios for 2nd occurrences: building_added, building_sold,
    go_salary_collected, player_jailed, property_mortgaged, mortgage_redeemed."""
    config = create_config("s13", directory, seed=13)
    engine = GameEngine(config)

    # building_added: land on own property
    assign_property(engine, "a", 1, level=1)
    assign_property(engine, "a", 3, level=1)
    engine.state.players["a"].cash = 200
    engine.state.players["a"].position = 0
    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.ROLLING

    try:
        set_dice(engine, [1, 0])
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 13: Repeat Coverage", events, "a", engine)

        # property_mortgaged - do it in ASSET_MANAGEMENT phase
        engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
        events = engine.execute(Mortgage("a", 3))
        collector.collect("Scenario 13: Repeat Coverage", events, "a", engine)

        # building_sold
        events = engine.execute(SellBuilding("a", 1))
        collector.collect("Scenario 13: Repeat Coverage", events, "a", engine)
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("a")), "a", engine)

        # Cycle back
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 13: Repeat Coverage", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("b")), "b", engine)
        collector.collect(
            "Scenario 13: Repeat Coverage", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("c")), "c", engine)
        collector.collect(
            "Scenario 13: Repeat Coverage", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("d")), "d", engine)

        # mortgage_redeemed
        engine.state.players["a"].cash = 200
        set_dice(engine, [1, 0])
        collector.collect(
            "Scenario 13: Repeat Coverage", engine.execute(RollDice("a")), "a", engine
        )
        events = engine.execute(RedeemMortgage("a", 3))
        collector.collect("Scenario 13: Repeat Coverage", events, "a", engine)
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("a")), "a", engine)

        # go_salary_collected: pass GO
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 13: Repeat Coverage", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("b")), "b", engine)
        collector.collect(
            "Scenario 13: Repeat Coverage", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("c")), "c", engine)
        collector.collect(
            "Scenario 13: Repeat Coverage", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("d")), "d", engine)

        engine.state.players["a"].position = 38
        set_dice(engine, [1, 2])
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 13: Repeat Coverage", events, "a", engine)
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("a")), "a", engine)

        # player_jailed: land on go-to-jail (position 30)
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 13: Repeat Coverage", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("b")), "b", engine)
        collector.collect(
            "Scenario 13: Repeat Coverage", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("c")), "c", engine)
        collector.collect(
            "Scenario 13: Repeat Coverage", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect("Scenario 13: Repeat Coverage", engine.execute(EndTurn("d")), "d", engine)

        engine.state.players["a"].position = 29
        set_dice(engine, [1, 0])
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 13: Repeat Coverage", events, "a", engine)
    except Exception as e:
        print(f"  Scenario 13 error: {e}")


def scenario_14_jail_fail_bankruptcy(directory: str, collector: EventCollector) -> None:
    """jail_roll_failed, player_bankrupt (2nd)."""
    config = create_config("s14", directory, seed=14)
    engine = GameEngine(config)

    # Setup jail fail
    engine.state.players["a"].jail_status = JailStatus.ROLLING
    engine.state.players["a"].position = 10
    engine.state.players["a"].jail_roll_attempts = 0
    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.ROLLING

    try:
        # Fail to roll out (twice for 2 occurrences)
        set_dice(engine, [2, 3])
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 14: Jail Fail/Bankruptcy", events, "a", engine)
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(EndTurn("a")), "a", engine
        )

        # Cycle back
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(EndTurn("b")), "b", engine
        )
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(EndTurn("c")), "c", engine
        )
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(EndTurn("d")), "d", engine
        )

        # Second jail fail
        set_dice(engine, [1, 3])
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 14: Jail Fail/Bankruptcy", events, "a", engine)
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(EndTurn("a")), "a", engine
        )

        # Setup bankruptcy (2nd occurrence)
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(EndTurn("b")), "b", engine
        )
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(EndTurn("c")), "c", engine
        )
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect(
            "Scenario 14: Jail Fail/Bankruptcy", engine.execute(EndTurn("d")), "d", engine
        )

        assign_property(engine, "d", 8, level=4)
        engine.state.players["a"].cash = 5
        engine.state.players["a"].position = 7
        engine.state.players["a"].jail_status = JailStatus.FREE

        set_dice(engine, [1, 0])
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 14: Jail Fail/Bankruptcy", events, "a", engine)
    except Exception as e:
        print(f"  Scenario 14 error: {e}")


def scenario_15_ongoing_effects(directory: str, collector: EventCollector) -> None:
    """ongoing_effect_created, ongoing_effect_expired (alliance + surge)."""
    config = create_config("s15", directory, seed=15)
    engine = GameEngine(config)

    # Alliance card
    engine.state.players["a"].position = 5
    engine.state.players["b"].position = 3
    give_card(engine, "a", "chance-alliance")

    try:
        events = engine.execute(UseChanceCard("a", "chance-alliance", target_player_id="b"))
        collector.collect("Scenario 15: Ongoing Effects", events, "a", engine)
        collector.collect("Scenario 15: Ongoing Effects", engine.execute(EndTurn("a")), "a", engine)

        # Cycle back
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 15: Ongoing Effects", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect("Scenario 15: Ongoing Effects", engine.execute(EndTurn("b")), "b", engine)
        collector.collect(
            "Scenario 15: Ongoing Effects", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect("Scenario 15: Ongoing Effects", engine.execute(EndTurn("c")), "c", engine)
        collector.collect(
            "Scenario 15: Ongoing Effects", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect("Scenario 15: Ongoing Effects", engine.execute(EndTurn("d")), "d", engine)

        # Surge card - use valid color group "brown"
        give_card(engine, "a", "chance-surge")
        events = engine.execute(UseChanceCard("a", "chance-surge", target_color_group="brown"))
        collector.collect("Scenario 15: Ongoing Effects", events, "a", engine)
        collector.collect("Scenario 15: Ongoing Effects", engine.execute(EndTurn("a")), "a", engine)

        # Advance 4 complete rounds to expire both effects
        for _ in range(4):
            set_dice(engine, [1, 0, 1, 0, 1, 0, 1, 0])
            collector.collect(
                "Scenario 15: Ongoing Effects", engine.execute(RollDice("b")), "b", engine
            )
            collector.collect(
                "Scenario 15: Ongoing Effects", engine.execute(EndTurn("b")), "b", engine
            )
            collector.collect(
                "Scenario 15: Ongoing Effects", engine.execute(RollDice("c")), "c", engine
            )
            collector.collect(
                "Scenario 15: Ongoing Effects", engine.execute(EndTurn("c")), "c", engine
            )
            collector.collect(
                "Scenario 15: Ongoing Effects", engine.execute(RollDice("d")), "d", engine
            )
            collector.collect(
                "Scenario 15: Ongoing Effects", engine.execute(EndTurn("d")), "d", engine
            )
            collector.collect(
                "Scenario 15: Ongoing Effects", engine.execute(RollDice("a")), "a", engine
            )
            collector.collect(
                "Scenario 15: Ongoing Effects", engine.execute(EndTurn("a")), "a", engine
            )
    except Exception as e:
        print(f"  Scenario 15 error: {e}")


def scenario_16_buy_stolen_vacate(directory: str, collector: EventCollector) -> None:
    """property_purchased_from_player, chance_card_stolen (2nd), property_vacated (2nd)."""
    config = create_config("s16", directory, seed=16)
    engine = GameEngine(config)

    # Buy card: property_purchased_from_player (must be adjacent - within 5 spaces)
    assign_property(engine, "b", 3, level=0)
    engine.state.players["a"].position = 1
    engine.state.players["a"].cash = 300
    give_card(engine, "a", "chance-buy")

    try:
        events = engine.execute(UseChanceCard("a", "chance-buy", target_position=3))
        collector.collect("Scenario 16: Buy/Stolen/Vacate", events, "a", engine)
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(EndTurn("a")), "a", engine
        )

        # Cycle back
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(EndTurn("b")), "b", engine
        )
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(EndTurn("c")), "c", engine
        )
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(EndTurn("d")), "d", engine
        )

        # Steal card: chance_card_stolen (2nd)
        engine.state.players["c"].chance_cards.append("chance-angel")
        engine.state.players["a"].chance_cards.append("chance-steal")
        engine.state.current_player_id = "a"
        engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
        engine.random.randint = lambda _low, _high: 6  # type: ignore
        events = engine.execute(UseChanceCard("a", "chance-steal", target_player_id="c"))
        collector.collect("Scenario 16: Buy/Stolen/Vacate", events, "a", engine)
        events = engine.execute(SelectStolenChanceCard("a", "chance-angel"))
        collector.collect("Scenario 16: Buy/Stolen/Vacate", events, "a", engine)

        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(EndTurn("a")), "a", engine
        )

        # Vacate card: property_vacated (2nd) - must be adjacent (within 5 spaces)
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(EndTurn("b")), "b", engine
        )
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(EndTurn("c")), "c", engine
        )
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect(
            "Scenario 16: Buy/Stolen/Vacate", engine.execute(EndTurn("d")), "d", engine
        )

        assign_property(engine, "d", 8, level=1)
        engine.state.players["a"].position = 6  # Within 5 spaces of position 8
        give_card(engine, "a", "chance-vacate")
        events = engine.execute(UseChanceCard("a", "chance-vacate", target_position=8))
        collector.collect("Scenario 16: Buy/Stolen/Vacate", events, "a", engine)
    except Exception as e:
        print(f"  Scenario 16 error: {e}")


def scenario_17_waiver_equalize_tax(directory: str, collector: EventCollector) -> None:
    """rent_waiver_used (2nd), rent_waivers_granted (2nd), cash_equalized (2nd),
    cash_tax_transferred (2nd).
    """
    config = create_config("s17", directory, seed=17)
    engine = GameEngine(config)

    # Waiver grant + use
    give_card(engine, "a", "chance-waiver")
    engine.state.players["a"].cash = 1000

    try:
        events = engine.execute(UseChanceCard("a", "chance-waiver"))
        collector.collect("Scenario 17: Waiver/Equalize/Tax", events, "a", engine)
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("a")), "a", engine
        )

        # Use waiver by landing on rent property
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("b")), "b", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("c")), "c", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("d")), "d", engine
        )

        assign_property(engine, "b", 6, level=1)
        engine.state.players["a"].position = 5
        set_dice(engine, [1, 0])
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 17: Waiver/Equalize/Tax", events, "a", engine)
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("a")), "a", engine
        )

        # Equalize card
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("b")), "b", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("c")), "c", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("d")), "d", engine
        )

        engine.state.players["a"].cash = 500
        engine.state.players["c"].cash = 1500
        give_card(engine, "a", "chance-equalize")
        events = engine.execute(UseChanceCard("a", "chance-equalize", target_player_id="c"))
        collector.collect("Scenario 17: Waiver/Equalize/Tax", events, "a", engine)
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("a")), "a", engine
        )

        # Tax card
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(RollDice("b")), "b", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("b")), "b", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(RollDice("c")), "c", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("c")), "c", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(RollDice("d")), "d", engine
        )
        collector.collect(
            "Scenario 17: Waiver/Equalize/Tax", engine.execute(EndTurn("d")), "d", engine
        )

        engine.state.players["d"].cash = 800
        give_card(engine, "a", "chance-tax")
        events = engine.execute(UseChanceCard("a", "chance-tax", target_player_id="d"))
        collector.collect("Scenario 17: Waiver/Equalize/Tax", events, "a", engine)
    except Exception as e:
        print(f"  Scenario 17 error: {e}")


def scenario_18_game_finish(directory: str, collector: EventCollector) -> None:
    """game_finished (2nd occurrence) - run to max rounds."""
    config = GameConfig(
        game_id="s18",
        experiment_id="manual-4b",
        seed=18,
        players=(
            PlayerConfig(player_id="a", seat=1),
            PlayerConfig(player_id="b", seat=2),
            PlayerConfig(player_id="c", seat=3),
            PlayerConfig(player_id="d", seat=4),
        ),
        initial_cash=1500,
        max_complete_rounds=3,  # Short game
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=Path(directory),
    )
    engine = GameEngine(config)

    try:
        # Run 3 complete rounds
        for _ in range(12):  # 3 rounds × 4 players
            current = engine.state.current_player_id
            set_dice(engine, [1, 2])
            collector.collect(
                "Scenario 18: Game Finish", engine.execute(RollDice(current)), current, engine
            )
            collector.collect(
                "Scenario 18: Game Finish", engine.execute(EndTurn(current)), current, engine
            )
            if engine.state.finished:
                break
    except Exception as e:
        print(f"  Scenario 18 error: {e}")


def scenario_19_final_coverage(directory: str, collector: EventCollector) -> None:
    """Final scenario to reach 2nd occurrences for remaining events:
    building_sold, chance_card_stolen, go_salary_collected, mortgage_redeemed,
    player_jailed, property_mortgaged, property_purchased_from_player, property_vacated."""
    config = create_config("s19", directory, seed=19)
    engine = GameEngine(config)

    # Setup for all operations
    assign_property(engine, "a", 1, level=2)
    assign_property(engine, "a", 3, level=0)
    assign_property(engine, "b", 6, level=1)
    engine.state.players["a"].cash = 500
    engine.state.players["a"].position = 0
    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT

    try:
        # building_sold
        events = engine.execute(SellBuilding("a", 1))
        collector.collect("Scenario 19: Final Coverage", events, "a", engine)

        # property_mortgaged
        events = engine.execute(Mortgage("a", 3))
        collector.collect("Scenario 19: Final Coverage", events, "a", engine)

        # End turn and cycle
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("a")), "a", engine)

        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("d")), "d", engine)

        # mortgage_redeemed
        engine.state.players["a"].cash = 300
        set_dice(engine, [1, 0])
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("a")), "a", engine)
        events = engine.execute(RedeemMortgage("a", 3))
        collector.collect("Scenario 19: Final Coverage", events, "a", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("a")), "a", engine)

        # go_salary_collected: pass GO
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("d")), "d", engine)

        engine.state.players["a"].position = 37
        set_dice(engine, [3, 1])
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 19: Final Coverage", events, "a", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("a")), "a", engine)

        # player_jailed: land on go-to-jail (position 30)
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("d")), "d", engine)

        engine.state.players["a"].position = 28
        set_dice(engine, [2, 0])
        events = engine.execute(RollDice("a"))
        collector.collect("Scenario 19: Final Coverage", events, "a", engine)

        # Cycle back for card operations
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("d")), "d", engine)

        # Release a from jail for next operations
        engine.state.players["a"].jail_status = JailStatus.FREE
        engine.state.players["a"].position = 5

        # property_purchased_from_player: buy card (within 5 spaces)
        assign_property(engine, "c", 8, level=0)
        engine.state.players["a"].cash = 400
        give_card(engine, "a", "chance-buy")
        events = engine.execute(UseChanceCard("a", "chance-buy", target_position=8))
        collector.collect("Scenario 19: Final Coverage", events, "a", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("a")), "a", engine)

        # Cycle back
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("d")), "d", engine)

        # property_vacated: vacate card (within 5 spaces)
        assign_property(engine, "d", 11, level=1)
        engine.state.players["a"].position = 9
        give_card(engine, "a", "chance-vacate")
        events = engine.execute(UseChanceCard("a", "chance-vacate", target_position=11))
        collector.collect("Scenario 19: Final Coverage", events, "a", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("a")), "a", engine)

        # Cycle back
        set_dice(engine, [1, 0, 1, 0, 1, 0])
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("b")), "b", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("c")), "c", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(RollDice("d")), "d", engine)
        collector.collect("Scenario 19: Final Coverage", engine.execute(EndTurn("d")), "d", engine)

        # chance_card_stolen: steal card
        engine.state.players["b"].chance_cards.append("chance-monster")
        engine.state.players["a"].chance_cards.append("chance-steal")
        engine.state.current_player_id = "a"
        engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
        engine.random.randint = lambda _low, _high: 5  # type: ignore
        events = engine.execute(UseChanceCard("a", "chance-steal", target_player_id="b"))
        collector.collect("Scenario 19: Final Coverage", events, "a", engine)
        events = engine.execute(SelectStolenChanceCard("a", "chance-monster"))
        collector.collect("Scenario 19: Final Coverage", events, "a", engine)

    except Exception as e:
        print(f"  Scenario 19 error: {e}")


def scenario_20_last_three(directory: str, collector: EventCollector) -> None:
    """Final push for last 3 events: chance_card_stolen,
    property_purchased_from_player, property_vacated.
    """
    config = create_config("s20", directory, seed=20)
    engine = GameEngine(config)

    # property_purchased_from_player #1: distance 1 (proven pattern)
    assign_property(engine, "b", 11, level=0)
    engine.state.players["a"].position = 10
    engine.state.players["a"].cash = 400
    give_card(engine, "a", "chance-buy")
    events = engine.execute(UseChanceCard("a", "chance-buy", target_position=11))
    collector.collect("Scenario 20: Last Three", events, "a", engine)

    # property_purchased_from_player #2: another distance 1
    assign_property(engine, "c", 16, level=0)
    engine.state.players["a"].position = 15
    engine.state.players["a"].cash = 400
    give_card(engine, "a", "chance-buy")
    events = engine.execute(UseChanceCard("a", "chance-buy", target_position=16))
    collector.collect("Scenario 20: Last Three", events, "a", engine)

    # property_vacated: distance 2 (proven pattern)
    assign_property(engine, "b", 3, level=0)
    engine.state.players["a"].position = 1
    give_card(engine, "a", "chance-vacate")
    events = engine.execute(UseChanceCard("a", "chance-vacate", target_position=3))
    collector.collect("Scenario 20: Last Three", events, "a", engine)

    # chance_card_stolen - full two-step flow
    engine.state.players["c"].chance_cards.append("chance-waiver")
    engine.state.players["a"].position = 5
    engine.state.players["a"].chance_cards.append("chance-steal")
    engine.state.current_player_id = "a"
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.random.randint = lambda _low, _high: 4  # type: ignore
    events = engine.execute(UseChanceCard("a", "chance-steal", target_player_id="c"))
    collector.collect("Scenario 20: Last Three", events, "a", engine)
    events = engine.execute(SelectStolenChanceCard("a", "chance-waiver"))
    collector.collect("Scenario 20: Last Three", events, "a", engine)


def main() -> None:
    """Execute all scenarios and generate report."""
    print("=" * 80)
    print("HISTORY BROADCAST MANUAL TEST")
    print("=" * 80)

    with TemporaryDirectory() as directory:
        collector = EventCollector()

        scenarios = [
            ("Scenario 1: Basic Flow", scenario_1_basic_flow),
            ("Scenario 2: Jail Mechanics", scenario_2_jail_mechanics),
            ("Scenario 3: Jail Fine", scenario_3_jail_pay_fine),
            ("Scenario 4: Buildings", scenario_4_building_ops),
            ("Scenario 5: Steal/Tax", scenario_5_chance_steal_tax),
            ("Scenario 6: Equalize/Buy", scenario_6_chance_equalize_buy),
            ("Scenario 7: Waiver/Nuke", scenario_7_chance_waiver_nuke),
            ("Scenario 8: Vacate/Ongoing", scenario_8_chance_vacate_ongoing),
            ("Scenario 9: Mortgage/Bankruptcy", scenario_9_mortgage_bankruptcy),
            ("Scenario 10: Community Chest", scenario_10_card_drawn_community),
            ("Scenario 11: Game Finish", scenario_11_game_finish),
            ("Scenario 12: Auto-build Skip", scenario_12_auto_build_skip),
            ("Scenario 13: Repeat Coverage", scenario_13_repeat_coverage),
            ("Scenario 14: Jail Fail/Bankruptcy", scenario_14_jail_fail_bankruptcy),
            ("Scenario 15: Ongoing Effects", scenario_15_ongoing_effects),
            ("Scenario 16: Buy/Stolen/Vacate", scenario_16_buy_stolen_vacate),
            ("Scenario 17: Waiver/Equalize/Tax", scenario_17_waiver_equalize_tax),
            ("Scenario 18: Game Finish", scenario_18_game_finish),
            ("Scenario 19: Final Coverage", scenario_19_final_coverage),
            ("Scenario 20: Last Three", scenario_20_last_three),
        ]

        for name, func in scenarios:
            print(f"\nRunning {name}...")
            try:
                func(directory, collector)
                event_count = len(
                    [1 for s, _, b, _ in collector.all_events if s == name and b is not None]
                )
                print(f"  Collected {event_count} broadcast events")
            except Exception as e:
                print(f"  Scenario failed: {e}")

        # Write report
        report_path = Path("tests/manual/history_broadcast_report.txt")
        collector.write_report(report_path)
        print(f"\n[OK] Full report written to: {report_path}")

        # Print coverage summary
        print("\n" + "=" * 80)
        print("COVERAGE SUMMARY")
        print("=" * 80)
        for event_type in sorted(WHITELIST):
            count = collector.counts[event_type]
            status = "[OK]" if count >= 2 else "[FAIL]"
            print(f"{status} {event_type:50s} {count:3d} occurrences")

        total = len(WHITELIST)
        covered = sum(1 for et in WHITELIST if collector.counts[et] >= 2)
        print(f"\nCovered: {covered}/{total} events with >=2 occurrences")

        # Verify coverage
        if not collector.verify_coverage(min_count=2):
            print("\nNote: Some rare events may require additional scenarios.")
            sys.exit(1)

        print("\n[OK] All whitelist events have >= 2 occurrences")


if __name__ == "__main__":
    main()
