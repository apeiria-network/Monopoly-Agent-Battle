from pathlib import Path

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.commands import EndTurn, RollDice
from monopoly_agent_battle.game.controllers import ScriptedController, run_scripted_game
from monopoly_agent_battle.game.engine import GameEngine


def test_scripted_controller_executes_commands_in_order(tmp_path: Path) -> None:
    engine = GameEngine(
        GameConfig(
            game_id="scripted-game",
            experiment_id="scripted-experiment",
            seed=1,
            players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
            max_complete_rounds=2,
            rules_version="classic-level0-v1",
            board_data_version="classic-us-40-v1",
            card_data_version="classic-cards-v1",
            output_directory=tmp_path,
        )
    )
    iterator = iter([1, 2])
    engine.random.randint = lambda _low, _high: next(iterator)  # type: ignore[method-assign]
    engine.state.players["a"].position = 38

    events = run_scripted_game(
        engine,
        ScriptedController([RollDice("a"), EndTurn("a")]),
    )

    assert events[-1].event_type == "turn_started"
    assert engine.state.current_player_id == "b"
