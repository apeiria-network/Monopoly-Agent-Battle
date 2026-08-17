"""Command-line entry points for game runs and demonstrations."""

from __future__ import annotations

import argparse
from pathlib import Path

from monopoly_agent_battle.agents.baseline import BaselineAgent
from monopoly_agent_battle.config.loader import config_hash, load_game_config
from monopoly_agent_battle.decision.runner import (
    DispatchController,
    RawDecisionController,
    run_decision_game,
)
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.mock_client import MockLLMClient
from monopoly_agent_battle.llm.recording_client import RecordingLLMClient
from monopoly_agent_battle.llm.registry import create_client, register_client_factory
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts, utc_timestamp


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(prog="monopoly-agent-battle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo_parser = subparsers.add_parser("demo", help="create a Phase 0 game run skeleton")
    demo_parser.add_argument("--config", required=True, type=Path, help="path to a game YAML file")
    play_parser = subparsers.add_parser(
        "play", help="run a complete game with credential-free mock LLM baselines"
    )
    play_parser.add_argument("--config", required=True, type=Path, help="path to a game YAML file")
    return parser


def run_demo(config_path: Path) -> Path:
    """Create an auditable empty game run from a frozen configuration."""
    config = load_game_config(config_path)
    artifacts = RunArtifacts.create(config)
    artifacts.append_event(
        "game_initialized",
        {"config_hash": config_hash(config), "seed": config.seed},
    )
    artifacts.write_result(
        {
            "ended_at": None,
            "end_reason": None,
            "game_id": config.game_id,
            "started_at": utc_timestamp(),
            "status": "initialized",
            "validity_status": "pending",
        }
    )
    return artifacts.run_directory


def run_play(config_path: Path) -> Path:
    """Run a full game where every player is a mock-LLM baseline agent."""
    config = load_game_config(config_path)
    if not config.model_profiles:
        raise SystemExit("play requires at least one model_profiles entry")
    artifacts = RunArtifacts.create(config)
    register_client_factory("mock", lambda profile: MockLLMClient(seed=config.seed))
    controllers: dict[str, RawDecisionController] = {}
    for player in config.players:
        if player.model_profile is None:
            msg = f"player {player.player_id} has no model_profile"
            raise SystemExit(msg)
        profile = config.model_profiles[player.model_profile]
        client = RecordingLLMClient(create_client(profile), artifacts)
        controllers[player.player_id] = BaselineAgent(
            player_id=player.player_id, client=client, profile=profile
        )
    run_decision_game(GameEngine(config), DispatchController(controllers), artifacts)
    return artifacts.run_directory


def main() -> None:
    """Run the requested command."""
    arguments = build_parser().parse_args()
    if arguments.command == "demo":
        print(run_demo(arguments.config))
    elif arguments.command == "play":
        print(run_play(arguments.config))
