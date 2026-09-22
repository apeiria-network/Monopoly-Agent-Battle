"""Generate fake-agent Ming-ablation batch configs (batch5/batch6, 20 games each).

Each game pits four Ming-ablation (去汇总) courts against each other with fake
random LLM clients — no real LLM is involved.  Player-id seat order rotates
per game so each id occupies every seat equally often.

Usage: python configs/fake_agent_batch_test/generate_ming_ablation_configs.py
"""

from pathlib import Path
from typing import Any

import yaml

from monopoly_agent_battle.config.loader import load_game_config

BASE = Path(__file__).resolve().parent

BATCHES = (5, 6)
GAMES_PER_BATCH = 20
ROUNDS_PER_GAME = 50

ROLES = (
    "chief_grand_secretary",
    "grand_secretary_1",
    "grand_secretary_2",
    "emperor",
)
PLAYERS = ("ming-abl-1", "ming-abl-2", "ming-abl-3", "ming-abl-4")


def build_game(batch: int, game: int) -> dict[str, Any]:
    seed = batch * 100 + game
    rotation = (game - 1) % len(PLAYERS)
    players: list[dict[str, Any]] = []
    profiles: dict[str, dict[str, Any]] = {}
    index = 0
    for player_index, player_id in enumerate(PLAYERS):
        seat = (player_index + rotation) % len(PLAYERS) + 1
        players.append(
            {
                "player_id": player_id,
                "seat": seat,
                "controller_type": "ming_ablation_court",
                "court_role_profiles": {
                    role: f"{player_id}-{role}" for role in ROLES
                },
            }
        )
        for role in ROLES:
            index += 1
            profiles[f"{player_id}-{role}"] = {
                "provider": "fake",
                "model": "fake-random-v1",
                "seed": seed * 100 + index,
            }
    players.sort(key=lambda player: player["seat"])
    return {
        "game_id": f"b{batch}-game-{game:03d}",
        "experiment_id": f"fake-batch-{batch}",
        "seed": seed,
        "players": players,
        "model_profiles": profiles,
        "initial_cash": 1500,
        "initial_chance_cards": 3,
        "max_complete_rounds": ROUNDS_PER_GAME,
        "rules_version": "classic-level0-v1",
        "rules_level": 0,
        "board_data_version": "classic-us-40-v1",
        "card_data_version": "classic-cards-v1",
        "output_directory": "runs/fake_agent_batch_test",
    }


def main() -> None:
    written = 0
    for batch in BATCHES:
        batch_dir = BASE / f"batch{batch}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        entries: list[str] = []
        for game in range(1, GAMES_PER_BATCH + 1):
            config = build_game(batch, game)
            path = batch_dir / f"game_{game:03d}.yaml"
            path.write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            load_game_config(path)
            entries.append(path.name)
            written += 1
        manifest = "games:\n" + "".join(f"  - {name}\n" for name in entries)
        (batch_dir / "batch.yaml").write_text(manifest, encoding="utf-8")
    print(f"wrote and validated {written} game configs + {len(BATCHES)} batch manifests")


if __name__ == "__main__":
    main()
