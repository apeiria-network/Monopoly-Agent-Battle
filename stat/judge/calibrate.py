"""Cross-check V(s) against the real engine, and calibrate section-6.7 timings.

WHAT THIS DOES
--------------
Two independent jobs, both small and safe to run repeatedly. Neither touches the
formal experiment pipeline and neither writes anything outside stat/judge/data/.

A. MECHANICAL CROSS-CHECK (``--check``)
   Section 6.2 defines V(s) on paper; value.py re-implements engine rules
   (rent, mortgage, jail, ongoing effects) outside the engine. Any drift between
   the two silently corrupts every downstream delta. So we verify, on real
   states replayed from real runs:

   1. M_assets equals rules.classic_level0.net_worth exactly (integer identity).
   2. Expected-rent reasoning matches the engine: for each property, we ask the
      ENGINE what rent a landing would produce (by cloning and settling a real
      landing) and compare it to value._rent_for. Any mismatch is reported with
      its position, owner and effects.
   3. V(s) degenerates to plain net worth when the horizon closes (H = 0), which
      is what makes late-game candidates commensurable (section 6.4).
   4. Pruning never rejects a state that is trivially safe (large cash, no debt).

B. TIMING CALIBRATION (``--calibrate``)
   Section 6.7 says the total runtime hinges on ``r``, the cost of ONE game
   replay WITHOUT building decision requests -- a number never actually measured
   (the recorded 13-27 s/game includes request construction). We measure:

   * ``r``          : pure replay, commands only, no request construction.
   * ``r_request``  : replay WITH build_decision_request, for contrast.
   * candidate cost : clone+execute per DECISION (all candidates enumerated).
   * jail cost      : the same, for jail nodes where roll_dice must be valued
     over the 36 ordered 2d6 outcomes (section 6.4).

HOW TO READ THE OUTPUT
----------------------
The check prints PASS/FAIL per assertion with counts; any FAIL blocks the rest
of section 6 and must be fixed before scoring anything. The calibration prints
per-game seconds and projects the section-6.7 totals at 1 and 4 cores. Compare
the projected total against the 2-7.5 h band in the document: if ``r`` lands
near 3 s the optimistic column holds, if it stays near the recorded 13-27 s the
pessimistic column does, and section 6.7 must be restated accordingly.

Usage from the repository root:
    .venv/Scripts/python.exe stat/judge/calibrate.py --check   [--games N]
    .venv/Scripts/python.exe stat/judge/calibrate.py --calibrate [--games N]

Defaults to a handful of games. This is a development tool: it does NOT produce
any of the section-6.8 result tables.
"""

from __future__ import annotations

import argparse
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import value as value_module
from replay_tools import DecisionPoint, iter_decision_points, load_commands

from monopoly_agent_battle.config.models import GameConfig
from monopoly_agent_battle.decision.requests import _candidate_commands, build_decision_request
from monopoly_agent_battle.domain.models import JailStatus, SettlementOperationKind, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine, GameRuleError
from monopoly_agent_battle.game.rules.classic_level0 import net_worth

ROOT = Path(__file__).resolve().parent.parent.parent
RUNS = ROOT / "runs"


def _sample_games(experiment: str, limit: int) -> list[Path]:
    root = RUNS / experiment
    if not root.exists():
        return []
    games = sorted(d for d in root.iterdir() if d.is_dir() and (d / "events.jsonl").exists())
    return games[:limit]


