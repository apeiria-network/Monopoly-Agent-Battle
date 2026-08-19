"""Minimal CLI for verifying the Phase 0 project baseline."""

from __future__ import annotations

import argparse
from pathlib import Path

from monopoly_agent_battle.config.loader import config_hash, load_game_config
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts, utc_timestamp


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(prog="monopoly-agent-battle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo_parser = subparsers.add_parser("demo", help="create a Phase 0 game run skeleton")
    demo_parser.add_argument("--config", required=True, type=Path, help="path to a game YAML file")
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


def main() -> None:
    """Run the requested command."""
    arguments = build_parser().parse_args()
    if arguments.command == "demo":
        print(run_demo(arguments.config))
