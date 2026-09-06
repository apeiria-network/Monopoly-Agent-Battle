"""Generate fake-agent batch test configs (4 batches x 20 games).

Court seat order rotates per game so each court occupies every seat equally
often.

Usage: python configs/fake_agent_batch_test/generate_configs.py
"""

from pathlib import Path
from typing import Any

import yaml

from monopoly_agent_battle.config.loader import load_game_config

BASE = Path(__file__).resolve().parent

GAMES_PER_BATCH = 20
ROUNDS_PER_GAME = 50

COURTS: list[tuple[str, str, dict[str, str]]] = [
    (
        "shang-court",
        "shang_court",
        {
            "great_priest": "shang-great-priest",
            "emperor": "shang-emperor",
        },
    ),
    (
        "qin-court",
        "qin_court",
        {
            "chancellor": "qin-chancellor",
            "grand_marshal": "qin-grand-marshal",
            "imperial_counsellor": "qin-imperial-counsellor",
            "emperor": "qin-emperor",
        },
    ),
    (
        "tang-court",
        "tang_court",
        {
            "zhongshu": "tang-zhongshu",
            "menxia": "tang-menxia",
            "emperor": "tang-emperor",
        },
    ),
    (
        "ming-court",
        "ming_court",
        {
            "chief_grand_secretary": "ming-chief-grand-secretary",
            "grand_secretary_1": "ming-grand-secretary-1",
            "grand_secretary_2": "ming-grand-secretary-2",
            "emperor": "ming-emperor",
        },
    ),
]


def build_game(batch: int, game: int) -> dict[str, Any]:
    seed = batch * 100 + game
    rotation = (game - 1) % len(COURTS)
    players: list[dict[str, Any]] = []
    profiles: dict[str, dict[str, Any]] = {}
    index = 0
    for court_index, (player_id, controller, roles) in enumerate(COURTS):
        seat = (court_index + rotation) % len(COURTS) + 1
        players.append(
            {
                "player_id": player_id,
                "seat": seat,
                "controller_type": controller,
                "court_role_profiles": roles,
            }
        )
        for profile_name in roles.values():
            index += 1
            profiles[profile_name] = {
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
    for batch in range(1, 5):
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
    print(f"wrote and validated {written} game configs + 4 batch manifests")


if __name__ == "__main__":
    main()
