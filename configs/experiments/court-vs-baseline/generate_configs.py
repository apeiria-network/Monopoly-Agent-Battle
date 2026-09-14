"""Generate court-vs-baseline (Section 3.2, 16 games) benchmark configs.

Per ``MonopolyAgentBattle_developer_docs/Courts-Battle-config-details.md`` §3.2:
16 games, seeds 101-116; each game = 1 court (full Agent) at its seat plus 3
single-LLM ``llm_baseline`` players filling the other seats. Court emperor model
is the game's base model; the 3 ministers expand per Section 1.3; the 3 baselines
all use the emperor model (same-model control).

Models A/B/C/D are frozen per Section 1.1:
  A=qwen3.8-flash  B=deepseek-v4-flash  C=gpt-5.6-luna  D=GLM-5.3-Flash
Each provider has 4 keys; per the confirmed batch plan every profile in a batch
uses one key number, key = (batch-1) % 4 + 1, cycling 1,2,3,4,1,2,3,4 across the
8 batches (2 games each).

Every generated YAML is validated with load_game_config at generation time.

Usage (from repo root):
    .venv/Scripts/python.exe configs/experiments/court-vs-baseline/generate_configs.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from monopoly_agent_battle.config.loader import load_game_config

EXP_DIR = Path(__file__).resolve().parent
EXPERIMENT_ID = "court-vs-baseline"

# Section 1.1 frozen mapping: letter -> (provider, model, base_url_env, api_key_env_prefix)
MODELS: dict[str, tuple[str, str, str, str]] = {
    "A": ("qwen", "qwen3.8-flash", "QWEN_URL", "QWEN_API_KEY"),
    "B": ("deepseek", "deepseek-v4-flash", "DEEPSEEK_URL", "DEEPSEEK_API_KEY"),
    "C": ("gpt", "gpt-5.6-luna", "GPT_URL", "GPT_API_KEY"),
    "D": ("glm", "GLM-5.3-Flash", "GLM_URL", "GLM_API_KEY"),
}

# Section 1.3 expansion: emperor (base) model -> (post1, post2, post3) minister models
EXPANSION: dict[str, tuple[str, str, str]] = {
    "A": ("B", "C", "D"),
    "B": ("A", "D", "C"),
    "C": ("D", "A", "B"),
    "D": ("C", "B", "A"),
}

# court -> (controller_type, role keys (post1,post2,post3,emperor),
#           player_prefix, profile names (post1,post2,post3,emperor))
COURTS: dict[str, tuple[str, tuple[str, str, str, str], str, tuple[str, str, str, str]]] = {
    "商": (
        "shang2_court",
        ("minister_1", "minister_2", "minister_3", "emperor"),
        "shang",
        ("shang-minister-1", "shang-minister-2", "shang-minister-3", "shang-emperor"),
    ),
    "秦": (
        "qin_court",
        ("chancellor", "grand_marshal", "imperial_counsellor", "emperor"),
        "qin",
        ("qin-chancellor", "qin-grand-marshal", "qin-imperial-counsellor", "qin-emperor"),
    ),
    "唐": (
        "tang_court",
        ("shangshu", "zhongshu", "menxia", "emperor"),
        "tang",
        ("tang-shangshu", "tang-zhongshu", "tang-menxia", "tang-emperor"),
    ),
    "明": (
        "ming_court",
        ("chief_grand_secretary", "grand_secretary_1", "grand_secretary_2", "emperor"),
        "ming",
        (
            "ming-chief-grand-secretary",
            "ming-grand-secretary-1",
            "ming-grand-secretary-2",
            "ming-emperor",
        ),
    ),
}

# Section 3.2 table: (global game no, game_id, seed, court, seat, emperor model)
GameSpec = tuple[int, str, int, str, int, str]
GAMES: list[GameSpec] = [
    (1, "SH01", 101, "商", 1, "A"),
    (2, "QI01", 102, "秦", 1, "B"),
    (3, "TA01", 103, "唐", 1, "C"),
    (4, "MI01", 104, "明", 1, "D"),
    (5, "SH02", 105, "商", 2, "B"),
    (6, "QI02", 106, "秦", 2, "C"),
    (7, "TA02", 107, "唐", 2, "D"),
    (8, "MI02", 108, "明", 2, "A"),
    (9, "SH03", 109, "商", 3, "C"),
    (10, "QI03", 110, "秦", 3, "D"),
    (11, "TA03", 111, "唐", 3, "A"),
    (12, "MI03", 112, "明", 3, "B"),
    (13, "SH04", 113, "商", 4, "D"),
    (14, "QI04", 114, "秦", 4, "A"),
    (15, "TA04", 115, "唐", 4, "B"),
    (16, "MI04", 116, "明", 4, "C"),
]

GAMES_PER_BATCH = 2
BATCHES = 8


def model_profile(letter: str, key_num: int) -> dict[str, Any]:
    provider, model, url_env, key_prefix = MODELS[letter]
    return {
        "provider": provider,
        "base_url_env": url_env,
        "api_key_env": f"{key_prefix}{key_num}",
        "model": model,
        "seed": 42,
        "max_tokens": 4096,
        "thinking": True,
        "timeout_seconds": 120,
    }


def build_game(entry: GameSpec, key_num: int) -> dict[str, Any]:
    _no, game_id, seed, court_char, seat, emperor = entry
    controller_type, role_keys, player_prefix, profile_names = COURTS[court_char]
    post_models = EXPANSION[emperor]

    players: list[dict[str, Any]] = []
    baseline_idx = 0
    for s in (1, 2, 3, 4):
        if s == seat:
            crp = {role_keys[j]: profile_names[j] for j in range(4)}
            players.append(
                {
                    "player_id": f"{player_prefix}-court",
                    "seat": s,
                    "controller_type": controller_type,
                    "court_role_profiles": crp,
                }
            )
        else:
            i = baseline_idx
            players.append(
                {
                    "player_id": f"baseline-{i + 1}",
                    "seat": s,
                    "controller_type": "llm_baseline",
                    "model_profile": f"baseline-model-{i + 1}",
                }
            )
            baseline_idx += 1

    profiles: dict[str, dict[str, Any]] = {}
    for j in range(3):
        profiles[profile_names[j]] = model_profile(post_models[j], key_num)
    profiles[profile_names[3]] = model_profile(emperor, key_num)
    for i in (1, 2, 3):
        profiles[f"baseline-model-{i}"] = model_profile(emperor, key_num)

    return {
        "game_id": game_id,
        "experiment_id": EXPERIMENT_ID,
        "seed": seed,
        "prompt_profile": "full-v2",
        "players": players,
        "model_profiles": profiles,
        "initial_cash": 1500,
        "max_complete_rounds": 50,
        "initial_chance_cards": 2,
        "rules_version": "classic-level0-v1",
        "rules_level": 0,
        "board_data_version": "classic-us-40-v1",
        "card_data_version": "classic-cards-v1",
        "output_directory": "runs",
    }


def game_comment(entry: GameSpec, key_num: int) -> str:
    no, game_id, _seed, court_char, seat, emperor = entry
    controller_type, role_keys, _prefix, _pn = COURTS[court_char]
    post_models = EXPANSION[emperor]
    seatmap: dict[int, str] = {seat: court_char}
    other_seats = [s for s in (1, 2, 3, 4) if s != seat]
    for i, s in enumerate(other_seats):
        seatmap[s] = f"baseline-{i + 1}"
    seats_line = " ".join(f"{s}={seatmap[s]}" for s in (1, 2, 3, 4))
    return (
        f"# court-vs-baseline {game_id} (game {no}); batch API KEY{key_num}\n"
        f"# seats: {seats_line}\n"
        f"# {court_char} base={emperor} ({controller_type}); "
        f"3 baselines all use emperor model {emperor}\n"
        f"# posts expanded per Courts-Battle-config-details.md 1.3: "
        f"{role_keys[0]}={post_models[0]} {role_keys[1]}={post_models[1]} "
        f"{role_keys[2]}={post_models[2]} {role_keys[3]}={emperor}\n"
        "# models: A=qwen3.8-flash B=deepseek-v4-flash C=gpt-5.6-luna D=GLM-5.3-Flash\n"
    )


def dump_game(game: dict[str, Any], comment: str) -> str:
    body = yaml.safe_dump(game, allow_unicode=True, sort_keys=False, width=100)
    return comment + body


def write_batch_manifest(batch_num: int, game_files: list[str]) -> str:
    key_num = (batch_num - 1) % 4 + 1
    lines = [
        f"# court-vs-baseline batch {batch_num} of {BATCHES}: "
        f"{len(game_files)} games; all profiles use API KEY{key_num}",
        "# Section 5.2: randomly shuffle execution order at runtime, not in this manifest.",
        "games:",
    ]
    for gf in game_files:
        lines.append(f"  - {gf}")
    return "\n".join(lines) + "\n"


def main() -> None:
    for b in range(1, BATCHES + 1):
        bdir = EXP_DIR / f"batch{b}"
        bdir.mkdir(parents=True, exist_ok=True)
        key_num = (b - 1) % 4 + 1
        game_files: list[str] = []
        for i in range(GAMES_PER_BATCH):
            entry = GAMES[(b - 1) * GAMES_PER_BATCH + i]
            no = entry[0]
            fname = f"game_{no:03d}.yaml"
            game = build_game(entry, key_num)
            text = dump_game(game, game_comment(entry, key_num))
            (bdir / fname).write_text(text, encoding="utf-8")
            load_game_config(bdir / fname)
            game_files.append(fname)
        (bdir / "batch.yaml").write_text(write_batch_manifest(b, game_files), encoding="utf-8")
    print(
        f"generated {len(GAMES)} games across {BATCHES} batches in {EXP_DIR} (all load-validated)"
    )


if __name__ == "__main__":
    main()
