"""Render a complete Stage 3 decision prompt for human inspection.

Run from the repository root:
    .venv/Scripts/python.exe tests/manual/render_decision_prompt.py

The fixture exposes one no-target option, one single-target option, and one
paired-target option so the complete `response_format` rendering is visible.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.prompts import render_decision_prompt
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import TurnPhase
from monopoly_agent_battle.game.engine import GameEngine


def main() -> None:
    """Print a deterministic asset-management prompt to standard output."""
    with TemporaryDirectory() as directory:
        config = GameConfig(
            game_id="prompt-inspection",
            experiment_id="manual-review",
            seed=1,
            players=(
                PlayerConfig(player_id="a", seat=1),
                PlayerConfig(player_id="b", seat=2),
            ),
            rules_version="classic-level0-v1",
            board_data_version="classic-us-40-v1",
            card_data_version="classic-cards-v1",
            output_directory=Path(directory),
        )
        engine = GameEngine(config)
        engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
        engine.state.properties[1].owner_id = "a"
        engine.state.players["a"].properties.add(1)
        engine.state.properties[3].owner_id = "b"
        engine.state.players["b"].properties.add(3)
        engine.state.players["a"].chance_cards.append("chance-swap-property")

        request = build_decision_request(engine, sequence=1)
        print(render_decision_prompt(request))


if __name__ == "__main__":
    main()
