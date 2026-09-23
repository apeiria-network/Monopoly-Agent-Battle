"""Generate baseline-vs-greedy (single llm_baseline vs 3 greedy_script) game configs.

Answers Courts-Battle-config-details.md section 7.3.2's question "can LLM beat
a hard-coded rule": the single LLM baseline is the only player with a model,
the other 3 seats are all greedy_script (zero-cost, no API calls). Judgment
reuses the section-8 greedy_script floor's per-seat point distribution as the
null (the same 800-game floor already backing courts_analysis.py /
cvb_analysis.py / fe_analysis.py via floor_test.py) -- "1 focus player vs 3
equally-strong opponents".

Design (upgraded from the doc's original 4-game diagonal-only sketch to a
full 4x4 Latin square per user direction, 2026 revision): 16 games, seeds
101-116 -- reusing the doc's section-1.4 MIRROR segment shared with
experiment 1, experiment 2's first 16 games, experiment 3 (FE), and
experiments 5/7 (same seed -> same dice/deck mainline, cross-experiment
comparable, NOT the same seat/player assignment). Every one of the 16
(baseline seat, baseline model) combinations occurs exactly once: for seat s
(1-4) and "block" b (0-3, one per batch), model = LETTERS[(s - 1 + b) % 4].
Within each batch (block) all 4 seats and all 4 models each appear once;
across the 4 batches every seat cycles through all 4 models once.

Models A-D are frozen per Courts-Battle-config-details.md section 1.1:
  A=qwen3.8-flash  B=deepseek-v4-flash  C=gpt-5.6-luna  D=GLM-5.3-Flash
API key rotation: one key per batch, key_num = batch number (batches 1-4 use
KEY1-KEY4), matching the existing convention in court-vs-baseline's script.

Every generated YAML is validated with load_game_config at generation time.

Usage (from repo root):
    .venv/Scripts/python.exe configs/experiments/baseline-vs-greedy/generate_configs.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from monopoly_agent_battle.config.loader import load_game_config

EXP_DIR = Path(__file__).resolve().parent
EXPERIMENT_ID = "baseline-vs-greedy"

# Section 1.1 frozen mapping: letter -> (provider, model, base_url_env, api_key_env_prefix)
MODELS: dict[str, tuple[str, str, str, str]] = {
    "A": ("qwen", "qwen3.8-flash", "QWEN_URL", "QWEN_API_KEY"),
    "B": ("deepseek", "deepseek-v4-flash", "DEEPSEEK_URL", "DEEPSEEK_API_KEY"),
    "C": ("gpt", "gpt-5.6-luna", "GPT_URL", "GPT_API_KEY"),
    "D": ("glm", "GLM-5.3-Flash", "GLM_URL", "GLM_API_KEY"),
}
LETTERS = ("A", "B", "C", "D")

FIRST_SEED = 101
TOTAL_GAMES = 16
BATCHES = 4
GAMES_PER_BATCH = TOTAL_GAMES // BATCHES  # 4


def build_game(global_index: int, key_num: int) -> tuple[dict[str, Any], str, int]:
    """global_index is 1-based; returns (config dict, letter, seat)."""
    block = (global_index - 1) // GAMES_PER_BATCH  # 0..3, one per batch
    k = (global_index - 1) % GAMES_PER_BATCH  # 0..3, seat offset within batch
    seat = k + 1
    letter = LETTERS[(k + block) % 4]
    seed = FIRST_SEED + global_index - 1
    game_id = f"BVG{global_index:02d}"

    provider, model, url_env, key_env_prefix = MODELS[letter]
    players: list[dict[str, Any]] = []
    for s in range(1, 5):
        if s == seat:
            players.append(
                {
                    "player_id": "baseline",
                    "seat": s,
                    "controller_type": "llm_baseline",
                    "model_profile": "baseline-model",
                }
            )
        else:
            players.append(
                {
                    "player_id": f"greedy-{s}",
                    "seat": s,
                    "controller_type": "greedy_script",
                }
            )

    config: dict[str, Any] = {
        "game_id": game_id,
        "experiment_id": EXPERIMENT_ID,
        "seed": seed,
        "prompt_profile": "full-v2",
        "players": players,
        "model_profiles": {
            "baseline-model": {
                "provider": provider,
                "base_url_env": url_env,
                "api_key_env": f"{key_env_prefix}{key_num}",
                "model": model,
                "seed": 42,
                "max_tokens": 4096,
                "thinking": True,
                "timeout_seconds": 120,
            }
        },
        "initial_cash": 1500,
        "initial_chance_cards": 2,
        "max_complete_rounds": 50,
        "rules_version": "classic-level0-v1",
        "rules_level": 0,
        "board_data_version": "classic-us-40-v1",
        "card_data_version": "classic-cards-v1",
        "output_directory": "runs",
    }
    return config, letter, seat


def dump_game(config: dict[str, Any], letter: str, seat: int, global_index: int) -> str:
    header = (
        f"# {EXPERIMENT_ID} {config['game_id']} (game {global_index} of {TOTAL_GAMES}); "
        f"seed {config['seed']}\n"
        f"# seats: baseline@{seat} (model {letter}={MODELS[letter][1]}), "
        f"greedy_script fills the other 3 seats\n"
        "# floor reference for judgment: runs/greedy_script (section 8 800-game floor),\n"
        "# per floor_test.py's per-seat empirical point distribution\n"
    )
    return header + yaml.safe_dump(config, allow_unicode=True, sort_keys=False)


def write_batch_manifest(batch: int, entries: list[str]) -> str:
    first_global = (batch - 1) * GAMES_PER_BATCH + 1
    last_global = batch * GAMES_PER_BATCH
    key_num = batch
    rel_manifest = f"configs/experiments/{EXPERIMENT_ID}/batch{batch}/batch.yaml"
    lines = [
        f"# {EXPERIMENT_ID} batch {batch} of {BATCHES}: "
        f"game_{first_global:02d}..game_{last_global:02d}; API KEY{key_num}",
        "# Run from the repository root with:",
        f"#   .venv/Scripts/monopoly-agent-battle.exe experiment run --batch {rel_manifest}",
        "# tasks.jsonl is written next to this manifest.",
        "games:",
    ]
    lines.extend(f"  - {name}" for name in entries)
    return "\n".join(lines) + "\n"


def main() -> None:
    written = 0
    seat_model_seen: set[tuple[int, str]] = set()
    for batch in range(1, BATCHES + 1):
        bdir = EXP_DIR / f"batch{batch}"
        bdir.mkdir(parents=True, exist_ok=True)
        key_num = batch
        entries: list[str] = []
        start = (batch - 1) * GAMES_PER_BATCH + 1
        for offset in range(GAMES_PER_BATCH):
            global_index = start + offset
            config, letter, seat = build_game(global_index, key_num)
            seat_model_seen.add((seat, letter))
            fname = f"game_{global_index:02d}.yaml"
            (bdir / fname).write_text(
                dump_game(config, letter, seat, global_index), encoding="utf-8"
            )
            load_game_config(bdir / fname)
            entries.append(fname)
            written += 1
        (bdir / "batch.yaml").write_text(write_batch_manifest(batch, entries), encoding="utf-8")
    assert len(seat_model_seen) == 16, f"expected all 16 (seat,model) combos, got {len(seat_model_seen)}"
    print(f"generated {written} games across {BATCHES} batches in {EXP_DIR}")
    print(f"seat x model coverage: {len(seat_model_seen)}/16 distinct combos (all load-validated)")


if __name__ == "__main__":
    main()
