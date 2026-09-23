"""Decision audit: LLM-vs-greedy-script agreement on recorded decisions.

Algorithm (all offline; no engine, no API calls):

1. Rebuild each recorded decision's DecisionRequest verbatim from the run's
   decisions.jsonl (the request JSON is a complete snapshot of the situation:
   legal options, target legal values, visible state).
2. Feed the rebuilt request to the GreedyScriptController (the scripted-floor
   heuristic) seeded by sha256(decision_id) for reproducibility; its reply is
   validated with the same parse_and_validate as any LLM reply, yielding the
   reference option + target for THIS exact situation.
3. Score per decision:
   - option_agree: final executed option == greedy option (1/0)
   - target_agree: option matches AND target dict matches (1/0)
   - random_floor: 1 / #options — the probability that a uniform random
     choice accidentally matches the greedy option
   - draft_agree: among the decision's court-trace drafts (officer drafts /
     FE member advice), the fraction matching the greedy option
4. Headline metric: signal = mean(option_agree) - mean(random_floor) over
   NON-TRIVIAL decisions (n_options > 1; single-option decisions are trivially
   100% with floor 1 and only dilute).  Signal = policy content above chance:
   uniform random play scores ~0 (verified on the fake-LLM batches), perfect
   greedy imitation scores ~1 - floor.
5. Arm comparison: decisions within a game are correlated (same players,
   models, deck), so the resampling unit is the GAME, not the decision:
   per-game per-arm mean signal -> paired per-game differences -> 10k
   game-cluster bootstrap -> CI95, plus Cliff's delta on the paired diffs.
   Effective n = #games regardless of decision count.

Boundary: the greedy script is a fixed heuristic REFERENCE, not ground truth;
agreement measures similarity to one reasonable policy, not correctness.
Outcome-valued scoring (rollout evaluation) is a separate, heavier method.

Outputs (exactly two files, fixed names, overwritten each run):
- stat/decision_audit_summary.csv — one block per experiment, grouped by
  controller_type and by base_model; experiments are never pooled.
- stat/decision_audit_paired.csv — all pairwise arm comparisons computed per
  experiment (per-game arm signals -> paired game diffs -> game-cluster
  bootstrap CI95 + Cliff's delta).

Usage from the repository root (no args = the four formal experiments):
    .venv/Scripts/python.exe stat/decision_audit.py [runs/<experiment> ...]
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any, cast

import numpy as np

from monopoly_agent_battle.agents.greedy_script import GreedyScriptController
from monopoly_agent_battle.decision.models import (
    DecisionKind,
    DecisionOption,
    DecisionRequest,
    OptionTarget,
)
from monopoly_agent_battle.decision.protocol import parse_and_validate

ROOT = Path(__file__).resolve().parent.parent
DRAFT_TYPES = {"draft", "advice"}
LEADER_ROLE = {"flat_ensemble": "leader"}
BOOT_RESAMPLES = 10_000
BASE_LETTER = {
    "qwen3.8-flash": "A",
    "deepseek-v4-flash": "B",
    "gpt-5.6-luna": "C",
    "glm-5.3-flash": "D",
}


def request_from_record(record: dict[str, Any]) -> DecisionRequest:
    options: list[DecisionOption] = []
    for raw in cast(list[dict[str, Any]], record["options"]):
        target_raw = cast(dict[str, Any] | None, raw.get("target"))
        target = (
            OptionTarget(
                kind=cast(str, target_raw["kind"]),
                fields=tuple(cast(list[str], target_raw["fields"])),
                command_fields=tuple(cast(list[str], target_raw["command_fields"])),
                legal_values=tuple(
                    tuple(values) for values in cast(list[list[object]], target_raw["legal_values"])
                ),
            )
            if target_raw is not None
            else None
        )
        options.append(
            DecisionOption(
                option_id=cast(str, raw["option_id"]),
                command_type=cast(str, raw["command_type"]),
                parameters=cast(dict[str, object], raw["parameters"]),
                title=cast(str, raw["title"]),
                preview=cast(str, raw["preview"]),
                response_format=cast(dict[str, object], raw["response_format"]),
                is_default=bool(raw.get("is_default", False)),
                target=target,
            )
        )
    return DecisionRequest(
        decision_id=cast(str, record["decision_id"]),
        game_id=cast(str, record["game_id"]),
        complete_rounds=int(cast(int, record["complete_rounds"])),
        player_id=cast(str, record["player_id"]),
        phase=cast(str, record["phase"]),
        kind=DecisionKind(cast(str, record["kind"])),
        question=cast(str, record["question"]),
        visible_state=cast(dict[str, object], record["visible_state"]),
        options=tuple(options),
        output_constraints=cast(dict[str, object], record["output_constraints"]),
    )


def greedy_choice(request: DecisionRequest) -> tuple[str, dict[str, object]]:
    seed = int.from_bytes(hashlib.sha256(request.decision_id.encode()).digest()[:8], "big")
    controller = GreedyScriptController(random.Random(seed))
    reply = controller(request)
    validation = parse_and_validate(reply, request)
    if not validation.valid or validation.option is None:
        raise AssertionError(f"greedy script failed its own request: {validation.error}")
    return validation.option.option_id, validation.target or {}


def selected_option_from_content(content: str | None) -> str | None:
    if not content:
        return None
    try:
        payload: Any = json.loads(content)
    except json.JSONDecodeError:
        return None
    selected = cast(dict[str, Any], payload).get("selected_option")
    if isinstance(selected, dict):
        option = cast(dict[str, Any], selected).get("option")
        return option if isinstance(option, str) else None
    return selected if isinstance(selected, str) else None


def norm_target(target: object) -> dict[str, object]:
    return cast(dict[str, object], target) if isinstance(target, dict) else {}


def audit_run(run_dir: Path, experiment: str) -> list[dict[str, Any]]:
    result_path = run_dir / "result.json"
    if not result_path.exists():
        return []
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("validity_status") != "valid":
        return []
    config = cast(
        dict[str, Any],
        json.loads((run_dir / "config.json").read_text(encoding="utf-8")),
    )["config"]
    players = {p["player_id"]: p for p in cast(list[dict[str, Any]], config["players"])}
    profiles = cast(dict[str, Any], config.get("model_profiles") or {})

    def base_model(player_id: str) -> str:
        player = players[player_id]
        controller = cast(str | None, player.get("controller_type")) or ""
        role_profiles = cast(dict[str, str], player.get("court_role_profiles") or {})
        profile_name = role_profiles.get(LEADER_ROLE.get(controller) or "emperor")
        if profile_name is None:
            profile_name = cast(str | None, player.get("model_profile"))
        model = cast(dict[str, str], profiles.get(profile_name or "") or {}).get("model", "")
        return BASE_LETTER.get(model.lower(), model)

    rows: list[dict[str, Any]] = []
    decisions_path = run_dir / "decisions.jsonl"
    for line in decisions_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        request = request_from_record(record["request"])
        greedy_option, greedy_target = greedy_choice(request)
        validation = cast(dict[str, Any], record["validation"])
        chosen = cast(str | None, validation.get("selected_option"))
        chosen_target = norm_target(validation.get("target"))
        n_options = len(request.options)
        trace = cast(dict[str, Any] | None, record.get("court_trace"))
        drafts: list[str] = []
        if trace:
            for call in cast(list[dict[str, Any]], trace.get("calls", [])):
                if call.get("content_type") in DRAFT_TYPES:
                    option = selected_option_from_content(cast(str | None, call.get("content")))
                    if option is not None:
                        drafts.append(option)
        rows.append(
            {
                "experiment": experiment,
                "game_id": cast(str, config["game_id"]),
                "decision_id": request.decision_id,
                "round": request.complete_rounds,
                "kind": request.kind.value,
                "player_id": request.player_id,
                "controller_type": cast(
                    str | None, players[request.player_id].get("controller_type")
                )
                or "",
                "base_model": base_model(request.player_id),
                "n_options": n_options,
                "chosen_option": chosen,
                "greedy_option": greedy_option,
                "option_agree": int(chosen == greedy_option),
                "target_agree": int(chosen == greedy_option and chosen_target == greedy_target),
                "random_floor": round(1.0 / n_options, 6),
                "n_drafts": len(drafts),
                "draft_agree": (
                    round(sum(1 for d in drafts if d == greedy_option) / len(drafts), 6)
                    if drafts
                    else ""
                ),
            }
        )
    return rows


def game_signal(rows: list[dict[str, Any]], controller: str) -> dict[str, float]:
    """Per-game mean (agree - floor) over non-trivial decisions for one arm."""
    signals: dict[str, float] = {}
    games = sorted({cast(str, r["game_id"]) for r in rows})
    for game in games:
        subset = [
            r
            for r in rows
            if r["game_id"] == game
            and r["controller_type"] == controller
            and int(r["n_options"]) > 1
        ]
        if subset:
            signals[game] = float(
                np.mean([float(r["option_agree"]) - float(r["random_floor"]) for r in subset])
            )
    return signals


def summarize(rows: list[dict[str, Any]], key: str, value: str) -> dict[str, Any] | None:
    subset = [r for r in rows if r[key] == value and int(r["n_options"]) > 1]
    if not subset:
        return None
    agree = float(np.mean([float(cast(int, r["option_agree"])) for r in subset]))
    target = float(np.mean([float(cast(int, r["target_agree"])) for r in subset]))
    floor = float(np.mean([float(cast(float, r["random_floor"])) for r in subset]))
    drafts = [float(cast(float, r["draft_agree"])) for r in subset if r["draft_agree"] != ""]
    return {
        "games": len({cast(str, r["game_id"]) for r in subset}),
        "decisions": len(subset),
        "option_agree": round(agree, 4),
        "target_agree": round(target, 4),
        "random_floor": round(floor, 4),
        "signal": round(agree - floor, 4),
        "draft_agree": round(float(np.mean(drafts)), 4) if drafts else "",
    }


def paired_comparison(
    rows: list[dict[str, Any]], left: str, right: str, rng: np.random.Generator
) -> dict[str, Any] | None:
    sig_left = game_signal(rows, left)
    sig_right = game_signal(rows, right)
    common = sorted(set(sig_left) & set(sig_right))
    if len(common) < 2:
        return None
    diffs = np.array([sig_left[g] - sig_right[g] for g in common])
    n = len(diffs)
    boots = np.empty(BOOT_RESAMPLES)
    for i in range(BOOT_RESAMPLES):
        boots[i] = float(np.mean(diffs[rng.integers(0, n, n)]))
    ci_lo, ci_hi = (float(v) for v in np.percentile(boots, [2.5, 97.5]))
    wins = int(np.sum(diffs > 0))
    losses = int(np.sum(diffs < 0))
    return {
        "games": n,
        "mean_diff": round(float(np.mean(diffs)), 4),
        "ci95_lo": round(ci_lo, 4),
        "ci95_hi": round(ci_hi, 4),
        "cliff_delta": round((wins - losses) / n, 4),
        "wins": wins,
        "losses": losses,
    }


def main() -> None:
    default_roots = [
        ROOT / "runs" / "4-courts-battle",
        ROOT / "runs" / "court-vs-baseline",
        ROOT / "runs" / "fe-vs-baseline",
        ROOT / "runs" / "court-fe-battle",
    ]
    roots = [Path(arg) for arg in sys.argv[1:]] or default_roots
    rows_by_experiment: dict[str, list[dict[str, Any]]] = {}
    for root in roots:
        experiment = root.name
        run_dirs = sorted(
            path.parent for path in root.rglob("decisions.jsonl") if "deprecate" not in path.parts
        )
        rows: list[dict[str, Any]] = []
        for run_dir in run_dirs:
            rows.extend(audit_run(run_dir, experiment))
        rows_by_experiment[experiment] = rows
        print(f"[{experiment}] audited {len(run_dirs)} games, {len(rows)} decisions")

    # Summary table: one block per experiment, grouped by controller_type and
    # by base model.  Experiments are never pooled across rows.
    summary_rows: list[dict[str, Any]] = []
    for experiment, rows in rows_by_experiment.items():
        print(f"\n[summary:{experiment}] non-trivial decisions (n_options > 1)")
        for controller in sorted({cast(str, r["controller_type"]) for r in rows}):
            stats = summarize(rows, "controller_type", controller)
            if stats is None:
                continue
            print(f"  {controller}: {stats}")
            summary_rows.append(
                {
                    "experiment": experiment,
                    "group_kind": "controller_type",
                    "group": controller,
                    **stats,
                }
            )
        for model in sorted({cast(str, r["base_model"]) for r in rows}):
            stats = summarize(rows, "base_model", model)
            if stats is None:
                continue
            print(f"  base_model={model or '?'}: {stats}")
            summary_rows.append(
                {
                    "experiment": experiment,
                    "group_kind": "base_model",
                    "group": model or "?",
                    **stats,
                }
            )
    summary_path = ROOT / "stat" / "decision_audit_summary.csv"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"\nwrote {summary_path} ({len(summary_rows)} rows)")

    # Paired table: ALL pairwise arm comparisons, computed per experiment
    # (per-game arm signals -> paired game diffs -> bootstrap CI95 + Cliff).
    paired_rows: list[dict[str, Any]] = []
    rng = np.random.default_rng(42)
    for experiment, rows in rows_by_experiment.items():
        controllers = sorted({cast(str, r["controller_type"]) for r in rows})
        print(f"\n[paired:{experiment}]")
        for i, left in enumerate(controllers):
            for right in controllers[i + 1 :]:
                stats = paired_comparison(rows, left, right, rng)
                if stats is None:
                    continue
                print(f"  {left} - {right}: {stats}")
                paired_rows.append(
                    {"experiment": experiment, "arm_a": left, "arm_b": right, **stats}
                )
    paired_path = ROOT / "stat" / "decision_audit_paired.csv"
    with paired_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(paired_rows[0]))
        writer.writeheader()
        writer.writerows(paired_rows)
    print(f"wrote {paired_path} ({len(paired_rows)} rows)")


if __name__ == "__main__":
    main()