def run_check(games: int) -> int:
    """Cross-check value.py against the engine on replayed states."""
    failures = 0
    checked_states = 0
    rent_comparisons = 0
    rent_mismatches: list[str] = []

    for directory in _sample_games("sane_random", games):
        for point in iter_decision_points(directory):
            state = point.engine.state
            owned = sum(1 for p in state.properties.values() if p.owner_id is not None)
            if owned == 0:
                continue  # early game: nothing to compare rents against
            checked_states += 1
            for player_id in state.players:
                breakdown = value_module.evaluate(state, player_id)
                expected_assets = net_worth(state.players[player_id], state)
                if breakdown.m_assets != expected_assets:
                    failures += 1
                    print(f"FAIL M_assets {player_id}: {breakdown.m_assets} != {expected_assets}")
            mismatches, count = _compare_rents(point)
            rent_comparisons += count
            rent_mismatches.extend(mismatches)
            if checked_states >= 400:
                break
        if checked_states >= 400:
            break

    print(
        f"[1] M_assets identity : {checked_states} states x players -> "
        f"{'PASS' if failures == 0 else f'{failures} FAIL'}"
    )
    print(
        f"[2] rent vs engine    : {rent_comparisons} comparisons -> "
        f"{'PASS' if not rent_mismatches else f'{len(rent_mismatches)} FAIL'}"
    )
    for line in rent_mismatches[:10]:
        print(f"    {line}")
    failures += len(rent_mismatches)

    failures += _check_horizon_collapse()
    failures += _check_pruning_sanity()
    print(f"\n{'ALL CHECKS PASS' if failures == 0 else f'{failures} FAILURES'}")
    return 1 if failures else 0


def _compare_rents(point: DecisionPoint) -> tuple[list[str], int]:
    """Compare value._rent_for against engine-settled rent, both perspectives."""
    from monopoly_agent_battle.domain.models import SpaceKind
    from monopoly_agent_battle.game.board_data.classic_us_40 import BOARD_BY_POSITION

    state = point.engine.state
    mismatches: list[str] = []
    count = 0
    for position, property_state in state.properties.items():
        owner_id = property_state.owner_id
        if owner_id is None:
            continue
        # Utilities price off the 2d6 expectation, so equality does not apply.
        if BOARD_BY_POSITION[position].kind is SpaceKind.UTILITY:
            continue
        for payer in state.players.values():
            if payer.bankrupt or payer.player_id == owner_id:
                continue
            measured = _engine_rent(point.engine, position, payer.player_id)
            if measured is None:
                continue
            charge, received = measured
            count += 1
            ours_charge = value_module._rent_for(
                state, position, owner_id, elapsed={}, payer_id=payer.player_id
            )
            ours_income = value_module._rent_for(
                state,
                position,
                owner_id,
                elapsed={},
                payer_id=payer.player_id,
                owner_income=True,
            )
            if abs(ours_charge - charge) > 1e-6:
                mismatches.append(
                    f"charge pos={position} owner={owner_id} payer={payer.player_id} "
                    f"ours={ours_charge} engine={charge}"
                )
            if received is not None and abs(ours_income - received) > 1e-6:
                mismatches.append(
                    f"income pos={position} owner={owner_id} payer={payer.player_id} "
                    f"ours={ours_income} engine={received}"
                )
            break
    return mismatches, count


def _engine_rent(
    engine: GameEngine, position: int, payer_id: str
) -> tuple[float, float | None] | None:
    """Return (charge to payer, receipt by owner) for a landing, per the engine.

    Three details make a naive "cash delta" oracle wrong, and all three were
    caught by this check rather than assumed:

    * Rent waivers are zeroed on the clone first. value._rent_for prices the
      GROSS rent of a landing; R_s applies the waiver budget separately across
      expected landing mass. Leaving waivers in would compare gross to a
      waived zero.
    * When the payer cannot cover the rent the engine moves no cash at all --
      it queues a BLOCKED payment and opens a disposal window. The charge is
      the cash moved PLUS anything left sitting in the queue.
    * Under an ALLIANCE the charge and the receipt differ (engine.py:1440),
      so the two perspectives must be returned and checked separately.
    """
    state = engine.state
    owner_id = state.properties[position].owner_id
    if owner_id is None or owner_id == payer_id:
        return None
    clone = deepcopy(engine)
    clone_payer = clone.state.players[payer_id]
    clone_payer.rent_waivers = 0
    before = clone_payer.cash
    owner_before = clone.state.players[owner_id].cash
    queued_before = _queued_payment_total(clone, payer_id)
    events: list[Any] = []
    clone._collect_rent(  # noqa: SLF001 - deliberate white-box check
        clone_payer,
        clone.state.players[owner_id],
        position,
        7,
        events,
    )
    paid = float(before - clone_payer.cash)
    queued = _queued_payment_total(clone, payer_id) - queued_before
    charge = paid + float(queued)
    if queued:
        # Payment queued as BLOCKED: nothing has settled yet. The owner receipt
        # is unobservable, and under an ALLIANCE the queued figure is the GROSS
        # rent -- the split only happens at settlement -- so the payer's net
        # charge is unobservable too. Skip the alliance case entirely.
        if value_module._alliance_partner(clone.state, owner_id) is not None:
            return None
        return charge, None
    owner_after = clone.state.players[owner_id].cash
    received = float(owner_after - owner_before)
    return charge, received


