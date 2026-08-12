"""Deterministic command driver for tests and non-LLM simulations."""

from collections.abc import Iterable

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
    SellBuilding,
    UseChanceCard,
)
from monopoly_agent_battle.domain.models import GameEvent
from monopoly_agent_battle.game.engine import GameEngine

GameCommand = (
    RollDice
    | Build
    | SellBuilding
    | Mortgage
    | RedeemMortgage
    | PayJailFine
    | ResolveRent
    | EndTurn
    | DeclareBankruptcy
    | DiscardChanceCard
    | UseChanceCard
)


class ScriptedController:
    """Supply a fixed command sequence without involving an LLM."""

    def __init__(self, commands: Iterable[GameCommand]) -> None:
        self._commands = iter(commands)

    def next_command(self) -> GameCommand:
        """Return the next scripted command."""
        return next(self._commands)


def run_scripted_game(engine: GameEngine, controller: ScriptedController) -> list[GameEvent]:
    """Execute scripted commands until exhaustion or a completed game."""
    events: list[GameEvent] = []
    while not engine.state.finished:
        try:
            command = controller.next_command()
        except StopIteration:
            break
        events.extend(engine.execute(command))
    return events
