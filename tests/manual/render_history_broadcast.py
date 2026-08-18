"""Manual test: render history broadcast for all whitelist events.

Run from the repository root:
    .venv/Scripts/python.exe tests/manual/render_history_broadcast.py

This script simulates a game and prints broadcast sentences for whitelist events.
Note: This is a minimal demonstration. Full event coverage requires complex scenarios.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.broadcast import render_event
from monopoly_agent_battle.domain.commands import (
    DeclareBankruptcy,
    EndTurn,
    Mortgage,
    PayJailFine,
    RedeemMortgage,
    RollDice,
    SellBuilding,
    UseChanceCard,
)
from monopoly_agent_battle.domain.models import GameEvent, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine


def main() -> None:
    """Execute a simulated game and print broadcast sentences."""
    with TemporaryDirectory() as directory:
        config = GameConfig(
            game_id="broadcast-test",
            experiment_id="manual-4b",
            seed=42,  # Fixed seed for reproducibility
            players=(
                PlayerConfig(player_id="a", seat=1),
                PlayerConfig(player_id="b", seat=2),
                PlayerConfig(player_id="c", seat=3),
                PlayerConfig(player_id="d", seat=4),
            ),
            initial_cash=1200,  # Lower cash to trigger more rent/payment events
            max_complete_rounds=15,  # More rounds for diverse scenarios
            rules_version="classic-level0-v1",
            board_data_version="classic-us-40-v1",
            card_data_version="classic-cards-v1",
            output_directory=Path(directory),
        )
        engine = GameEngine(config)

        event_counts: Counter[str] = Counter()
        viewer_id = "a"

        def collect_and_render(events: list[GameEvent]) -> None:
            """Render and print each event, counting occurrences."""
            for event in events:
                event_counts[event.event_type] += 1
                broadcast = render_event(event, viewer_id)
                if broadcast is not None:
                    print(f"[{event.event_type}] {broadcast}")

        max_actions = 800
        action_count = 0

        while not engine.state.finished and action_count < max_actions:
            player_id = engine.state.current_player_id
            phase = engine.state.turn_phase
            player = engine.state.players[player_id]

            try:
                if phase == TurnPhase.ROLLING:
                    events = engine.execute(RollDice(player_id))
                    collect_and_render(events)
                    action_count += 1

                elif phase == TurnPhase.ASSET_MANAGEMENT:
                    # Try to use chance cards if available
                    if player.chance_cards:
                        card_id = player.chance_cards[0]
                        # Try simple cards first (no target required)
                        try:
                            events = engine.execute(UseChanceCard(player_id, card_id))
                            collect_and_render(events)
                            action_count += 1
                            continue
                        except Exception:
                            pass

                    # Try to redeem mortgaged properties if cash is high
                    if player.cash > 500:
                        for pos, prop in engine.state.properties.items():
                            if prop.owner_id == player_id and prop.mortgaged:
                                try:
                                    events = engine.execute(RedeemMortgage(player_id, pos))
                                    collect_and_render(events)
                                    action_count += 1
                                    break
                                except Exception:
                                    pass

                    # End turn
                    events = engine.execute(EndTurn(player_id))
                    collect_and_render(events)
                    action_count += 1

                elif phase == TurnPhase.PAYMENT_RESOLUTION:
                    # Handle payment resolution
                    handled = False

                    # If in jail and have cash, try to pay fine
                    if player.jail_status.value != "free" and player.cash >= 50:
                        try:
                            events = engine.execute(PayJailFine(player_id))
                            collect_and_render(events)
                            action_count += 1
                            handled = True
                            continue
                        except Exception:
                            pass

                    # Try selling buildings first (better value)
                    for pos, prop in engine.state.properties.items():
                        if prop.owner_id == player_id and prop.building_level > 0:
                            try:
                                events = engine.execute(SellBuilding(player_id, pos))
                                collect_and_render(events)
                                action_count += 1
                                handled = True
                                break
                            except Exception:
                                pass

                    if not handled:
                        # Try mortgaging properties
                        for pos, prop in engine.state.properties.items():
                            if (
                                prop.owner_id == player_id
                                and not prop.mortgaged
                                and prop.building_level == 0
                            ):
                                try:
                                    events = engine.execute(Mortgage(player_id, pos))
                                    collect_and_render(events)
                                    action_count += 1
                                    handled = True
                                    break
                                except Exception:
                                    pass

                    if not handled:
                        # Try declaring bankruptcy if no assets left
                        try:
                            events = engine.execute(DeclareBankruptcy(player_id))
                            collect_and_render(events)
                            action_count += 1
                        except Exception:
                            # Cannot resolve payment
                            break

                elif phase == TurnPhase.FORCED_DISCARD:
                    # Try to discard first card
                    if player.chance_cards:
                        from monopoly_agent_battle.domain.commands import DiscardChanceCard

                        try:
                            events = engine.execute(
                                DiscardChanceCard(player_id, player.chance_cards[0])
                            )
                            collect_and_render(events)
                            action_count += 1
                        except Exception as e:
                            print(f"Failed to discard card: {e}")
                            break
                    else:
                        break

                elif phase == TurnPhase.THEFT_CARD_SELECTION:
                    # Skip theft selection - requires specific card IDs
                    print("Reached THEFT_CARD_SELECTION phase, ending test")
                    break

                elif phase == TurnPhase.TURN_COMPLETE:
                    # Turn complete - engine will auto-advance, just continue loop
                    continue

                else:
                    print(f"Unexpected phase: {phase}")
                    break

            except Exception as e:
                print(f"Error during {player_id} action in phase {phase}: {e}")
                break

        print("\n" + "=" * 80)
        print("EVENT COVERAGE SUMMARY")
        print("=" * 80)

        whitelist = {
            "dice_rolled",
            "player_moved",
            "go_salary_collected",
            "property_purchased",
            "property_purchased_from_player",
            "payment_made",
            "player_bankrupt",
            "player_jailed",
            "jail_released",
            "jail_roll_failed",
            "turn_started",
            "turn_ended",
            "card_drawn",
            "card_discarded",
            "chance_card_used",
            "card_die_rolled",
            "chance_card_stolen",
            "building_added",
            "building_sold",
            "building_level_changed",
            "property_mortgaged",
            "mortgage_redeemed",
            "property_reset",
            "property_vacated",
            "rent_waiver_used",
            "rent_waivers_granted",
            "cash_received",
            "cash_tax_transferred",
            "cash_equalized",
            "ongoing_effect_created",
            "ongoing_effect_expired",
            "automatic_build_skipped_insufficient_cash",
            "game_finished",
        }

        for event_type in sorted(whitelist):
            count = event_counts[event_type]
            status = "OK" if count >= 3 else "!!"
            print(f"{status} {event_type:50s} {count:3d} occurrences")

        total_whitelist = len(whitelist)
        covered = sum(1 for et in whitelist if event_counts[et] >= 3)

        print(f"\nCovered: {covered}/{total_whitelist} events with >=3 occurrences")
        print("\nNote: This is a basic simulation covering common events.")
        print("Full coverage requires complex scenarios with card usage, jail, building, etc.")


if __name__ == "__main__":
    main()
