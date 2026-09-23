"""Generate experiment 11 game configs: greedy_script vs sane_random, 2v2 direct
head-to-head.

Rationale: experiments 8/9 (see Courts-Battle-config-details.md section 8) only
ran the two scripted floors in homogeneous 4-player games and compared their
aggregate seat-score distributions -- an indirect comparison with limited
power. This experiment lets the two floors compete in the SAME game (2 greedy
seats vs 2 sane_random seats), cancelling per-game luck the same way experiment
10 (court-fe-battle) does for court vs flat_ensemble, to directly test whether
the scripted "task has decision leverage" premise (Courts-Battle-config-details
.md section 7.3.1 question 2) holds at much higher statistical power (960
games vs 800+800 homogeneous games).

Design:
  * 960 games, seeds 20001-20960 (one seed per game), section 1.4 "5, 6 etc."
    reserved band -- does not overlap 2001-2800 (experiments 8/9) or 301-312
    (experiment 10).
  * Seats 1-4; each game assigns exactly 2 seats to greedy_script and 2 to
    sane_random. All C(4,2)=6 seat-pair configurations are used, each exactly
    160 times (960 / 6 = 160), rotated round-robin game-by-game (game i uses
    configuration (i-1) % 6), so every consecutive block of 6 games already
    covers all 6 pairs once. This keeps every 120-game batch internally
    balanced too (120 / 6 = 20 games per pair per batch).
  * 8 batches of 120 games each (seeds 20001-20120, 20121-20240, ...).
  * Game parameters mirror the frozen scripted-floor configs (initial_cash
    1500, initial_chance_cards=2, max_complete_rounds=50, classic Level 0)
    so results stay comparable to the section 8 floors and the LLM
    experiments' noise floor (section 8.5).

Every generated YAML is validated with load_game_config at generation time.

Usage (from repo root):
    .venv/Scripts/python.exe configs/experiments/scripted_benchmarks/greedy_vs_sane_random/generate_configs.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from monopoly_agent_battle.config.loader import load_game_config

HERE = Path(__file__).resolve().parent
EXPERIMENT_ID = "greedy_vs_sane_random"
GAME_ID_PREFIX = "greedy_vs_sane"

FIRST_SEED = 20001
TOTAL_GAMES = 960
GAMES_PER_BATCH = 120
BATCHES = TOTAL_GAMES // GAMES_PER_BATCH  # 8

ROUNDS_PER_GAME = 50
INITIAL_CHANCE_CARDS = 2

# All C(4,2)=6 seat-pair configurations: seats listed are greedy_script's
# seats; the other two seats take sane_random. Rotated round-robin so game i
# (1-indexed) uses SEAT_PAIRS[(i - 1) % 6].
SEAT_PAIRS: tuple[tuple[int, int], ...] = (
    (1, 2),
    (1, 3),
    (1, 4),
    (2, 3),
    (2, 4),
    (3, 4),
)


def build_game(global_index: int) -> dict[str, Any]:
    seed = FIRST_SEED + global_index - 1
    greedy_seats = SEAT_PAIRS[(global_index - 1) % len(SEAT_PAIRS)]
    players = []
    for seat in range(1, 5):
        controller_type = "greedy_script" if seat in greedy_seats else "sane_random"
        prefix = "greedy" if controller_type == "greedy_script" else "sane"
        players.append(
            {
                "player_id": f"{prefix}-{seat}",
                "seat": seat,
                "controller_type": controller_type,
            }
        )
    return {
        "game_id": f"{GAME_ID_PREFIX}-{global_index:03d}",
        "experiment_id": EXPERIMENT_ID,
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


def write_batch_manifest(batch_dir: Path, batch: int, entries: list[str]) -> None:
    first_global = (batch - 1) * GAMES_PER_BATCH + 1
    last_global = batch * GAMES_PER_BATCH
    first_seed = FIRST_SEED + first_global - 1
    last_seed = FIRST_SEED + last_global - 1
    rel_manifest = (
        f"configs/experiments/scripted_benchmarks/greedy_vs_sane_random/"
        f"batch{batch}/batch.yaml"
    )
    lines = [
        f"# {EXPERIMENT_ID} batch {batch} of {BATCHES}: "
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
    pair_counts: dict[tuple[int, int], int] = {pair: 0 for pair in SEAT_PAIRS}
    for batch in range(1, BATCHES + 1):
        batch_dir = HERE / f"batch{batch}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        entries: list[str] = []
        start = (batch - 1) * GAMES_PER_BATCH + 1
        for offset in range(GAMES_PER_BATCH):
            global_index = start + offset
            config = build_game(global_index)
            pair_counts[SEAT_PAIRS[(global_index - 1) % len(SEAT_PAIRS)]] += 1
            path = batch_dir / f"game_{global_index:03d}.yaml"
            path.write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            load_game_config(path)
            entries.append(path.name)
            written += 1
        write_batch_manifest(batch_dir, batch, entries)
    print(f"wrote and validated {written} game configs across {BATCHES} batches")
    print("seat-pair balance (greedy_script seats):")
    for pair, count in pair_counts.items():
        print(f"  {pair}: {count}")


if __name__ == "__main__":
    main()
