"""Ad-hoc analysis of runs/court-vs-baseline (64 games) per Courts-Battle-config-details.md.

Scoring follows §3/§5: final rank maps to points 3/2/1/0 (rank 1 = 3, rank 4 = 0),
luck expectation 1.5. Seat-calibrated Monte-Carlo null distributions follow §8.5.3:
for each real game, resample with replacement a floor game at the same seat and sum
points; the p-value is the share of luck-only totals reaching the observed total.

Run from the repository root:
    .venv/Scripts/python.exe stat/cvb_analysis.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from monopoly_agent_battle.game.board_data.classic_us_40 import (  # noqa: E402
    BOARD_BY_POSITION,
)

ROOT = Path(__file__).resolve().parent.parent / "runs"
CVB = ROOT / "court-vs-baseline"
COURTS = {"SH": "shang", "QI": "qin", "TA": "tang", "MI": "ming"}
COURT_CN = {"shang": "商", "qin": "秦", "tang": "唐", "ming": "明"}
POINTS = (3, 2, 1, 0)

GameRow = dict[str, Any]
SeatPoints = dict[int, list[int]]


def net_worth(result: dict[str, Any], player_id: str) -> int:
    """Net worth per the engine formula: cash + property prices + building costs
    minus full purchase price of mortgaged properties."""
    player = result["players"][player_id]
    props = result["properties"]
    total: int = player["cash"]
    for pos in player["properties"]:
        board = BOARD_BY_POSITION[int(pos)]
        total += board.price or 0
        state = props[str(pos)]
        total += (board.building_cost or 0) * state["building_level"]
        if state["mortgaged"]:
            total -= board.price or 0
    return total


def load_game(gdir: Path) -> GameRow:
    result: dict[str, Any] = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
    config: dict[str, Any] = json.loads((gdir / "config.json").read_text(encoding="utf-8"))[
        "config"
    ]
    players = config["players"]
    court_pl = next(p for p in players if p["controller_type"] != "llm_baseline")
    baselines = [p for p in players if p["controller_type"] == "llm_baseline"]
    profiles = config["model_profiles"]
    role_profiles: dict[str, Any] = court_pl.get("court_role_profiles") or {}
    emperor_profile: str | None = role_profiles.get("emperor")
    emperor_model = profiles[emperor_profile]["model"] if emperor_profile else None
    baseline_models = sorted({profiles[p["model_profile"]]["model"] for p in baselines})
    rankings = result["rankings"]  # best first
    rank_of = {pid: i + 1 for i, pid in enumerate(rankings)}
    gid = gdir.name
    court_key = COURTS[gid[:2]]
    per_player = result.get("llm_token_stats", {}).get("per_player", {})
    totals = result.get("llm_token_stats", {}).get("totals", {})

    def tok(pid_prefix: str) -> dict[str, int]:
        agg = {"calls": 0, "input": 0, "output": 0, "thinking": 0, "decisions": 0}
        for key, st in per_player.items():
            if key == pid_prefix or key.startswith(pid_prefix + "."):
                n: int = st.get("successful_calls", 0)
                agg["calls"] += n
                per_decision = st.get("per_decision", {})
                agg["input"] += int(
                    st.get("avg_cached_input_tokens", 0) * n
                    + st.get("avg_uncached_input_tokens", 0) * n
                )
                agg["output"] += int(st.get("avg_output_tokens", 0) * n)
                agg["thinking"] += int(st.get("avg_thinking_tokens", 0) * n)
                agg["decisions"] += per_decision.get("decisions", 0)
        return agg

    court_id: str = court_pl["player_id"]
    return {
        "game_id": gid,
        "court": court_key,
        "seed": config["seed"],
        "court_seat": court_pl["seat"],
        "emperor_model": emperor_model,
        "baseline_models": baseline_models,
        "validity": result["validity_status"],
        "status": result["status"],
        "end_reason": result["end_reason"],
        "rounds": result["complete_rounds"],
        "llm_calls": result["llm_calls"],
        "llm_fallbacks": result["llm_fallbacks"],
        "decision_fallbacks": result["decision_fallbacks"],
        "reconnect_events": result["reconnect_events"],
        "court_id": court_id,
        "court_rank": rank_of[court_id],
        "court_points": POINTS[rank_of[court_id] - 1],
        "court_bankrupt": result["players"][court_id]["bankrupt"],
        "court_survived": result["players"][court_id]["survived_turns"],
        "court_net_worth": net_worth(result, court_id),
        "baseline_points_total": sum(POINTS[rank_of[b["player_id"]] - 1] for b in baselines),
        "baseline_best_rank": min(rank_of[b["player_id"]] for b in baselines),
        "court_tokens": tok(court_id),
        "baseline_tokens": [tok(b["player_id"]) for b in baselines],
        "totals": totals,
    }


def load_floor(floor_dir: Path) -> tuple[SeatPoints, list[int]]:
    """Return ({seat: per-game points list}, complete-rounds list) over valid games."""
    seat_points: SeatPoints = {1: [], 2: [], 3: [], 4: []}
    rounds_list: list[int] = []
    for gdir in sorted(floor_dir.iterdir()):
        if not gdir.is_dir() or not (gdir / "result.json").exists():
            continue
        result: dict[str, Any] = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
        config: dict[str, Any] = json.loads((gdir / "config.json").read_text(encoding="utf-8"))[
            "config"
        ]
        seat_of = {p["player_id"]: p["seat"] for p in config["players"]}
        if result["validity_status"] != "valid":
            continue
        for i, pid in enumerate(result["rankings"]):
            seat_points[seat_of[pid]].append(POINTS[i])
        rounds_list.append(result["complete_rounds"])
    return seat_points, rounds_list


def mc_pvalue(
    seats: list[int],
    total: int,
    seat_points: SeatPoints,
    iters: int = 200_000,
    seed: int = 7,
) -> float:
    """P(luck-only seat-calibrated total >= observed) by §8.5.3 resampling."""
    rng = random.Random(seed)
    pools = {s: seat_points[s] for s in set(seats)}
    ge = 0
    for _ in range(iters):
        t = sum(rng.choice(pools[s]) for s in seats)
        if t >= total:
            ge += 1
    return ge / iters


def main() -> None:
    games: list[GameRow] = []
    for gdir in sorted(CVB.iterdir()):
        if not gdir.is_dir() or "-bak" in gdir.name or gdir.name.endswith(".invalid"):
            continue
        if not (gdir / "result.json").exists():
            print(f"MISSING result.json: {gdir.name}")
            continue
        games.append(load_game(gdir))
    print(f"loaded {len(games)} games")

    sane_seats, sane_rounds = load_floor(ROOT / "sane_random")
    greedy_seats, greedy_rounds = load_floor(ROOT / "greedy_script")
    for name, sp in (("sane", sane_seats), ("greedy", greedy_seats)):
        for s in range(1, 5):
            v = sp[s]
            mean = sum(v) / len(v)
            var = sum((x - mean) ** 2 for x in v) / (len(v) - 1)
            print(f"floor {name} seat {s}: n={len(v)} mean={mean:.4f} sd={var**0.5:.4f}")

    valid = [g for g in games if g["validity"] == "valid"]
    invalid = [g for g in games if g["validity"] != "valid"]
    print(f"valid={len(valid)} invalid={len(invalid)}")
    for g in invalid:
        print(
            f"  INVALID: {g['game_id']} status={g['status']} end={g['end_reason']} "
            f"fallbacks={g['llm_fallbacks']}/{g['llm_calls']}"
        )

    out_path = Path(__file__).resolve().parent / "cvb_games.json"
    out_path.write_text(
        json.dumps({"games": games}, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print("\n=== per-court aggregate (valid games) ===")
    for ck in ("shang", "qin", "tang", "ming"):
        gs = [g for g in valid if g["court"] == ck]
        n = len(gs)
        pts = sum(g["court_points"] for g in gs)
        ranks: list[int] = [g["court_rank"] for g in gs]
        nw = [g["court_net_worth"] for g in gs]
        surv = [g["court_survived"] for g in gs]
        bankrupt = sum(1 for g in gs if g["court_bankrupt"])
        wins = sum(1 for g in gs if g["court_rank"] == 1)
        top2 = sum(1 for g in gs if g["court_rank"] <= 2)
        calls = sum(g["court_tokens"]["calls"] for g in gs)
        decisions = sum(g["court_tokens"]["decisions"] for g in gs)
        inp = sum(g["court_tokens"]["input"] for g in gs)
        outp = sum(g["court_tokens"]["output"] for g in gs)
        think = sum(g["court_tokens"]["thinking"] for g in gs)
        fb = sum(g["llm_fallbacks"] for g in gs)
        recon = sum(g["reconnect_events"] for g in gs)
        seats = [g["court_seat"] for g in gs]
        p_sane = mc_pvalue(seats, pts, sane_seats)
        p_greedy = mc_pvalue(seats, pts, greedy_seats)
        rdist = {r: ranks.count(r) for r in (1, 2, 3, 4)}
        print(
            f"{COURT_CN[ck]} n={n} pts={pts} (mean {pts / n:.3f}) rank_dist={rdist} "
            f"wins={wins} top2={top2} bankrupt={bankrupt} "
            f"nw_mean={sum(nw) / n:.0f} surv_mean={sum(surv) / n:.1f} "
            f"calls={calls} dec={decisions} calls/dec={calls / max(decisions, 1):.2f} "
            f"in={inp / 1e6:.2f}M out={outp / 1e6:.2f}M think={think / 1e6:.2f}M "
            f"fallbacks={fb} reconnects={recon} p_sane={p_sane:.4f} p_greedy={p_greedy:.4f}"
        )

    print("\n=== overall ===")
    tot_court = sum(g["court_points"] for g in valid)
    tot_base = sum(g["baseline_points_total"] for g in valid)
    print(
        f"court points total={tot_court} mean/game={tot_court / len(valid):.3f} "
        f"(luck expectation 1.5); baseline avg per player={tot_base / len(valid) / 3:.3f}"
    )
    court_first = sum(1 for g in valid if g["court_rank"] == 1)
    court_last = sum(1 for g in valid if g["court_rank"] == 4)
    print(f"court rank1={court_first}/{len(valid)} rank4={court_last}/{len(valid)}")

    print("\n=== by emperor/baseline model ===")
    by_model: dict[str, list[GameRow]] = {}
    for g in valid:
        by_model.setdefault(g["emperor_model"], []).append(g)
    for m in sorted(by_model):
        gs = by_model[m]
        pts = sum(g["court_points"] for g in gs)
        bpts = sum(g["baseline_points_total"] for g in gs) / 3
        first = sum(1 for g in gs if g["court_rank"] == 1)
        print(
            f"{m}: n={len(gs)} court_pts={pts} mean={pts / len(gs):.3f} "
            f"baseline_mean={bpts / len(gs):.3f} court_rank1={first}"
        )

    print("\n=== by court seat ===")
    by_seat: dict[int, list[GameRow]] = {}
    for g in valid:
        by_seat.setdefault(g["court_seat"], []).append(g)
    for s in sorted(by_seat):
        gs = by_seat[s]
        pts = sum(g["court_points"] for g in gs)
        print(f"seat {s}: n={len(gs)} court mean={pts / len(gs):.3f}")

    print("\n=== court x emperor model ===")
    for ck in ("shang", "qin", "tang", "ming"):
        line = [COURT_CN[ck]]
        for m in sorted(by_model):
            gs = [g for g in valid if g["court"] == ck and g["emperor_model"] == m]
            pts = sum(g["court_points"] for g in gs)
            line.append(f"{m}: {pts}/{len(gs) * 3}")
        print("  ".join(line))

    print("\n=== game length / end reason ===")
    rl = sum(1 for g in valid if g["end_reason"] == "round_limit")
    rounds_mean = sum(g["rounds"] for g in valid) / len(valid)
    print(f"round_limit={rl}/{len(valid)}; rounds mean={rounds_mean:.1f}")
    print(
        f"sane floor rounds mean={sum(sane_rounds) / len(sane_rounds):.1f} "
        f"greedy floor rounds mean={sum(greedy_rounds) / len(greedy_rounds):.1f}"
    )

    print("\n=== per-game rows ===")
    for g in games:
        print(
            f"{g['game_id']} {COURT_CN[g['court']]} seat{g['court_seat']} "
            f"emp={g['emperor_model']} valid={g['validity']} rank={g['court_rank']} "
            f"pts={g['court_points']} nw={g['court_net_worth']} surv={g['court_survived']} "
            f"rounds={g['rounds']} end={g['end_reason']} calls={g['llm_calls']} "
            f"fb={g['llm_fallbacks']} recon={g['reconnect_events']}"
        )


if __name__ == "__main__":
    main()
