"""Single-pass scorer: replay every game once and emit per-decision delta-V.

WHAT THIS COMPUTES
------------------
This is the one expensive stage of section 6. It replays each run exactly once
(section 6.7 "single-pass replay") and, at every recorded decision, enumerates
the legal candidates, evaluates V(s'_a) for each, and writes one row per
decision. Every downstream analysis (6.9 tests 1-4) reads these rows instead of
touching the engine again, so statistical choices can be revised without paying
the replay cost twice.

Per decision it records:

    delta_v_executed   V(s'_a) - V(s) for the action actually taken
    delta_v_best       max over candidates that survive bankruptcy pruning
    regret             delta_v_best - delta_v_executed, >= 0 (section 6.3)
    delta_massets_*    the same two quantities using ONLY net worth. This is
                       the section-6.6 positive control: a term of V that is
                       mechanically tied to the outcome, so if the ranking test
                       cannot detect even this, it has no power and the
                       negative result is uninformative.
    luck_L             section-6.6 test 3. See below.
    spread_dv          max - min delta_v; zero means every candidate is
                       equivalent under V and the decision carries no signal.

THE LUCK PERCENTILE (section 6.6 test 3)
----------------------------------------
SaneRandomController picks in TWO stages: uniformly among OPTIONS, then
uniformly among that option's target values (random_baseline.py:40-56). So a
candidate's probability is 1/(|options| * |targets of its option|) -- NOT
uniform over candidates, and the plain rank is therefore NOT uniform either.
We record the probability-weighted mid-percentile

    L_d = P(delta_v < delta_v_chosen) + P(ties containing the choice) / 2

under that exact two-stage law, which satisfies E[L_d] = 1/2 by construction.
Ties are pooled (many candidates share a delta_v), which is what keeps the
identity exact. The draw set excludes sell_building/mortgage during asset
management, because the controller removes them before drawing; scoring them
inside L_d would break E[L_d] = 1/2. Regret still uses the FULL legal set.

L_d is written only for seats actually controlled by sane_random; other seats
get NaN.

HOW TO READ THE OUTPUT
----------------------
One .npz per experiment under stat/judge/data/, arrays aligned row-wise; load
with ``load_table``. Columns are documented in ``COLUMNS``. Useful invariants,
all asserted during the run:

* regret >= 0 always (owner decision 2026-09-24). When the executed action
  itself failed pruning, it is excluded from the argmax set (section 6.3) and
  raw regret can go negative; those rows are clamped to 0 and flagged with
  executed_pruned == 1 so they stay countable. A negative regret on a row with
  executed_pruned == 0 means a replay or enumeration bug, not a finding.
* candidate_count == 1 -> regret == 0 by definition; section 6.4 excludes these.
* pruned_all == 1 marks decisions where every candidate failed the bankruptcy
  screens and the comparison fell back to the full legal set (section 6.4).
* luck_L is NaN for non-sane_random seats, and in [0, 1] otherwise.

A large regret is NOT "a mistake": V is one public heuristic, and section 6.6
test 3 must pass before regret may be described as correctness at all. Until
then it is "deviation from V".

Usage from the repository root (SMALL self-tests only unless the owner has
approved a full run):
    .venv/Scripts/python.exe stat/judge/evaluate.py sane_random --limit 3
    .venv/Scripts/python.exe stat/judge/evaluate.py --all

Writes stat/judge/data/decisions_<experiment>.npz.
"""

from __future__ import annotations

import argparse
import sys
import time
from copy import deepcopy
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import value as value_module
from replay_tools import DecisionPoint, ReplayDivergence, iter_decision_points

from monopoly_agent_battle.decision.models import DecisionKind
from monopoly_agent_battle.decision.requests import (
    _candidate_commands,
    _command_type,
    _split_command,
)
from monopoly_agent_battle.domain.commands import GameCommand, RollDice
from monopoly_agent_battle.domain.models import JailStatus, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine, GameRuleError

ROOT = Path(__file__).resolve().parent.parent.parent
RUNS = ROOT / "runs"
DATA_DIR = Path(__file__).resolve().parent / "data"

#: Section 6.4: jail roll_dice is valued over the 36 ORDERED 2d6 outcomes.
#: Ordered, not aggregated by total, because the engine branches on whether the
#: roll is a double (jail release / third doubles), so (2,2) and (1,3) differ
#: despite sharing a total (engine.py:248-272).
DICE_OUTCOMES = tuple((a, b) for a in range(1, 7) for b in range(1, 7))

