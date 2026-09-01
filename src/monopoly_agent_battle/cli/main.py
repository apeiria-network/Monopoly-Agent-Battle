"""Command-line entry points for game runs and demonstrations."""

from __future__ import annotations

import argparse
import hashlib
import random
from pathlib import Path

from monopoly_agent_battle.agents.baseline import BaselineAgent
from monopoly_agent_battle.agents.ming import MingCourtAgent
from monopoly_agent_battle.agents.qin import QinCourtAgent
from monopoly_agent_battle.agents.random_baseline import RandomBaselineController
from monopoly_agent_battle.agents.shang import ShangCourtAgent
from monopoly_agent_battle.agents.tang import TangCourtAgent
from monopoly_agent_battle.config.loader import config_hash, load_game_config
from monopoly_agent_battle.config.local_env import load_local_env
from monopoly_agent_battle.config.models import (
    MingCourtRoleProfiles,
    QinCourtRoleProfiles,
    ShangCourtRoleProfiles,
    TangCourtRoleProfiles,
)
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.runner import (
    ConversationBinding,
    DispatchController,
    RawDecisionController,
    run_decision_game,
)
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.resume import resume_random_game
from monopoly_agent_battle.llm.fake_client import FakeLLMClient
from monopoly_agent_battle.llm.mock_client import MockLLMClient
from monopoly_agent_battle.llm.openai_compatible_client import OpenAICompatibleClient
from monopoly_agent_battle.llm.recording_client import RecordingLLMClient
from monopoly_agent_battle.llm.registry import create_client, register_client_factory
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts, utc_timestamp
from monopoly_agent_battle.performance.tracker import PerformanceTracker
from monopoly_agent_battle.reporting.single_game import write_single_game_report


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(prog="monopoly-agent-battle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("demo", help="create a Phase 0 game run skeleton")
    play_parser = subparsers.add_parser(
        "play", help="run a complete game with mock-LLM and random non-LLM baselines"
    )
    play_parser.add_argument("--config", required=True, type=Path, help="path to a game YAML file")
    report_parser = subparsers.add_parser(
        "report", help="render a readable report from a completed or partial run"
    )
    report_parser.add_argument("--run-dir", required=True, type=Path, help="run artifact directory")
    report_parser.add_argument("--output", type=Path, help="optional Markdown output path")
    resume_parser = subparsers.add_parser(
        "resume", help="resume an unfinished all-random run"
    )
    resume_parser.add_argument("--run-dir", required=True, type=Path, help="run artifact directory")
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
    conversations: dict[str, ConversationBinding] = {}
    needs_mock_client = any(
        profile.provider == "mock" for profile in config.model_profiles.values()
    )
    if needs_mock_client:
        register_client_factory("mock", lambda profile: MockLLMClient(seed=profile.seed))
    if any(profile.provider == "fake" for profile in config.model_profiles.values()):
        register_client_factory("fake", lambda profile: FakeLLMClient(seed=profile.seed))
    if any(profile.provider == "openai_compatible" for profile in config.model_profiles.values()):
        register_client_factory("openai_compatible", OpenAICompatibleClient)
    for player in config.players:
        if _is_random_baseline(player.controller_type):
            controllers[player.player_id] = RandomBaselineController(
                _random_baseline_rng(config.seed, player.seat, player.player_id)
            )
            continue
        if player.controller_type == "shang_court":
            assert isinstance(player.court_role_profiles, ShangCourtRoleProfiles)
            priest_profile = config.model_profiles[player.court_role_profiles.great_priest]
            emperor_profile = config.model_profiles[player.court_role_profiles.emperor]
            priest_client = RecordingLLMClient(create_client(priest_profile), artifacts)
            emperor_client = RecordingLLMClient(create_client(emperor_profile), artifacts)
            conversation = AgentConversation(
                agent_id=player.player_id,
                window_turns=config.window_turns,
                prompt_profile=config.prompt_profile,
            )
            conversations[player.player_id] = conversation
            controllers[player.player_id] = ShangCourtAgent(
                player_id=player.player_id,
                great_priest_client=priest_client,
                great_priest_profile=priest_profile,
                emperor_client=emperor_client,
                emperor_profile=emperor_profile,
                emperor_conversation=conversation,
            )
            continue
        if player.controller_type == "qin_court":
            assert isinstance(player.court_role_profiles, QinCourtRoleProfiles)
            roles = {
                role: config.model_profiles[getattr(player.court_role_profiles, role)]
                for role in ("chancellor", "grand_marshal", "imperial_counsellor", "emperor")
            }
            role_clients = {
                role: RecordingLLMClient(create_client(profile), artifacts)
                for role, profile in roles.items()
            }
            role_conversations = {
                role: AgentConversation(
                    agent_id=f"{player.player_id}.{role}",
                    window_turns=config.window_turns,
                    prompt_profile=config.prompt_profile,
                )
                for role in roles
            }
            conversations[player.player_id] = role_conversations
            controllers[player.player_id] = QinCourtAgent(
                player_id=player.player_id,
                chancellor_client=role_clients["chancellor"],
                chancellor_profile=roles["chancellor"],
                grand_marshal_client=role_clients["grand_marshal"],
                grand_marshal_profile=roles["grand_marshal"],
                imperial_counsellor_client=role_clients["imperial_counsellor"],
                imperial_counsellor_profile=roles["imperial_counsellor"],
                emperor_client=role_clients["emperor"],
                emperor_profile=roles["emperor"],
                conversations=role_conversations,
                validation_retries=config.validation_retries,
            )
            continue
        if player.controller_type == "ming_court":
            assert isinstance(player.court_role_profiles, MingCourtRoleProfiles)
            roles = {
                role: config.model_profiles[getattr(player.court_role_profiles, role)]
                for role in (
                    "chief_grand_secretary",
                    "grand_secretary_1",
                    "grand_secretary_2",
                    "emperor",
                )
            }
            role_clients = {
                role: RecordingLLMClient(create_client(profile), artifacts)
                for role, profile in roles.items()
            }
            role_conversations = {
                role: AgentConversation(
                    agent_id=f"{player.player_id}.{role}",
                    window_turns=config.window_turns,
                    prompt_profile=config.prompt_profile,
                )
                for role in roles
            }
            conversations[player.player_id] = role_conversations
            controllers[player.player_id] = MingCourtAgent(
                player_id=player.player_id,
                chief_client=role_clients["chief_grand_secretary"],
                chief_profile=roles["chief_grand_secretary"],
                secretary_1_client=role_clients["grand_secretary_1"],
                secretary_1_profile=roles["grand_secretary_1"],
                secretary_2_client=role_clients["grand_secretary_2"],
                secretary_2_profile=roles["grand_secretary_2"],
                emperor_client=role_clients["emperor"],
                emperor_profile=roles["emperor"],
                conversations=role_conversations,
                validation_retries=config.validation_retries,
            )
            continue
        if player.controller_type == "tang_court":
            assert isinstance(player.court_role_profiles, TangCourtRoleProfiles)
            roles = {
                role: config.model_profiles[getattr(player.court_role_profiles, role)]
                for role in ("zhongshu", "menxia", "emperor")
            }
            role_clients = {
                role: RecordingLLMClient(create_client(profile), artifacts)
                for role, profile in roles.items()
            }
            role_conversations = {
                role: AgentConversation(
                    agent_id=f"{player.player_id}.{role}",
                    window_turns=config.window_turns,
                    prompt_profile=config.prompt_profile,
                )
                for role in roles
            }
            conversations[player.player_id] = role_conversations
            controllers[player.player_id] = TangCourtAgent(
                player_id=player.player_id,
                zhongshu_client=role_clients["zhongshu"],
                zhongshu_profile=roles["zhongshu"],
                menxia_client=role_clients["menxia"],
                menxia_profile=roles["menxia"],
                emperor_client=role_clients["emperor"],
                emperor_profile=roles["emperor"],
                conversations=role_conversations,
                validation_retries=config.validation_retries,
            )
            continue
        if player.model_profile is None:
            msg = f"player {player.player_id} has no model_profile"
            raise SystemExit(msg)
        profile = config.model_profiles[player.model_profile]
        client = RecordingLLMClient(create_client(profile), artifacts)
        conversation = AgentConversation(
            agent_id=player.player_id,
            window_turns=config.window_turns,
            prompt_profile=config.prompt_profile,
        )
        conversations[player.player_id] = conversation
        controllers[player.player_id] = BaselineAgent(
            player_id=player.player_id,
            client=client,
            profile=profile,
            conversation=conversation,
        )
    engine = GameEngine(config)
    court_types = {
        player.player_id: str(player.controller_type)
        for player in config.players
        if player.controller_type in {"shang_court", "qin_court", "tang_court", "ming_court"}
    }
    tracker = PerformanceTracker(engine, court_types)
    run_decision_game(
        engine,
        DispatchController(controllers),
        artifacts,
        conversations=conversations,
        performance_tracker=tracker,
    )
    return artifacts.run_directory


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
    load_local_env()
    arguments = build_parser().parse_args()
    if arguments.command == "demo":
        print(run_demo(arguments.config))
    elif arguments.command == "play":
        print(run_play(arguments.config))
    elif arguments.command == "report":
        print(write_single_game_report(arguments.run_dir, arguments.output))
    elif arguments.command == "resume":
        print(resume_random_game(arguments.run_dir))