def _queued_payment_total(engine: GameEngine, payer_id: str) -> int:
    """Total outstanding payment amounts owed by ``payer_id`` in the queue."""
    return sum(
        operation.amount or 0
        for operation in engine.state.settlement_operations
        if operation.kind is SettlementOperationKind.PAYMENT and operation.player_id == payer_id
    )


def _check_horizon_collapse() -> int:
    """V must equal plain net worth once the round limit is reached."""
    from monopoly_agent_battle.domain.models import GameState, PlayerState, PropertyState

    players = {
        "a": PlayerState("a", 1, 1500, properties={1, 3}),
        "b": PlayerState("b", 2, 1500),
    }
    properties = {position: PropertyState() for position in range(40)}
    properties[1].owner_id = "a"
    properties[3].owner_id = "a"
    state = GameState(players, properties, "a", complete_rounds=value_module.ROUND_LIMIT)
    breakdown = value_module.evaluate(state, "a")
    ok = (
        breakdown.r_short == 0.0
        and breakdown.r_long == 0.0
        and breakdown.m_monopoly == 0.0
        and breakdown.total == float(breakdown.m_assets)
    )
    print(f"[3] horizon collapse  : H=0 -> V == M_assets -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _check_pruning_sanity() -> int:
    """A cash-rich player with no exposure must survive both pruning screens."""
    from monopoly_agent_battle.domain.models import GameState, PlayerState, PropertyState

    players = {
        "a": PlayerState("a", 1, 5000),
        "b": PlayerState("b", 2, 1500),
    }
    properties = {position: PropertyState() for position in range(40)}
    state = GameState(players, properties, "a")
    ok = value_module.passes_pruning(state, "a", 0)
    print(f"[4] pruning sanity    : rich player passes -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def run_calibration(games: int) -> int:
    """Measure replay cost with and without request construction."""
    directories = _sample_games("sane_random", games)
    if not directories:
        print("no sane_random runs found -- nothing to calibrate")
        return 1

    pure_total = 0.0
    request_total = 0.0
    candidate_times: list[float] = []
    jail_times: list[float] = []
    candidate_counts: list[int] = []
    decision_nodes = 0

    for directory in directories:
        config, commands = load_commands(directory)

        start = time.perf_counter()
        _replay_pure(config, commands)
        pure_total += time.perf_counter() - start

        start = time.perf_counter()
        nodes = _replay_with_requests(config, commands)
        request_total += time.perf_counter() - start
        decision_nodes += nodes

    for point in iter_decision_points(directories[0]):
        engine = point.engine
        player_id = engine.state.current_player_id
        in_jail = (
            engine.state.turn_phase is TurnPhase.ROLLING
            and engine.state.players[player_id].jail_status is JailStatus.ROLLING
        )
        # Cost of scoring a WHOLE decision: enumerate every candidate, then
        # clone+execute+value each one. Section 6.7's 52-104 ms is per
        # decision, not per candidate, so anything less would flatter us.
        start = time.perf_counter()
        candidates = _candidate_commands(engine, player_id)
        scored = 0
        for candidate in candidates:
            clone = deepcopy(engine)
            try:
                clone.execute(candidate)
            except GameRuleError:
                continue
            value_module.evaluate(clone.state, player_id)
            scored += 1
        elapsed = time.perf_counter() - start
        if scored < 2:
            continue  # section 6.4 excludes single-candidate decisions
        candidate_counts.append(scored)
        (jail_times if in_jail else candidate_times).append(elapsed)
        if len(candidate_times) >= 150:
            break

    count = len(directories)
    r_pure = pure_total / count
    r_request = request_total / count
    per_candidate = (sum(candidate_times) / len(candidate_times)) if candidate_times else 0.0
    per_jail = (sum(jail_times) / len(jail_times)) if jail_times else per_candidate
    mean_candidates = sum(candidate_counts) / len(candidate_counts) if candidate_counts else 0.0

    print(f"games measured          : {count}")
    print(f"decision nodes / game   : {decision_nodes / count:.0f}")
    print(f"r  (pure replay)        : {r_pure:.3f} s/game")
    print(f"r  (with requests)      : {r_request:.3f} s/game")
    print(f"legal candidates / node : {mean_candidates:.1f}")
    print(
        f"per-DECISION scoring    : {per_candidate * 1000:.1f} ms (section 6.7 assumed 52-104 ms)"
    )
    print(f"per-jail-decision       : {per_jail * 1000:.1f} ms")

    total_games = 2668
    total_decisions = 277_000
    jail_decisions = 13_000
    replay_hours = total_games * r_pure / 3600
    scoring_hours = (total_decisions - jail_decisions) * per_candidate / 3600
    # Jail nodes value roll_dice over the 36 ordered 2d6 outcomes (section 6.4).
    # Ordered, not aggregated by total: the engine branches on whether the roll
    # is a DOUBLE (jail release, third-doubles) as well as on the total, so
    # (2,2) and (1,3) behave differently despite sharing a total.
    jail_hours = jail_decisions * per_jail * 36 / 3600
    single = replay_hours + scoring_hours + jail_hours
    print("\n--- section 6.7 projection (measured) ---")
    print(f"replay 2,668 games      : {replay_hours:.2f} h single-core")
    print(f"scoring 264k decisions  : {scoring_hours:.2f} h single-core")
    print(f"jail 13k decisions x36  : {jail_hours:.2f} h single-core")
    print(f"TOTAL                   : {single:.2f} h single / {single / 4:.2f} h at 4 cores")
    print(
        "\nNOTE: measured on sane_random floor games. LLM games hold more cards,"
        "\nso their candidate sets (swap cards enumerate 40x40) run larger."
    )
    return 0


def _replay_pure(config: GameConfig, commands: list[Any]) -> None:
    engine = GameEngine(config)
    for command in commands:
        try:
            engine.execute(command)
        except GameRuleError:
            return


def _replay_with_requests(config: GameConfig, commands: list[Any]) -> int:
    engine = GameEngine(config)
    nodes = 0
    for index, command in enumerate(commands):
        try:
            request = build_decision_request(engine, index)
            if request is not None and len(request.options) > 1:
                nodes += 1
        except (GameRuleError, AssertionError, RuntimeError):
            pass
        try:
            engine.execute(command)
        except GameRuleError:
            return nodes
    return nodes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run the mechanical cross-check")
    parser.add_argument("--calibrate", action="store_true", help="measure replay/candidate cost")
    parser.add_argument("--games", type=int, default=3, help="number of games to use")
    args = parser.parse_args()
    if not args.check and not args.calibrate:
        parser.error("choose --check and/or --calibrate")
    status = 0
    if args.check:
        status |= run_check(args.games)
    if args.calibrate:
        print()
        status |= run_calibration(args.games)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
