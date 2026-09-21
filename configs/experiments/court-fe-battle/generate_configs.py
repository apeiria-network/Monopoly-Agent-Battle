"""Generate experiment 10 (CF) game configs: ming court vs flat ensemble, 2v2.

Spec: Courts-Battle-config-details.md §9. 12 games = 6 court-seat pairs x 2,
base models A-D crossed (3 games each), seeds 301-312 (per §1.4 allocation and
the §9 table; the stray "165-176" in §9 prose is stale). All 16 role profiles
(2 courts x 4 roles + 2 FE x 4 roles) expand from the game's base model per
the §1.3 table (§9: "按第 1.3 节展开"): the four agents share the same CHIEF
model (emperor = leader = base model, "局内四个 Agent 基座相同"), while the
three subordinate posts of every agent take the remaining three models in the
§1.3 rotation (each agent internally uses A, B, C, D once).
Court side: ming (highest z vs baseline in experiment 2). Instance seat
convention: court-1/court-2 take the two court seats in ascending order,
fe-1/fe-2 take the two remaining seats in ascending order. Execution: 6
batches x 2 games, keeping the §9 interleaved order (CF01,06,08,02,07,09,03,
05,12,04,10,11) chunked into pairs. API key suffixes cycle KEY1-KEY4 by
batch: batch N uses KEY((N-1) mod 4 + 1).

Regenerate everything (from the repository root):
    .venv/Scripts/python.exe configs/experiments/court-fe-battle/generate_configs.py
"""

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
EXPERIMENT_ID = "court-fe-battle"

MODELS = {
    "A": ("qwen", "QWEN_URL", "QWEN_API_KEY", "qwen3.8-flash"),
    "B": ("deepseek", "DEEPSEEK_URL", "DEEPSEEK_API_KEY", "deepseek-v4-flash"),
    "C": ("gpt", "GPT_URL", "GPT_API_KEY", "gpt-5.6-luna"),
    "D": ("glm", "GLM_URL", "GLM_API_KEY", "GLM-5.3-Flash"),
}

# (game_id, seed, court_seats, base_model) — §9 table, seeds 301-312 per §1.4.
GAMES = [
    ("CF01", 301, (1, 2), "A"),
    ("CF02", 302, (2, 4), "B"),
    ("CF03", 303, (1, 4), "C"),
    ("CF04", 304, (3, 4), "D"),
    ("CF05", 305, (1, 3), "A"),
    ("CF06", 306, (1, 4), "B"),
    ("CF07", 307, (1, 2), "C"),
    ("CF08", 308, (1, 3), "D"),
    ("CF09", 309, (3, 4), "A"),
    ("CF10", 310, (2, 3), "B"),
    ("CF11", 311, (2, 4), "C"),
    ("CF12", 312, (2, 3), "D"),
]

BATCHES = {
    1: ["CF01", "CF06"],
    2: ["CF08", "CF02"],
    3: ["CF07", "CF09"],
    4: ["CF03", "CF05"],
    5: ["CF12", "CF04"],
    6: ["CF10", "CF11"],
}

COURT_ROLES = ("emperor", "chief_grand_secretary", "grand_secretary_1", "grand_secretary_2")
FE_ROLES = ("leader", "member_1", "member_2", "member_3")

# §1.3 expansion: given the game's base model, which model fills each post.
# chief = emperor (court) / leader (FE); post1-3 = the three subordinate roles.
EXPANSION = {
    "A": {"chief": "A", "post1": "B", "post2": "C", "post3": "D"},
    "B": {"chief": "B", "post1": "A", "post2": "D", "post3": "C"},
    "C": {"chief": "C", "post1": "D", "post2": "A", "post3": "B"},
    "D": {"chief": "D", "post1": "C", "post2": "B", "post3": "A"},
}

# Controller role name -> §1.3 expansion slot.
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
    game_id: str, seed: int, court_seats: tuple[int, int], model_key: str, key_suffix: int
) -> dict[str, object]:
    fe_seats = [s for s in (1, 2, 3, 4) if s not in court_seats]
    instances = [
        ("court-1", court_seats[0], "ming_court", COURT_ROLES, "court1"),
        ("court-2", court_seats[1], "ming_court", COURT_ROLES, "court2"),
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
            seed, court_seats, model_key = games_by_id[game_id]
            fe_seats = [s for s in (1, 2, 3, 4) if s not in court_seats]
            config = build_game(game_id, seed, court_seats, model_key, key_suffix)
            header = (
                f"# court-fe-battle {game_id} (experiment 10, seed {seed});"
                f" batch API KEY{key_suffix}\n"
                f"# seats: court-1={court_seats[0]} court-2={court_seats[1]}"
                f" fe-1={fe_seats[0]} fe-2={fe_seats[1]}\n"
                f"# base model {model_key}: all 4 agents' chief (emperor/leader) = {model_key},"
                " subordinate posts expand per §1.3\n"
                "# models: A=qwen3.8-flash B=deepseek-v4-flash C=gpt-5.6-luna D=GLM-5.3-Flash\n"
            )
            filename = f"game_{game_number:03d}.yaml"
            (batch_dir / filename).write_text(
                header + yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            entries.append(filename)
        batch_header = (
            f"# court-fe-battle batch {batch_index} of 6: "
            + ", ".join(f"{name} ({gid})" for name, gid in zip(entries, game_ids, strict=True))
            + f"; API KEY{key_suffix}\n"
            "# Run from the repository root with:\n"
            "#   .venv/Scripts/monopoly-agent-battle.exe experiment run"
            f" --batch configs/experiments/court-fe-battle/batch{batch_index}/batch.yaml\n"
            "# tasks.jsonl is written next to this manifest.\n"
        )
        (batch_dir / "batch.yaml").write_text(
            batch_header + yaml.safe_dump({"games": entries}, sort_keys=False),
            encoding="utf-8",
        )
        print(f"batch{batch_index}: {', '.join(game_ids)}")


if __name__ == "__main__":
    main()
