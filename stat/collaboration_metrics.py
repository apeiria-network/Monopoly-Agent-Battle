"""Collaboration process metrics (P-series) for court/FE agents, per experiment.

Implements the §6.2 metric table of Courts-Battle-config-details.md (P-007
有效干预率 deleted by project decision — it required counterfactual simulation
and could not be measured credibly). Input: experiment run directories. Output:
a per-(entity x game) detail CSV (the §6.3 statistical unit) and a per-entity
summary CSV (mean/median/P90 across games, applicability-annotated).

Data sources per game dir:
- decisions.jsonl -> per-decision court_trace.calls (role, phase, content_type,
  outcome, content) for all negotiation metrics, plus the decision-level
  `fallback` flag for P-009;
- llm_calls.jsonl -> authoritative API-call count/tokens/duration per entity
  (caller_role prefix), for P-008 (ratios of totals per game, per the doc);
- llm_digest.csv -> per-call error flags (是否报错回复) for role-level P-009.

Entity roles (proposer = emits selected_option+target proposals):
- ming_court: 3 grand secretaries (content_type "draft", phases first/redraft);
  the chief's phase-"advice" summary is the 汇总意见 and is NOT an officer
  opinion (project ruling). Decider: emperor (final_decision).
- qin_court: chancellor + grand_marshal ("advice"); the imperial_counsellor
  only assesses the two ("comment" with agree/disagree judgements) and is not
  a proposer. Decider: emperor.
- shang2_court: minister_1..3 ("advice"). Decider: emperor.
- tang_court: zhongshu is the ONLY drafter ("draft", redrafts on rejection);
  menxia emits review verdicts agree/disagree; shangshu writes a text summary
  with no proposal. Decider: emperor.
- flat_ensemble: member_1..3 ("advice"). Decider: leader (its "advice" call is
  the final decision).
- llm_baseline / scripted: no trace -> only P-008/P-009 entity-level rows.

Metric definitions and rulings (see doc §6.2; deviations noted):
- "一致" = ALL proposers' (option, normalized target) identical (unanimous).
- P-001 initial disagreement: proposers' FIRST outputs not unanimous.
- P-002 opinion change: ming, officers' redraft output vs first draft, pooled
  over officer x redraft-decisions; tang, zhongshu's last draft vs first over
  decisions with a redraft. Only ming/tang (doc).
- P-003 consensus formation: ming, initial-disagree -> final unanimous;
  tang, round-1 menxia "disagree" -> final verdict "agree".
- P-004 decider adoption: decider == officer's FINAL proposal, per role;
  "any" = decider matches at least one officer. Tang: zhongshu only.
- P-005 minority adoption: among non-unanimous final proposals, decider picks
  a NON-majority option. Majority must be strict (>half); ties/3-way splits
  (no majority) are excluded from both counts. N/A for shang2 (doc: 商代无
  多数概念), qin (2 proposers -> split means no majority by construction),
  tang (single drafter).
- P-006 review intervention (tang): menxia verdict == "disagree" rate.
- P-008 overhead: per game, entity's llm_calls (count, input+output tokens,
  duration) / entity's decisions — ratio of totals, per doc.
- P-009 invalid-output & fallback: decision-level `fallback` flag rate per
  entity; role-level digest error-flag rate per speaker role.
- P-010 advice diversity: distinct (option, target) among officers' FINAL
  proposals, averaged over decisions. Tang N/A (single drafter).
- P-011 officer-decider agreement: per officer, final proposal == decider,
  averaged (numerically the per-role P-004; reported as the officer-side view).

Trace hygiene: court_trace logs delivery echoes (outcome "advice_normalized"
duplicates the real call's content; final_decision may appear twice). Entries
with outcome "advice_normalized" and exact (role, content_type, content)
duplicates are dropped before any counting; llm_calls.jsonl (real API calls)
is unaffected by echoes and is used for P-008.

Run from the repository root:
    .venv/Scripts/python.exe stat/collaboration_metrics.py [run_dir ...]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS = [
    "court-vs-baseline",
    "fe-vs-baseline",
    "4-courts-battle",
    "court-fe-battle",
]

COURT_SPEC: dict[str, dict[str, Any]] = {
    "ming_court": {
        "proposers": ["chief_grand_secretary", "grand_secretary_1", "grand_secretary_2"],
        "proposal_ct": {"draft"},  # chief's phase-"advice" 汇总意见 is NOT a proposal
        "decider": "emperor",
        "reviewer": None,
        "redraft": True,
        "family": "ming",
    },
    "qin_court": {
        "proposers": ["chancellor", "grand_marshal"],
        "proposal_ct": {"advice"},
        "decider": "emperor",
        "reviewer": None,
        "redraft": False,
        "family": "qin",
    },
    "shang2_court": {
        "proposers": ["minister_1", "minister_2", "minister_3"],
        "proposal_ct": {"advice"},
        "decider": "emperor",
        "reviewer": None,
        "redraft": False,
        "family": "shang",
    },
    "tang_court": {
        "proposers": ["zhongshu"],
        "proposal_ct": {"draft"},
        "decider": "emperor",
        "reviewer": "menxia",
        "redraft": True,
        "family": "tang",
    },
    "flat_ensemble": {
        "proposers": ["member_1", "member_2", "member_3"],
        "proposal_ct": {"advice"},
        "decider": "leader",
        "reviewer": None,
        "redraft": False,
        "family": "fe",
    },
}
CONTROLLER_FAMILY = {
    "llm_baseline": "baseline",
    "sane_random": "sane_random",
    "greedy_script": "greedy_script",
}
# Applicability: metrics not meaningful for a controller are left empty.
P005_OK = {"ming_court", "flat_ensemble"}  # need >=3 proposers for a majority
P001_OK = {"ming_court", "qin_court", "shang2_court", "flat_ensemble"}
P010_OK = P001_OK

Choice = tuple[str, Any]
Call = dict[str, Any]


def norm_target(target: Any) -> Any:
    """Canonicalize target for equality: None/{} -> None; dict -> sorted tuple."""
    if target is None or target == {}:
        return None
    if isinstance(target, dict):
        items = cast(dict[str, Any], target).items()
        pairs: list[tuple[str, str]] = [(str(k), str(v)) for k, v in items]
        return tuple(sorted(pairs))
    return str(target)


def parse_choice(content: str | None) -> Choice | None:
    """Extract (option, normalized target) from a call's raw JSON content."""
    if not content:
        return None
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    selected = cast(dict[str, Any], obj).get("selected_option")
    if not isinstance(selected, dict):
        return None
    data = cast(dict[str, Any], selected)
    option = data.get("option")
    if option is None:
        return None
    return (str(option), norm_target(data.get("target")))


