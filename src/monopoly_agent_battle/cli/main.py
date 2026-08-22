"""Command-line entry points for game runs and demonstrations."""

from __future__ import annotations

import argparse
import hashlib
import random
from pathlib import Path

from monopoly_agent_battle.agents.baseline import BaselineAgent
from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.config.loader import config_hash, load_game_config
from monopoly_agent_battle.context.conversation import AgentConversation
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
        "play", help="run a complete game with mock-LLM and random non-LLM baselines"
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
    """Run a full game with configured mock-LLM and random non-LLM baselines."""
    config = load_game_config(config_path)
    artifacts = RunArtifacts.create(config)
    controllers: dict[str, RawDecisionController] = {}
    conversations: dict[str, AgentConversation] = {}
    needs_mock_client = any(
        _is_llm_baseline(player.controller_type, player.model_profile) for player in config.players
    )
    if needs_mock_client:
        register_client_factory("mock", lambda profile: MockLLMClient(seed=config.seed))
    for player in config.players:
        if _is_random_baseline(player.controller_type):
            controllers[player.player_id] = RandomBaselineController(
                _random_baseline_rng(config.seed, player.seat, player.player_id)
            )
            continue
        if player.model_profile is None:
            msg = f"player {player.player_id} has no model_profile"
            raise SystemExit(msg)
        profile = config.model_profiles[player.model_profile]
        client = RecordingLLMClient(create_client(profile), artifacts)
        conversation = AgentConversation(
            agent_id=player.player_id, window_turns=config.window_turns
        )
        conversations[player.player_id] = conversation
        controllers[player.player_id] = BaselineAgent(
            player_id=player.player_id,
            client=client,
            profile=profile,
            conversation=conversation,
        )
    run_decision_game(
        GameEngine(config),
        DispatchController(controllers),
        artifacts,
        conversations=conversations,
    )
    return artifacts.run_directory


def _is_llm_baseline(controller_type: str | None, model_profile: str | None) -> bool:
    """Resolve the legacy controller configuration into its LLM baseline behavior."""
    return controller_type == "llm_baseline" or (
        controller_type is None and model_profile is not None
    )


def _is_random_baseline(controller_type: str | None) -> bool:
    """Return whether an explicitly configured player is a random baseline."""
    return controller_type == "random_baseline"


def _random_baseline_rng(seed: int, seat: int, player_id: str) -> random.Random:
    """Create a stable player-local RNG without consuming the engine RNG stream."""
    material = f"random-baseline-v1:{seed}:{seat}:{player_id}".encode()
    derived_seed = int.from_bytes(hashlib.sha256(material).digest(), "big")
    return random.Random(derived_seed)


def main() -> None:
    """Run the requested command."""
    arguments = build_parser().parse_args()
    if arguments.command == "demo":
        print(run_demo(arguments.config))
    elif arguments.command == "play":
        print(run_play(arguments.config))
