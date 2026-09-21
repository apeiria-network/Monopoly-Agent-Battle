"""Collaboration process metrics for court/FE agents of ONE experiment.

Implements the §6.2 metric table of Courts-Battle-config-details.md (the 有效
干预率 metric was deleted by project decision — it required counterfactual
simulation and could not be measured credibly). Input: ONE experiment run
directory; only that experiment is analysed. Output: a per-(entity x game)
detail CSV (the §6.3 statistical unit) and a per-entity summary CSV (MEAN
across games only). Both default to
stat/collaboration_<experiment>_{detail,summary}.csv.

Data sources per game dir:
- decisions.jsonl -> per-decision court_trace.calls (role, phase, content_type,
  outcome, content) for all negotiation metrics, plus the decision-level
  `fallback` flag for fallback_rate;
- llm_calls.jsonl -> authoritative API-call count/tokens/duration per entity
  (caller_role prefix), for the overhead metrics (ratios of totals per game);
- llm_digest.csv -> per-call error flags (是否报错回复) for role-level
  error_rate__<role>.

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
- llm_baseline / scripted: no trace -> only overhead/fallback rows.

Metric definitions and interpretation guide
-------------------------------------------
"一致" below = ALL proposers' (option, normalized target) identical.

- initial_disagreement (初始分歧率): share of decisions where the proposers'
  FIRST outputs are not unanimous. High values mean the heterogeneous models
  genuinely disagree before any discussion.
- opinion_change (意见改变率): ming — officers' redraft output differs from
  their first draft, pooled over officer x redraft-decisions (decisions
  without a redraft round had no second chance and are excluded); tang —
  zhongshu's last draft differs from its first over decisions with a redraft.
  Only ming/tang. Reads as "how often discussion actually moves anyone".
- consensus_formation (共识形成率): ming — initially split decisions that end
  unanimous; tang — decisions whose first menxia verdict is "disagree" that
  end with a final verdict "agree". Only ming/tang.
- decider_adoption_any / adoption__<role> (皇帝采纳率): decider's final choice
  equals an officer's FINAL proposal — "any" counts a match with at least one
  officer, per-role columns break it down. Tang: zhongshu is the only officer.
  High values mean the decider mostly rubber-stamps; low values mean it
  overrules or composes something new.
- minority_adoption (少数意见采纳率): among non-unanimous final proposals,
  the decider picks a NON-majority option. Majority must be strict (>half);
  3-way splits with no majority are excluded from both counts. N/A for
  shang2 (doc: 商代无多数概念), qin (2 proposers -> a split means no majority
  by construction), tang (single drafter).
- review_intervention (审核干预率, tang only): decisions where any menxia
  verdict is "disagree" over decisions reviewed.
- calls/tokens/seconds_per_decision (协作开销): per game, the entity's
  llm_calls totals (count, input+output tokens, duration) divided by its
  decisions — ratio of totals. Compare courts (~4-7 calls) against baseline
  (~1.2) for the §7.3.1 cost account.
- fallback_rate / error_rate__<role> (非法输出及回退率): decision-level
  `fallback` flag rate per entity (invalid JSON/option or timeout forced the
  default option); role-level digest error-flag rate per speaker role.
- advice_diversity (建议多样性): distinct (option, target) values among the
  officers' FINAL proposals, averaged over decisions. Tang N/A (single
  drafter). Range 1..#proposers; 1 = always unanimous.
- officer_decider_agreement (官员最终决策一致率): per officer, final proposal
  == decider's choice, averaged across officers. Numerically the per-role
  adoption view from the officer side. §6.2 wording rule: this measures
  opinion convergence in the process, NOT decision correctness.

Trace hygiene: court_trace logs delivery echoes (outcome "advice_normalized"
duplicates the real call's content; final_decision may appear twice). Entries
with outcome "advice_normalized" and exact (role, content_type, content)
duplicates are dropped before any counting; llm_calls.jsonl (real API calls)
is unaffected by echoes and feeds the overhead metrics.

Run from the repository root (one experiment per invocation):
    .venv/Scripts/python.exe stat/collaboration_metrics.py runs/court-vs-baseline
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

# Applicability: metrics not meaningful for a controller are left empty.
P005_OK = {"ming_court", "flat_ensemble"}  # need >=3 proposers for a majority
P001_OK = {"ming_court", "qin_court", "shang2_court", "flat_ensemble"}
P010_OK = P001_OK

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
        "game_id": game_id,
        "entity": entity,
        "controller_type": controller,
        "seat": seat,
        "decisions": decisions,
        "llm_calls": int(overhead["calls"]),
        "calls_per_decision": ratio(overhead["calls"], decisions),
        "tokens_per_decision": ratio(overhead["tokens"], decisions),
        "seconds_per_decision": ratio(overhead["duration_ms"] / 1000.0, decisions),
        "initial_disagreement": ratio(acc["p001_num"], acc["p001_den"]),
        "opinion_change": ratio(acc["p002_num"], acc["p002_den"]),
        "consensus_formation": ratio(acc["p003_num"], acc["p003_den"]),
        "decider_adoption_any": ratio(acc["p004_any"], acc["p004_den"]),
        "minority_adoption": ratio(acc["p005_num"], acc["p005_den"]),
        "review_intervention": ratio(acc["p006_num"], acc["p006_den"]),
        "fallback_rate": ratio(acc["p009_fallback"], decisions),
        "advice_diversity": ratio(acc["p010_sum"], acc["p010_den"]),
    }
    role_nums = acc["p004_role_num"]
    role_dens = acc["p004_role_den"]
    agreements = [role_nums.get(r, 0) / role_dens[r] for r in role_dens if role_dens[r]]
    row["officer_decider_agreement"] = float(np.mean(agreements)) if agreements else None
    for role, (errors, total) in sorted(role_errors.items()):
        row[f"error_rate__{role}"] = ratio(errors, total)
    for role in sorted(role_dens):
        row[f"adoption__{role}"] = ratio(role_nums.get(role, 0), role_dens[role])
    return row


def load_game(gdir: Path) -> list[dict[str, Any]]:
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
    """Per-entity-family MEAN over the game rows (median/P90 intentionally omitted)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        controller = str(row["controller_type"])
        family = COURT_SPEC.get(controller, {}).get("family") or CONTROLLER_FAMILY.get(
            controller, controller
        )
        groups.setdefault(str(family), []).append(row)
    metric_cols = sorted(
        {
            col
            for row in rows
            for col in row
            if col not in {"game_id", "entity", "controller_type", "seat", "decisions", "llm_calls"}
        }
    )
    out: list[dict[str, Any]] = []
    for entity, group in sorted(groups.items()):
        summary: dict[str, Any] = {
            "entity": entity,
            "controller_type": group[0]["controller_type"],
            "games": len(group),
            "decisions": sum(int(r["decisions"]) for r in group),
            "llm_calls": sum(int(r["llm_calls"]) for r in group),
        }
        for col in metric_cols:
            values = [float(r[col]) for r in group if r.get(col) is not None]
            if values:
                summary[col] = round(float(np.mean(values)), 4)
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
        "run_dir",
        type=Path,
        help="ONE experiment run dir (e.g. runs/court-vs-baseline); only this "
        "experiment is analysed",
    )
    parser.add_argument("--detail", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        raise SystemExit(f"run dir does not exist: {run_dir}")
    detail = args.detail or ROOT / "stat" / f"collaboration_{run_dir.name}_detail.csv"
    summary = args.summary or ROOT / "stat" / f"collaboration_{run_dir.name}_summary.csv"

    rows: list[dict[str, Any]] = []
    for gdir in sorted(run_dir.iterdir()):
        if not gdir.is_dir() or gdir.name == "deprecate" or "." in gdir.name:
            continue
        if (gdir / "result.json").exists() and (gdir / "config.json").exists():
            rows.extend(load_game(gdir))
    print(f"entity-game rows: {len(rows)}")
    write_csv(rows, detail)
    write_csv(summarize(rows), summary)


if __name__ == "__main__":
    main()