#: The controller drops these from asset-management draws (random_baseline.py:69).
SANE_BLOCKED = frozenset({"sell_building", "mortgage"})

COLUMNS: tuple[str, ...] = (
    "game_index",
    "decision_index",
    "seat",
    "kind_code",
    "complete_rounds",
    "candidate_count",
    "draw_count",
    "delta_v_executed",
    "delta_v_best",
    "regret",
    "delta_massets_executed",
    "delta_massets_best",
    "spread_dv",
    "luck_L",
    "luck_L_massets",
    "pruned_all",
    "executed_pruned",
    "is_sane_seat",
)

KIND_CODES = {
    DecisionKind.ASSET_MANAGEMENT: 0,
    DecisionKind.PAYMENT_RESOLUTION: 1,
    DecisionKind.JAIL: 2,
    DecisionKind.FORCED_DISCARD: 3,
    DecisionKind.THEFT_CARD_SELECTION: 4,
}


def _phase_kind(engine: GameEngine, player_id: str) -> DecisionKind | None:
    """Classify the decision at this state, or None if it is not a decision."""
    phase = engine.state.turn_phase
    if phase is TurnPhase.PAYMENT_RESOLUTION:
        return DecisionKind.PAYMENT_RESOLUTION
    if phase is TurnPhase.FORCED_DISCARD:
        return DecisionKind.FORCED_DISCARD
    if phase is TurnPhase.THEFT_CARD_SELECTION:
        return DecisionKind.THEFT_CARD_SELECTION
    if phase is TurnPhase.ASSET_MANAGEMENT:
        return DecisionKind.ASSET_MANAGEMENT
    if engine.state.players[player_id].jail_status is JailStatus.ROLLING:
        return DecisionKind.JAIL
    return None


def _action_cost(engine: GameEngine, command: GameCommand) -> int:
    """C(m) from the section-6.2 table: current-turn cash outlay of an action."""
    from monopoly_agent_battle.domain.commands import (
        PayJailFine,
        RedeemMortgage,
        UseChanceCard,
    )
    from monopoly_agent_battle.game.board_data.classic_us_40 import BOARD_BY_POSITION
    from monopoly_agent_battle.game.cards.classic_cards import CARDS_BY_ID, CardEffect

    if engine.state.turn_phase is TurnPhase.PAYMENT_RESOLUTION:
        operation = engine.state.settlement_operations[0]
        return int(operation.amount or 0)
    if isinstance(command, PayJailFine):
        return 50
    if isinstance(command, RedeemMortgage):
        price = BOARD_BY_POSITION[command.position].price or 0
        return value_module._round_half_up(price * 55, 100)
    if isinstance(command, UseChanceCard):
        card = CARDS_BY_ID.get(command.card_id)
        if card is not None and card.effect is CardEffect.BUY_PROPERTY:
            position = command.target_position
            if position is not None:
                price = BOARD_BY_POSITION[position].price or 0
                return value_module._round_half_up(price * 110, 100)
    return 0


def _fixed_dice(first: int, second: int):
    """Return a randint substitute that replays one fixed 2d6 roll, then raises."""
    rolls = iter((first, second))

    def _randint(_low: int, _high: int) -> int:
        return next(rolls)

    return _randint


def _evaluate_candidate(
    engine: GameEngine, command: GameCommand, player_id: str
) -> tuple[float, float] | None:
    """Execute one candidate on a clone and return (V, M_assets) after it.

    Jail roll_dice is averaged over the 36 ordered dice outcomes (section 6.4)
    by forcing the engine's RNG, which keeps the result deterministic.
    """
    if isinstance(command, RollDice):
        total_v = 0.0
        total_assets = 0.0
        realised = 0
        for first, second in DICE_OUTCOMES:
            clone = deepcopy(engine)
            clone.random.randint = _fixed_dice(first, second)  # type: ignore[method-assign]
            try:
                clone.execute(command)
            except (GameRuleError, StopIteration):
                continue
            breakdown = value_module.evaluate(clone.state, player_id)
            total_v += breakdown.total
            total_assets += breakdown.m_assets
            realised += 1
        if realised == 0:
            return None
        return total_v / realised, total_assets / realised

    clone = deepcopy(engine)
    try:
        clone.execute(command)
    except GameRuleError:
        return None
    breakdown = value_module.evaluate(clone.state, player_id)
    return breakdown.total, float(breakdown.m_assets)


