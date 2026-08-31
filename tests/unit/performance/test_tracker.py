"""Unit coverage for terminal performance-window finalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.performance.scoring import DecisionEvidence, DecisionSignature
from monopoly_agent_battle.performance.tracker import PerformanceTracker


def _engine(tmp_path: Path) -> GameEngine:
    return GameEngine(
        GameConfig(
            game_id="tracker-game",
            experiment_id="tracker-test",
            seed=1,
            players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
            rules_version="classic-level0-v1",
            board_data_version="classic-us-40-v1",
            card_data_version="classic-cards-v1",
            output_directory=tmp_path,
        )
    )


def _evidence(decision_id: str) -> DecisionEvidence:
    signature = DecisionSignature.from_parts("end_turn", {})
    return DecisionEvidence(decision_id, signature, {"chancellor": signature})


def test_finalize_scores_open_basic_window_at_terminal_net_worth(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    tracker = PerformanceTracker(engine, {"a": "qin_court"})

    assert tracker.start_turn("a") == []
    tracker.record_decision("a", _evidence("d1"))
    engine.state.players["a"].cash = 1400
    engine.state.finished = True

    results = tracker.finalize()

    assert len(results) == 1
    result = results[0]
    assert result.start_turn == 1
    assert result.end_turn == 2
    assert result.end_net_worth == 1400
    assert [item.decision_id for item in result.decisions] == ["d1"]
    assert tracker.finalize() == []
    assert len(tracker.all_results()) == 1


def test_finalize_scores_all_players_and_long_term_window_once(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    tracker = PerformanceTracker(engine, {"a": "qin_court", "b": "shang_court"})

    for _ in range(3):
        tracker.start_turn("a")
    tracker.start_turn("b")
    engine.state.finished = True

    results = tracker.finalize()

    assert {(item.player_id, item.window.value) for item in results} == {
        ("a", "basic"),
        ("a", "long_term"),
        ("b", "basic"),
    }
    shang = next(item for item in results if item.player_id == "b")
    assert shang.no_scorable_officers is True
    assert shang.assessments == {}
    assert tracker.finalize() == []


def test_finalize_requires_terminal_engine_state(tmp_path: Path) -> None:
    tracker = PerformanceTracker(_engine(tmp_path), {"a": "qin_court"})
    tracker.start_turn("a")

    with pytest.raises(ValueError, match="finished"):
        tracker.finalize()