def dedupe_calls(calls: list[Call]) -> list[Call]:
    """Drop normalization echoes and exact duplicates from a court trace."""
    kept: list[Call] = []
    seen: set[str] = set()
    for call in calls:
        if call.get("outcome") == "advice_normalized":
            continue
        digest = hashlib.md5(
            f"{call.get('role')}|{call.get('content_type')}|{call.get('content')}".encode()
        ).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        kept.append(call)
    return kept


class DecisionRecord:
    """Parsed negotiation facts of one entity's one decision."""

    def __init__(self, controller: str, calls: list[Call]) -> None:
        spec = COURT_SPEC[controller]
        self.first: dict[str, Choice] = {}
        self.final: dict[str, Choice] = {}
        self.redraft_roles: list[str] = []
        self.redraft_changed: dict[str, bool] = {}
        self.menxia_verdicts: list[str] = []
        self.decider: Choice | None = None
        by_role: dict[str, list[tuple[str | None, Choice]]] = {}
        for call in calls:
            role = str(call.get("role"))
            choice = parse_choice(call.get("content"))
            if role in spec["proposers"] and call.get("content_type") in spec["proposal_ct"]:
                if choice is not None:
                    by_role.setdefault(role, []).append((call.get("phase"), choice))
            elif role == spec["reviewer"] and call.get("content_type") == "review":
                if choice is not None and choice[0] in ("agree", "disagree"):
                    self.menxia_verdicts.append(choice[0])
            elif role == spec["decider"]:
                decider_ct = call.get("content_type")
                if choice is not None and (
                    decider_ct == "final_decision"
                    or (controller == "flat_ensemble" and decider_ct == "advice")
                ):
                    self.decider = choice  # last parseable decider output wins
        redraft_roles: list[str] = []
        for role, entries in by_role.items():
            self.first[role] = entries[0][1]
            self.final[role] = entries[-1][1]
            phases = {phase for phase, _ in entries}
            if spec["redraft"] and ("redraft" in phases or len(entries) > 1):
                redraft_roles.append(role)
                self.redraft_changed[role] = entries[0][1] != entries[-1][1]
        self.redraft_roles = redraft_roles

    def initial_split(self) -> bool | None:
        if len(self.first) < 2:
            return None
        return len(set(self.first.values())) > 1

    def final_unanimous(self) -> bool | None:
        if len(self.final) < 2:
            return None
        return len(set(self.final.values())) == 1

    def majority_choice(self) -> Choice | None:
        """Strict majority among final proposals; None when tied/no majority."""
        counts: dict[Choice, int] = {}
        for choice in self.final.values():
            counts[choice] = counts.get(choice, 0) + 1
        top, top_n = max(counts.items(), key=lambda kv: kv[1])
        return top if top_n > len(self.final) / 2 else None


