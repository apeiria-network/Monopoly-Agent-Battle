"""Generate the ming-ablation vs flat-ensemble 2v2 game configs (12 games).

Design (confirmed by the project owner, 2026-09):
* 2 v 2: two ``ming_ablation_court`` instances (``mab-1``/``mab-2``) versus two
  ``flat_ensemble`` instances (``fe-1``/``fe-2``); four-player standard rules,
  each side's two sessions are independent and share no information ("faction"
  is only a post-hoc statistics unit).
* 12 games = 6 court-seat configurations x 2, base models A-D 3 games each.
* Mirrors ``configs/experiments/court-fe-battle`` (experiment 10) game by game:
  same seeds 301-312, same court seat pair and same base model per index, so
  that MAB0x and CF0x form a same-seed / same-seat / same-model pair
  ("full ming court" vs "ming ablation") against the same opponent type.
* All 16 role profiles (2 x ming-ablation x 4 roles + 2 x FE x 4 roles) expand
  from the game's base model per Courts-Battle-config-details.md 1.3: the four
  agents share the same chief model (emperor = leader = base model) while the
  three subordinate posts of every agent take the remaining three models in the
  1.3 rotation (each agent internally uses A, B, C, D once).
* Seat convention: ``mab-1``/``mab-2`` take the two court seats in ascending
  order, ``fe-1``/``fe-2`` take the two remaining seats in ascending order.
* Execution layout: 6 batches x 2 games (simple sequential chunking). Every
  model profile inside one batch file uses the same API key suffix; the suffix
  cycles KEY1-KEY4 by batch: batch N uses KEY((N-1) mod 4 + 1).
* The ablation is the current ``ming_ablation_court`` implementation: drafts +
  redraft + weighted vote kept, the chief's summary-advice step removed.

Game parameters match the court-fe-battle / CF experiment: initial_cash=1500,
max_complete_rounds=50, initial_chance_cards=2, classic-level0-v1,
rules_level=0, classic-us-40-v1 board, classic-cards-v1 cards, prompt
profile full-v2.

This script only writes YAML and validates every generated file with
``load_game_config`` (no LLM calls, no game execution).

Regenerate everything (from the repository root):
    .venv/Scripts/python.exe configs/experiments/ming-ablation-vs-fe/generate_configs.py
"""

from pathlib import Path

import yaml

from monopoly_agent_battle.config.loader import load_game_config

HERE = Path(__file__).resolve().parent
EXPERIMENT_ID = "ming-ablation-vs-fe"

MODELS = {
    "A": ("qwen", "QWEN_URL", "QWEN_API_KEY", "qwen3.8-flash"),
    "B": ("deepseek", "DEEPSEEK_URL", "DEEPSEEK_API_KEY", "deepseek-v4-flash"),
    "C": ("gpt", "GPT_URL", "GPT_API_KEY", "gpt-5.6-luna"),
    "D": ("glm", "GLM_URL", "GLM_API_KEY", "GLM-5.3-Flash"),
}

# (game_id, seed, mab_seats, base_model) — mirrored from court-fe-battle CF01-CF12
# (same seed, same court seat pair, same base model per index).
GAMES = [
    ("MAB01", 301, (1, 2), "A"),
    ("MAB02", 302, (2, 4), "B"),
    ("MAB03", 303, (1, 4), "C"),
    ("MAB04", 304, (3, 4), "D"),
    ("MAB05", 305, (1, 3), "A"),
    ("MAB06", 306, (1, 4), "B"),
    ("MAB07", 307, (1, 2), "C"),
    ("MAB08", 308, (1, 3), "D"),
    ("MAB09", 309, (3, 4), "A"),
    ("MAB10", 310, (2, 3), "B"),
    ("MAB11", 311, (2, 4), "C"),
    ("MAB12", 312, (2, 3), "D"),
]

BATCHES = {
    1: ["MAB01", "MAB02"],
    2: ["MAB03", "MAB04"],
    3: ["MAB05", "MAB06"],
    4: ["MAB07", "MAB08"],
    5: ["MAB09", "MAB10"],
    6: ["MAB11", "MAB12"],
}

MAB_ROLES = ("emperor", "chief_grand_secretary", "grand_secretary_1", "grand_secretary_2")
FE_ROLES = ("leader", "member_1", "member_2", "member_3")

# Courts-Battle-config-details.md 1.3 expansion: given the game's base model,
# which model fills each post. chief = emperor (ming-ablation) / leader (FE);
# post1-3 = the three subordinate roles in the order listed above.
EXPANSION = {
    "A": {"chief": "A", "post1": "B", "post2": "C", "post3": "D"},
    "B": {"chief": "B", "post1": "A", "post2": "D", "post3": "C"},
    "C": {"chief": "C", "post1": "D", "post2": "A", "post3": "B"},
    "D": {"chief": "D", "post1": "C", "post2": "B", "post3": "A"},
}

