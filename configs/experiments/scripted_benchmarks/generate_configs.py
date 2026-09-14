"""Generate zero-cost scripted-benchmark game configs.

Two experiments defined in Courts-Battle-config-details.md section 8:
  * sane_random   : 4 sane_random players,   seeds 2001-2200 (200 games)
  * greedy_script : 4 greedy_script players, seeds 2201-2400 (200 games)

Each experiment is split into 8 batches of 25 games.  Game parameters mirror the
frozen 4-courts-battle configs (initial_chance_cards=2, max_complete_rounds=50)
so the resulting seat-score distributions are directly comparable to the formal
LLM experiments as a noise floor (section 8.5).

Every generated YAML is validated with load_game_config at generation time.

Usage (from repo root):
    .venv/Scripts/python.exe configs/experiments/scripted_benchmarks/generate_configs.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from monopoly_agent_battle.config.loader import load_game_config

# configs/experiments/  (parent of this script's scripted_benchmarks/ directory)
BASE = Path(__file__).resolve().parent.parent

GAMES_PER_BATCH = 25
BATCHES = 8
ROUNDS_PER_GAME = 50
INITIAL_CHANCE_CARDS = 2

EXPERIMENTS: list[dict[str, Any]] = [
    {
        "experiment_id": "sane_random",
        "controller_type": "sane_random",
        "player_prefix": "sane",
        "game_id_prefix": "sane",
        "first_seed": 2001,
    },
    {
        "experiment_id": "greedy_script",
        "controller_type": "greedy_script",
        "player_prefix": "greedy",
        "game_id_prefix": "greedy",
        "first_seed": 2201,
    },
]


def build_game(exp: dict[str, Any], global_index: int) -> dict[str, Any]:
    seed = exp["first_seed"] + global_index - 1
    players = [
        {
            "player_id": f"{exp['player_prefix']}-{seat}",
            "seat": seat,
            "controller_type": exp["controller_type"],
        }
        for seat in range(1, 5)
    ]
    return {
        "game_id": f"{exp['game_id_prefix']}-{global_index:03d}",
        "experiment_id": exp["experiment_id"],
        "seed": seed,
        "players": players,
        "initial_cash": 1500,
        "initial_chance_cards": INITIAL_CHANCE_CARDS,
        "max_complete_rounds": ROUNDS_PER_GAME,
        "rules_version": "classic-level0-v1",
        "rules_level": 0,
        "board_data_version": "classic-us-40-v1",
        "card_data_version": "classic-cards-v1",
        "output_directory": "runs",
    }


def write_batch_manifest(
    batch_dir: Path, exp: dict[str, Any], batch: int, entries: list[str]
) -> None:
    first_global = (batch - 1) * GAMES_PER_BATCH + 1
    last_global = batch * GAMES_PER_BATCH
    first_seed = exp["first_seed"] + first_global - 1
    last_seed = exp["first_seed"] + last_global - 1
    rel_manifest = (
        f"configs/experiments/{exp['experiment_id']}/batch{batch}/batch.yaml"
    )
    lines = [
        f"# {exp['experiment_id']} batch {batch} of {BATCHES}: "
        f"game_{first_global:03d} (seed {first_seed}) .. "
        f"game_{last_global:03d} (seed {last_seed})",
        "# Run from the repository root with:",
        f"#   .venv/Scripts/monopoly-agent-battle.exe experiment run --batch {rel_manifest}",
        "# tasks.jsonl is written next to this manifest.",
        "games:",
    ]
    lines.extend(f"  - {name}" for name in entries)
    (batch_dir / "batch.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    written = 0
    manifests = 0
    for exp in EXPERIMENTS:
        exp_dir = BASE / exp["experiment_id"]
        for batch in range(1, BATCHES + 1):
            batch_dir = exp_dir / f"batch{batch}"
            batch_dir.mkdir(parents=True, exist_ok=True)
            entries: list[str] = []
            start = (batch - 1) * GAMES_PER_BATCH + 1
            for offset in range(GAMES_PER_BATCH):
                global_index = start + offset
                config = build_game(exp, global_index)
                path = batch_dir / f"game_{global_index:03d}.yaml"
                path.write_text(
                    yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )
                load_game_config(path)
                entries.append(path.name)
                written += 1
            write_batch_manifest(batch_dir, exp, batch, entries)
            manifests += 1
    print(f"wrote and validated {written} game configs + {manifests} batch manifests")


if __name__ == "__main__":
    main()