def empty_metrics() -> dict[str, Any]:
    return {
        "decisions": 0,
        "p001_num": 0,
        "p001_den": 0,
        "p002_num": 0,
        "p002_den": 0,
        "p003_num": 0,
        "p003_den": 0,
        "p004_any": 0,
        "p004_den": 0,
        "p004_role_num": {},
        "p004_role_den": {},
        "p005_num": 0,
        "p005_den": 0,
        "p006_num": 0,
        "p006_den": 0,
        "p009_fallback": 0,
        "p010_sum": 0,
        "p010_den": 0,
    }


def accumulate(acc: dict[str, Any], record: DecisionRecord, controller: str) -> None:
    """Fold one parsed court decision into the per-game counters."""
    split = record.initial_split()
    if controller in P001_OK and split is not None:
        acc["p001_den"] += 1
        acc["p001_num"] += int(split)
    if COURT_SPEC[controller]["redraft"]:
        if controller == "ming_court":
            for role in record.redraft_roles:
                acc["p002_den"] += 1
                acc["p002_num"] += int(record.redraft_changed.get(role, False))
        elif record.redraft_roles:  # tang: single drafter, per decision
            acc["p002_den"] += 1
            acc["p002_num"] += int(any(record.redraft_changed.values()))
    if controller == "ming_court" and split:
        acc["p003_den"] += 1
        acc["p003_num"] += int(record.final_unanimous() is True)
    elif controller == "tang_court" and record.menxia_verdicts:
        if record.menxia_verdicts[0] == "disagree":
            acc["p003_den"] += 1
            acc["p003_num"] += int(record.menxia_verdicts[-1] == "agree")
    if record.decider is not None and record.final:
        acc["p004_den"] += 1
        if record.decider in set(record.final.values()):
            acc["p004_any"] += 1
        for role, choice in record.final.items():
            acc["p004_role_den"][role] = acc["p004_role_den"].get(role, 0) + 1
            if record.decider == choice:
                acc["p004_role_num"][role] = acc["p004_role_num"].get(role, 0) + 1
    if controller in P005_OK and record.decider is not None:
        if record.final_unanimous() is False:
            majority = record.majority_choice()
            if majority is not None:
                acc["p005_den"] += 1
                acc["p005_num"] += int(record.decider != majority)
    if record.menxia_verdicts:  # per decision: any "disagree" verdict = intervened
        acc["p006_den"] += 1
        acc["p006_num"] += int("disagree" in record.menxia_verdicts)
    if controller in P010_OK and len(record.final) >= 2:
        acc["p010_sum"] += len(set(record.final.values()))
        acc["p010_den"] += 1


def ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def finalize_game(
    experiment: str,
    game_id: str,
    entity: str,
    controller: str,
    seat: int,
    acc: dict[str, Any],
    overhead: dict[str, float],
    role_errors: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    decisions = acc["decisions"]
    row: dict[str, Any] = {
        "experiment": experiment,
        "game_id": game_id,
        "entity": entity,
        "controller_type": controller,
        "seat": seat,
        "decisions": decisions,
        "llm_calls": int(overhead["calls"]),
        "p008_calls_per_decision": ratio(overhead["calls"], decisions),
        "p008_tokens_per_decision": ratio(overhead["tokens"], decisions),
        "p008_seconds_per_decision": ratio(overhead["duration_ms"] / 1000.0, decisions),
        "p001_initial_disagreement": ratio(acc["p001_num"], acc["p001_den"]),
        "p002_opinion_change": ratio(acc["p002_num"], acc["p002_den"]),
        "p003_consensus_formation": ratio(acc["p003_num"], acc["p003_den"]),
        "p004_decider_adoption_any": ratio(acc["p004_any"], acc["p004_den"]),
        "p005_minority_adoption": ratio(acc["p005_num"], acc["p005_den"]),
        "p006_review_intervention": ratio(acc["p006_num"], acc["p006_den"]),
        "p009_fallback_rate": ratio(acc["p009_fallback"], decisions),
        "p010_advice_diversity": ratio(acc["p010_sum"], acc["p010_den"]),
    }
    role_nums = acc["p004_role_num"]
    role_dens = acc["p004_role_den"]
    agreements = [role_nums.get(r, 0) / role_dens[r] for r in role_dens if role_dens[r]]
    row["p011_officer_decider_agreement"] = float(np.mean(agreements)) if agreements else None
    for role, (errors, total) in sorted(role_errors.items()):
        row[f"p009_error_rate__{role}"] = ratio(errors, total)
    for role in sorted(role_dens):
        row[f"p004_adoption__{role}"] = ratio(role_nums.get(role, 0), role_dens[role])
    return row


def load_game(experiment: str, gdir: Path) -> list[dict[str, Any]]:
    """All entity rows for one game dir."""
    result = json.loads((gdir / "result.json").read_text(encoding="utf-8"))
    if result.get("validity_status") != "valid":
        return []
    config = json.loads((gdir / "config.json").read_text(encoding="utf-8"))["config"]
    players = {p["player_id"]: (p["controller_type"], p["seat"]) for p in config["players"]}

    decisions_path = gdir / "decisions.jsonl"
    accs: dict[str, dict[str, Any]] = {pid: empty_metrics() for pid in players}
    if decisions_path.exists():
        for line in decisions_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = cast(dict[str, Any], json.loads(line))
            pid = cast(dict[str, Any], entry.get("request") or {}).get("player_id")
            if pid not in accs:
                continue
            controller = players[pid][0]
            acc = accs[pid]
            acc["decisions"] += 1
            acc["p009_fallback"] += int(bool(entry.get("fallback")))
            trace = cast(dict[str, Any], entry.get("court_trace") or {})
            calls = dedupe_calls(cast(list[Call], trace.get("calls") or []))
            if controller in COURT_SPEC and calls:
                accumulate(acc, DecisionRecord(controller, calls), controller)

    overhead: dict[str, dict[str, float]] = {
        pid: {"calls": 0.0, "tokens": 0.0, "duration_ms": 0.0} for pid in players
    }
    calls_path = gdir / "llm_calls.jsonl"
    if calls_path.exists():
        for line in calls_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            call = json.loads(line)
            caller = str(call.get("caller_role") or "")
            for pid in players:
                if caller == pid or caller.startswith(pid + "."):
                    overhead[pid]["calls"] += 1
                    overhead[pid]["tokens"] += float(call.get("input_tokens") or 0) + float(
                        call.get("output_tokens") or 0
                    )
                    overhead[pid]["duration_ms"] += float(call.get("duration_ms") or 0)
                    break

    role_errors: dict[str, dict[str, tuple[int, int]]] = {pid: {} for pid in players}
    digest_path = gdir / "llm_digest.csv"
    if digest_path.exists():
        with digest_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                speaker = row.get("发言者") or ""
                for pid in players:
                    if speaker == pid or speaker.startswith(pid + "."):
                        short = speaker[len(pid) + 1 :] if "." in speaker else speaker
                        errors, total = role_errors[pid].get(short, (0, 0))
                        total += 1
                        errors += int((row.get("是否报错回复") or "") == "True")
                        role_errors[pid][short] = (errors, total)
                        break

    game_id = str(config["game_id"])
    rows: list[dict[str, Any]] = []
    for pid, (controller, seat) in players.items():
        rows.append(
            finalize_game(
                experiment,
                game_id,
                pid,
                controller,
                seat,
                accs[pid],
                overhead[pid],
                role_errors[pid],
            )
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-(experiment, entity-family) mean/median/P90 over the game rows."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        controller = str(row["controller_type"])
        family = COURT_SPEC.get(controller, {}).get("family") or CONTROLLER_FAMILY.get(
            controller, controller
        )
        groups.setdefault((str(row["experiment"]), str(family)), []).append(row)
    metric_cols = sorted(
        {
            col
            for row in rows
            for col in row
            if col.startswith(
                ("p001", "p002", "p003", "p004", "p005", "p006", "p008", "p009", "p010", "p011")
            )
        }
    )
    out: list[dict[str, Any]] = []
    for (experiment, entity), group in sorted(groups.items()):
        summary: dict[str, Any] = {
            "experiment": experiment,
            "entity": entity,
            "controller_type": group[0]["controller_type"],
            "games": len(group),
            "decisions": sum(int(r["decisions"]) for r in group),
            "llm_calls": sum(int(r["llm_calls"]) for r in group),
        }
        for col in metric_cols:
            values = [float(r[col]) for r in group if r.get(col) is not None]
            if not values:
                continue
            arr = np.asarray(values)
            summary[f"{col}__mean"] = round(float(arr.mean()), 4)
            summary[f"{col}__median"] = round(float(np.median(arr)), 4)
            summary[f"{col}__p90"] = round(float(np.percentile(arr, 90)), 4)
        out.append(summary)
    return out


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dirs",
        nargs="*",
        type=Path,
        default=[ROOT / "runs" / d for d in DEFAULT_RUNS],
        help="experiment run dirs (default: the four LLM experiments)",
    )
    parser.add_argument(
        "--detail",
        type=Path,
        default=ROOT / "stat" / "collaboration_detail.csv",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=ROOT / "stat" / "collaboration_summary.csv",
    )
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for run_dir in args.run_dirs:
        if not run_dir.is_dir():
            continue
        experiment = run_dir.name
        for gdir in sorted(run_dir.iterdir()):
            if not gdir.is_dir() or gdir.name == "deprecate" or "." in gdir.name:
                continue
            if (gdir / "result.json").exists() and (gdir / "config.json").exists():
                rows.extend(load_game(experiment, gdir))
    print(f"entity-game rows: {len(rows)}")
    write_csv(rows, args.detail)
    write_csv(summarize(rows), args.summary)


if __name__ == "__main__":
    main()