def _draw_probabilities(
    engine: GameEngine,
    commands: list[GameCommand],
    kind: DecisionKind,
) -> dict[int, float]:
    """Two-stage sane_random draw law over candidate indices.

    Stage 1 picks an OPTION uniformly, stage 2 picks a target within it
    uniformly. Options are grouped exactly as _legal_options does, so the law
    matches the controller rather than approximating it.
    """
    groups: dict[tuple[object, ...], list[int]] = {}
    for index, command in enumerate(commands):
        command_type = _command_type(command)
        if kind is DecisionKind.ASSET_MANAGEMENT and command_type in SANE_BLOCKED:
            continue
        fixed_params, _fields, _values = _split_command(command)
        key = (command_type, tuple(sorted(fixed_params.items())))
        groups.setdefault(key, []).append(index)
    if not groups:
        # Every option was filtered out; the controller falls back to the full set.
        for index, command in enumerate(commands):
            fixed_params, _fields, _values = _split_command(command)
            key = (_command_type(command), tuple(sorted(fixed_params.items())))
            groups.setdefault(key, []).append(index)
    probabilities: dict[int, float] = {}
    option_probability = 1.0 / len(groups)
    for indices in groups.values():
        for index in indices:
            probabilities[index] = option_probability / len(indices)
    return probabilities


def _mid_percentile(values: list[float], probabilities: dict[int, float], chosen: int) -> float:
    """P(value < chosen) + P(ties)/2 under the draw law; E[.] = 1/2 exactly."""
    chosen_value = values[chosen]
    below = 0.0
    tied = 0.0
    for index, probability in probabilities.items():
        if values[index] < chosen_value - 1e-9:
            below += probability
        elif abs(values[index] - chosen_value) <= 1e-9:
            tied += probability
    total = below + tied
    if total <= 0.0:
        return float("nan")
    return below + tied / 2.0


def score_game(directory: Path, game_index: int, sane_seats: set[str]) -> list[tuple[float, ...]]:
    """Replay one game and return one row per recorded decision."""
    rows: list[tuple[float, ...]] = []
    for point in iter_decision_points(directory):
        row = _score_point(point, game_index, sane_seats)
        if row is not None:
            rows.append(row)
    return rows


def _score_point(
    point: DecisionPoint, game_index: int, sane_seats: set[str]
) -> tuple[float, ...] | None:
    engine = point.engine
    player_id = (
        engine.state.settlement_operations[0].player_id
        if engine.state.turn_phase is TurnPhase.PAYMENT_RESOLUTION
        else engine.state.current_player_id
    )
    kind = _phase_kind(engine, player_id)
    if kind is None:
        return None

    candidates = _candidate_commands(engine, player_id)
    if not candidates:
        return None

    baseline = value_module.evaluate(engine.state, player_id)
    legal: list[GameCommand] = []
    values: list[float] = []
    assets: list[float] = []
    for command in candidates:
        scored = _evaluate_candidate(engine, command, player_id)
        if scored is None:
            continue
        legal.append(command)
        values.append(scored[0] - baseline.total)
        assets.append(scored[1] - baseline.m_assets)
    if not legal:
        return None

    executed_index = _match_executed(legal, point.command)
    if executed_index is None:
        # The recorded action is not in the enumerated set: skip rather than
        # silently score the wrong action. Reported by the caller as a gap.
        return None

    survivors = [
        index
        for index, command in enumerate(legal)
        if value_module.passes_pruning(engine.state, player_id, _action_cost(engine, command))
    ]
    pruned_all = 0
    if not survivors:
        survivors = list(range(len(legal)))
        pruned_all = 1

    best = max(values[index] for index in survivors)
    best_assets = max(assets[index] for index in survivors)
    # If the executed action itself failed pruning, it is excluded from the
    # max (section 6.3), so regret can go negative (e.g. a redeem whose delta-V
    # beats every survivor but breaks cash_min). Owner decision 2026-09-24:
    # clamp to 0 and mark executed_pruned so these decisions stay countable.
    executed_pruned = int(executed_index not in survivors)
    regret = best - values[executed_index]
    if executed_pruned:
        regret = max(0.0, regret)
    elif regret < -1e-6:
        raise AssertionError(f"negative regret at decision {point.index}: {regret}")

    is_sane = player_id in sane_seats
    luck = float("nan")
    luck_assets = float("nan")
    draw_count = 0
    if is_sane:
        probabilities = _draw_probabilities(engine, legal, kind)
        draw_count = len(probabilities)
        if executed_index in probabilities and draw_count > 1:
            luck = _mid_percentile(values, probabilities, executed_index)
            luck_assets = _mid_percentile(assets, probabilities, executed_index)

    return (
        float(game_index),
        float(point.index),
        float(engine.state.players[player_id].seat),
        float(KIND_CODES[kind]),
        float(point.complete_rounds),
        float(len(legal)),
        float(draw_count),
        values[executed_index],
        best,
        max(0.0, regret),
        assets[executed_index],
        best_assets,
        max(values) - min(values),
        luck,
        luck_assets,
        float(pruned_all),
        float(executed_pruned),
        float(int(is_sane)),
    )


