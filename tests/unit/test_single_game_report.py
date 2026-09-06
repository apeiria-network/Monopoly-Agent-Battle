from pathlib import Path

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.decision.runner import DeterministicPolicyController, run_decision_game
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts
from monopoly_agent_battle.reporting.single_game import (
    build_single_game_report,
    render_single_game_report,
)


def make_config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="report-game",
        experiment_id="report-experiment",
        seed=1,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        max_complete_rounds=1,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=output_directory,
    )


def test_single_game_report_aggregates_safe_fields(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    run_decision_game(GameEngine(config), DeterministicPolicyController(), artifacts)

    report = build_single_game_report(artifacts.run_directory)
    text = render_single_game_report(report)

    assert report["status"] == "completed"
    assert report["rankings"] == ["b", "a"]
    assert report["llm"]["calls"] == 0
    assert report["decisions"]["total"] == 2
    assert report["decisions"]["non_llm"] == 2
    assert "# 单局结果：report-game" in text
    assert "a" in text and "b" in text
    assert "runtime" not in text


def test_single_game_report_rejects_missing_result(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)

    from monopoly_agent_battle.reporting.single_game import ReportError

    try:
        build_single_game_report(artifacts.run_directory)
    except ReportError as error:
        assert "result.json" in str(error)
    else:
        raise AssertionError("missing result should be rejected")