# Controller role name -> 1.3 expansion slot.
ROLE_SLOT = {
    "emperor": "chief",
    "chief_grand_secretary": "post1",
    "grand_secretary_1": "post2",
    "grand_secretary_2": "post3",
    "leader": "chief",
    "member_1": "post1",
    "member_2": "post2",
    "member_3": "post3",
}


def model_profile(model_key: str, key_suffix: int) -> dict[str, object]:
    provider, base_url_env, api_key_env, model = MODELS[model_key]
    return {
        "provider": provider,
        "base_url_env": base_url_env,
        "api_key_env": f"{api_key_env}{key_suffix}",
        "model": model,
        "seed": 42,
        "max_tokens": 4096,
        "thinking": True,
        "timeout_seconds": 120,
    }


def build_game(
    game_id: str, seed: int, mab_seats: tuple[int, int], model_key: str, key_suffix: int
) -> dict[str, object]:
    fe_seats = [s for s in (1, 2, 3, 4) if s not in mab_seats]
    instances = [
        ("mab-1", mab_seats[0], "ming_ablation_court", MAB_ROLES, "mab1"),
        ("mab-2", mab_seats[1], "ming_ablation_court", MAB_ROLES, "mab2"),
        ("fe-1", fe_seats[0], "flat_ensemble", FE_ROLES, "fe1"),
        ("fe-2", fe_seats[1], "flat_ensemble", FE_ROLES, "fe2"),
    ]
    players: list[dict[str, object]] = []
    profiles: dict[str, object] = {}
    for player_id, seat, controller, roles, prefix in instances:
        role_profiles: dict[str, str] = {}
        for role in roles:
            profile_name = f"{prefix}-{role.replace('_', '-')}"
            role_profiles[role] = profile_name
            role_model = EXPANSION[model_key][ROLE_SLOT[role]]
            profiles[profile_name] = model_profile(role_model, key_suffix)
        players.append(
            {
                "player_id": player_id,
                "seat": seat,
                "controller_type": controller,
                "court_role_profiles": role_profiles,
            }
        )
    players.sort(key=lambda p: int(p["seat"]))
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


def main() -> None:
    games_by_id = {gid: (seed, seats, model) for gid, seed, seats, model in GAMES}
    game_number = 0
    for batch_index, game_ids in BATCHES.items():
        key_suffix = (batch_index - 1) % 4 + 1
        batch_dir = HERE / f"batch{batch_index}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        entries: list[str] = []
        for game_id in game_ids:
            game_number += 1
            seed, mab_seats, model_key = games_by_id[game_id]
            fe_seats = [s for s in (1, 2, 3, 4) if s not in mab_seats]
            config = build_game(game_id, seed, mab_seats, model_key, key_suffix)
            header = (
                f"# ming-ablation-vs-fe {game_id} (seed {seed}); batch API KEY{key_suffix}\n"
                f"# seats: mab-1={mab_seats[0]} mab-2={mab_seats[1]}"
                f" fe-1={fe_seats[0]} fe-2={fe_seats[1]}\n"
                f"# mirror of court-fe-battle CF{game_number:02d}: same seed, same court seats,"
                f" same base model {model_key}\n"
                f"# base model {model_key}: all 4 agents' chief (emperor/leader) = {model_key},"
                " subordinate posts expand per Courts-Battle-config-details.md 1.3\n"
                "# models: A=qwen3.8-flash B=deepseek-v4-flash C=gpt-5.6-luna D=GLM-5.3-Flash\n"
            )
            filename = f"game_{game_number:03d}.yaml"
            path = batch_dir / filename
            path.write_text(
                header + yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            load_game_config(path)
            entries.append(filename)
        batch_header = (
            f"# ming-ablation-vs-fe batch {batch_index} of 6: "
            + ", ".join(f"{name} ({gid})" for name, gid in zip(entries, game_ids, strict=True))
            + f"; every profile in this batch uses API KEY{key_suffix}\n"
            "# Run from the repository root with:\n"
            "#   .venv/Scripts/monopoly-agent-battle.exe experiment run"
            f" --batch configs/experiments/ming-ablation-vs-fe/batch{batch_index}/batch.yaml\n"
            "# tasks.jsonl is written next to this manifest.\n"
        )
        (batch_dir / "batch.yaml").write_text(
            batch_header + yaml.safe_dump({"games": entries}, sort_keys=False),
            encoding="utf-8",
        )
        print(f"batch{batch_index}: {', '.join(game_ids)} (KEY{key_suffix})")
    print(f"generated {len(GAMES)} games across {len(BATCHES)} batches in {HERE} (all load-validated)")


if __name__ == "__main__":
    main()