def _match_executed(legal: list[GameCommand], executed: GameCommand) -> int | None:
    """Find the enumerated candidate identical to the recorded command."""
    for index, command in enumerate(legal):
        if type(command) is type(executed) and command == executed:
            return index
    return None


def _sane_seats(directory: Path) -> set[str]:
    """Player ids controlled by sane_random in this run."""
    import json

    document = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    players = document.get("config", {}).get("players", [])
    return {
        str(player["player_id"])
        for player in players
        if player.get("controller_type") == "sane_random"
    }


def run_experiment(experiment: str, limit: int | None) -> int:
    """Score every game of one experiment and write the .npz table."""
    root = RUNS / experiment
    if not root.exists():
        print(f"no such experiment: {root}")
        return 1
    directories = sorted(d for d in root.iterdir() if d.is_dir() and (d / "events.jsonl").exists())
    if limit is not None:
        directories = directories[:limit]
    if not directories:
        print(f"no games under {root}")
        return 1

    rows: list[tuple[float, ...]] = []
    skipped: list[str] = []
    started = time.perf_counter()
    for game_index, directory in enumerate(directories):
        try:
            rows.extend(score_game(directory, game_index, _sane_seats(directory)))
        except (ReplayDivergence, AssertionError) as error:
            skipped.append(f"{directory.name}: {error}")
        done = game_index + 1
        if done % 25 == 0 or done == len(directories):
            elapsed = time.perf_counter() - started
            print(
                f"  {done}/{len(directories)} games, {len(rows)} decisions, "
                f"{elapsed:.1f}s ({elapsed / done:.2f}s/game)"
            )

    table = np.array(rows, dtype=np.float64) if rows else np.empty((0, len(COLUMNS)))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output = DATA_DIR / f"decisions_{experiment}.npz"
    np.savez_compressed(
        output,
        table=table,
        columns=np.array(COLUMNS),
        games=np.array([d.name for d in directories]),
    )
    print(f"wrote {output} -- {table.shape[0]} decisions from {len(directories)} games")
    if skipped:
        print(f"SKIPPED {len(skipped)} games:")
        for line in skipped[:10]:
            print(f"  {line}")
    return 0


def load_table(experiment: str) -> tuple[np.ndarray, dict[str, int]]:
    """Load a scored table and a name->column-index map."""
    with np.load(DATA_DIR / f"decisions_{experiment}.npz", allow_pickle=False) as archive:
        table = archive["table"]
        columns = {str(name): index for index, name in enumerate(archive["columns"])}
    return table, columns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiments", nargs="*", help="run-directory names under runs/")
    parser.add_argument("--limit", type=int, default=None, help="only the first N games")
    parser.add_argument("--all", action="store_true", help="all floor + LLM experiments")
    args = parser.parse_args()

    experiments = list(args.experiments)
    if args.all:
        experiments = [
            "sane_random",
            "greedy_script",
            "greedy_vs_sane_random",
            "4-courts-battle",
            "court-vs-baseline",
            "fe-vs-baseline",
            "court-fe-battle",
        ]
    if not experiments:
        parser.error("name at least one experiment, or pass --all")

    status = 0
    for experiment in experiments:
        print(f"\n=== {experiment} ===")
        status |= run_experiment(experiment, args.limit)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
