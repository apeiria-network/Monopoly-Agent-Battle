"""The 1-step-lookahead value function V(s) from Other_analysis_field.md section 6.2.

WHAT THIS COMPUTES
------------------
    V(s) = M_assets + R_s + R_l + M_monopoly

evaluated for ONE focal player on a real engine ``GameState``. Every term is in
game currency, so V is directly comparable across candidates of the same
decision. ``delta_v`` = V(s'_a) - V(s) is what section 6.3 turns into regret.

    M_assets    Project net worth, delegated verbatim to
                rules/classic_level0.net_worth (cash + property price +
                building value - mortgaged property price, full deduction).

    R_s         Short-horizon expected NET rent flow over the next k_s = 5
                PLAYER TURNS, dealt round-robin from the current player in seat
                order. For each future turn we advance only the mover, using the
                j-step 2d6 landing chain from that mover's current position:
                  * mover is an opponent  -> + expected rent it pays me
                  * mover is me           -> - expected rent I pay others
                Truncated to min(5, 4H) turns where H = 50 - complete_rounds.

    R_l         Same net-flow idea over k_l = 3 laps, but with the section-6.2
                flat long-run rate: every player lands on every space 1/7 times
                per lap. Truncated to min(3, H/5.71) laps.

    M_monopoly  Future BUILDING upside only (current rents are already inside
                R_s/R_l, so counting them again would double-count). Budget
                F = cash + laps*200 + R_l - cash_min is spent greedily by
                "rent gain per build dollar" across own unmortgaged streets, up
                to level 5 (hotel). Per section 6.2 the color group need NOT be
                complete and the monopoly x2 is NOT applied. F is a HARD budget:
                a build step the budget cannot cover is not counted, matching
                Gopalakrishnan et al., where F is likewise spent down. Railroads
                and utilities cannot be built on and are excluded. The rent
                increments are multiplied by the expected opponent landings
                inside the R_l window.

Rent is resolved with the engine's own rules (engine.py:486-521):
mortgaged or jailed owner -> 0; RENT_FREEZE -> 0; RENT_SURGE -> x2; a complete
unbuilt color group -> x2; the payer's rent_waivers cancel the earliest
payments. Under an ALLIANCE the payer is charged the FULL amount and the split
happens afterwards at settlement (engine.py:1440), so only the OWNER's receipt
is halved -- the two perspectives differ and ``owner_income`` selects between
them. Ongoing effects are decayed turn by turn across the R_s window, so an
effect with 1 turn left stops applying partway through.

Every rule above is verified against the live engine by
``calibrate.py --check`` (8,203 rent comparisons, both perspectives). That
check found three real defects here -- alliance split direction, half-up
rounding semantics, and applying the split before instead of after the surge --
so it must keep passing before any score is trusted.

Also exported: ``passes_pruning`` -- the two section-6.2 bankruptcy screens.
Candidates failing them are dropped before the argmax; if every candidate fails,
the caller falls back to the full legal set and flags the decision.

HOW TO READ THE OUTPUT
----------------------
``evaluate`` returns a ``ValueBreakdown`` so a number can always be traced to a
term. Rough scale on this board: M_assets dominates (thousands), R_s is small
(tens), R_l and M_monopoly are hundreds. A candidate that mortgages a property
shows a clearly negative delta (net worth drops by the full purchase price and
that property's rent leaves R_s/R_l); redeeming shows the mirror image. If a
delta is large and positive, expect it to come from M_assets (bought/won
property) or M_monopoly (unlocked a build path), and check which.

Terms go to zero as the game ends: at H = 0 all of R_s, R_l and M_monopoly
vanish and V collapses to plain net worth. That is the section-6.2 truncation
working as designed -- it is why late-game candidates stay commensurable with
candidates that end the game, and why late-game regret is legitimately small.

DOCUMENTED APPROXIMATIONS (section 6.2, owner-confirmed 2026-09-24)
-------------------------------------------------------------------
1. M_monopoly uses the level actually AFFORDED by the budget F, not an
   unconditional hotel; F is a hard constraint.
2. Utilities are priced at the 2d6 expectation (7), since the engine charges by
   the actual roll and no roll exists inside an expectation window.
3. Railroads and utilities are excluded from M_monopoly (cannot be built on).
4. Jailed players are held still for the turns they are stuck; the landing chain
   only models free movers.
5. The rent-waiver budget is consumed against EXPECTED landing mass
   (fractional), since R_s is itself an expectation.
6. Card-driven movement is not modelled; section 6.2 scopes the chain to dice.
2. Jailed players are held still for the turns they are stuck; the landing chain
   only models free movers.
3. The rent-waiver budget is consumed against EXPECTED landing mass (fractional),
   since R_s is itself an expectation.

Library only, no main. Used by evaluate.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy.typing as npt
from landing import BOARD_SIZE, UNIFORM_LANDING, load_chain

from monopoly_agent_battle.domain.models import (
    GameState,
    JailStatus,
    OngoingEffectKind,
    PlayerState,
    SpaceKind,
)
from monopoly_agent_battle.game.board_data.classic_us_40 import (
    BOARD_BY_POSITION,
    COLOR_GROUPS,
    RAILROAD_RENTS,
)
from monopoly_agent_battle.game.rules.classic_level0 import net_worth

#: Section 6.2 frozen constants.
K_SHORT_TURNS = 5
K_LONG_LAPS = 3
CASH_MIN = 100
GO_SALARY = 200
MAX_BUILDING_LEVEL = 5
ROUND_LIMIT = 50
TURNS_PER_ROUND = 4
#: A lap averages 40 / 5.71 moves, so 5.71 self-turns make one lap.
TURNS_PER_LAP = 5.71


@dataclass(frozen=True, slots=True)
class ValueBreakdown:
    """One evaluation of V(s), kept term by term for auditing."""

    total: float
    m_assets: int
    r_short: float
    r_long: float
    m_monopoly: float
    short_turns: int
    long_laps: float

    def __float__(self) -> float:
        return self.total


def remaining_rounds(state: GameState) -> int:
    """Return H = 50 - complete_rounds, floored at 0."""
    return max(0, ROUND_LIMIT - state.complete_rounds)


def evaluate(
    state: GameState,
    player_id: str,
    *,
    hotel_ceiling: bool = False,
) -> ValueBreakdown:
    """Return the section-6.2 value of ``state`` from ``player_id``'s point of view."""
    player = state.players[player_id]
    assets = net_worth(player, state)
    if player.bankrupt:
        return ValueBreakdown(float(assets), assets, 0.0, 0.0, 0.0, 0, 0.0)

    horizon = remaining_rounds(state)
    short_turns = min(K_SHORT_TURNS, TURNS_PER_ROUND * horizon)
    long_laps = min(float(K_LONG_LAPS), horizon / TURNS_PER_LAP)

    r_short = _short_term_flow(state, player_id, short_turns)
    r_long = _long_term_flow(state, player_id, long_laps)
    monopoly = _monopoly_upside(state, player_id, long_laps, r_long, hotel_ceiling=hotel_ceiling)
    total = assets + r_short + r_long + monopoly
    return ValueBreakdown(total, assets, r_short, r_long, monopoly, short_turns, long_laps)


def _active_players(state: GameState) -> list[PlayerState]:
    return [p for p in state.players.values() if not p.bankrupt]


def _turn_order(state: GameState, turns: int) -> list[str]:
    """Deal the next ``turns`` player turns round-robin from the current player."""
    active = sorted(_active_players(state), key=lambda p: p.seat)
    if not active:
        return []
    ids = [p.player_id for p in active]
    try:
        start = ids.index(state.current_player_id)
    except ValueError:
        start = 0
    return [ids[(start + offset) % len(ids)] for offset in range(turns)]


def _short_term_flow(state: GameState, player_id: str, turns: int) -> float:
    """Expected net rent over the next ``turns`` player turns (dice-chain landings)."""
    if turns <= 0:
        return 0.0
    order = _turn_order(state, turns)
    if not order:
        return 0.0
    chain = load_chain(max(1, turns))
    moves_taken: dict[str, int] = {pid: 0 for pid in state.players}
    elapsed_turns: dict[str, int] = {pid: 0 for pid in state.players}
    waiver_budget = float(state.players[player_id].rent_waivers)
    flow = 0.0

    for mover_id in order:
        mover = state.players[mover_id]
        if mover.bankrupt:
            continue
        if mover.jail_status is not JailStatus.FREE:
            # Held in jail: no movement, no landing, but the turn still elapses.
            elapsed_turns[mover_id] += 1
            continue
        step = moves_taken[mover_id]
        moves_taken[mover_id] += 1
        elapsed_turns[mover_id] += 1
        distribution = chain[step][mover.position]

        if mover_id == player_id:
            paid, waiver_budget = _expected_payment(
                state, player_id, distribution, elapsed_turns, waiver_budget
            )
            flow -= paid
        else:
            flow += _expected_income(state, player_id, mover_id, distribution, elapsed_turns)
    return flow


def _long_term_flow(state: GameState, player_id: str, laps: float) -> float:
    """Expected net rent over ``laps`` laps at the flat 1/7 per-space rate."""
    if laps <= 0.0:
        return 0.0
    rate = laps * UNIFORM_LANDING
    others = [p for p in _active_players(state) if p.player_id != player_id]
    if not others:
        return 0.0
    flow = 0.0
    # Long-run term: ongoing effects have expired, so no decay bookkeeping.
    for position in range(BOARD_SIZE):
        property_state = state.properties.get(position)
        if property_state is None or property_state.owner_id is None:
            continue
        if property_state.owner_id == player_id:
            rent = _rent_for(state, position, player_id, elapsed=None, owner_income=True)
            flow += rate * rent * len(others)
        else:
            rent = _rent_for(state, position, property_state.owner_id, elapsed=None)
            flow -= rate * rent
    return flow


def _expected_income(
    state: GameState,
    player_id: str,
    mover_id: str,
    distribution: npt.NDArray,
    elapsed: dict[str, int],
) -> float:
    """Expected rent the focal player collects from ``mover_id``'s single move."""
    total = 0.0
    for position in state.players[player_id].properties:
        probability = float(distribution[position])
        if probability <= 0.0:
            continue
        rent = _rent_for(
            state, position, player_id, elapsed=elapsed, payer_id=mover_id, owner_income=True
        )
        total += probability * rent
    return total


def _expected_payment(
    state: GameState,
    player_id: str,
    distribution: npt.NDArray,
    elapsed: dict[str, int],
    waiver_budget: float,
) -> tuple[float, float]:
    """Expected rent the focal player pays on its own move, net of rent waivers."""
    total = 0.0
    exposure = 0.0
    for position in range(BOARD_SIZE):
        property_state = state.properties.get(position)
        if property_state is None or property_state.owner_id is None:
            continue
        if property_state.owner_id == player_id:
            continue
        probability = float(distribution[position])
        if probability <= 0.0:
            continue
        rent = _rent_for(
            state, position, property_state.owner_id, elapsed=elapsed, payer_id=player_id
        )
        if rent <= 0.0:
            continue
        total += probability * rent
        exposure += probability
    if waiver_budget > 0.0 and exposure > 0.0:
        # Waivers cancel the earliest rent events; consume expected landing mass.
        consumed = min(waiver_budget, exposure)
        total *= max(0.0, 1.0 - consumed / exposure)
        waiver_budget -= consumed
    return total, waiver_budget


def _effect_active(
    state: GameState,
    kind: OngoingEffectKind,
    color_group: str,
    elapsed: dict[str, int] | None,
) -> bool:
    """Whether a color-group effect still applies, decayed by its source's turns."""
    for effect in state.ongoing_effects:
        if effect.kind is not kind or effect.color_group != color_group:
            continue
        if elapsed is None:
            continue  # long-run term: treat every timed effect as expired
        spent = elapsed.get(effect.source_player_id, 0)
        if effect.remaining_turns - spent > 0:
            return True
    return False


def _alliance_partner(state: GameState, player_id: str) -> str | None:
    for effect in state.ongoing_effects:
        if effect.kind is not OngoingEffectKind.ALLIANCE:
            continue
        if player_id == effect.source_player_id:
            return effect.target_player_id
        if player_id == effect.target_player_id:
            return effect.source_player_id
    return None


def _rent_for(
    state: GameState,
    position: int,
    owner_id: str,
    *,
    elapsed: dict[str, int] | None,
    payer_id: str | None = None,
    owner_income: bool = False,
) -> float:
    """Effective rent for one landing, mirroring engine.py:486-521.

    ``owner_income`` selects the perspective. The payer always hands over the
    FULL amount; an ALLIANCE splits it afterwards at settlement
    (engine.py:1445), so only the owner's receipt is halved. Passing the wrong
    perspective silently misprices every allied property.
    """
    property_state = state.properties[position]
    owner = state.players[owner_id]
    if property_state.mortgaged or owner.bankrupt:
        return 0.0
    if owner.jail_status is not JailStatus.FREE:
        return 0.0
    space = BOARD_BY_POSITION[position]
    color_group = space.color_group

    if space.kind is SpaceKind.STREET:
        if color_group is None:
            return 0.0
        rent = float(space.rents[property_state.building_level])
        group = COLOR_GROUPS[color_group]
        if property_state.building_level == 0 and all(
            state.properties[item].owner_id == owner_id for item in group
        ):
            rent *= 2
    elif space.kind is SpaceKind.RAILROAD:
        count = sum(BOARD_BY_POSITION[item].kind is SpaceKind.RAILROAD for item in owner.properties)
        rent = float(RAILROAD_RENTS[count - 1]) if count else 0.0
    elif space.kind is SpaceKind.UTILITY:
        count = sum(BOARD_BY_POSITION[item].kind is SpaceKind.UTILITY for item in owner.properties)
        rent = 7.0 * (10 if count == 2 else 4)  # expected 2d6 total is 7
    else:
        return 0.0

    if color_group is not None:
        if _effect_active(state, OngoingEffectKind.RENT_FREEZE, color_group, elapsed):
            return 0.0
        if _effect_active(state, OngoingEffectKind.RENT_SURGE, color_group, elapsed):
            rent *= 2
    if rent <= 0.0:
        return 0.0
    if payer_id is not None and payer_id == owner_id:
        return 0.0

    # ALLIANCE settlement (engine.py:1440-1452). The payer is charged the FULL
    # post-surge rent; the split happens afterwards, the owner keeping
    # half_up(G, 2) and the partner receiving the remainder (bank-adjusted so
    # both end on half_up). Perspectives therefore differ, and the split is
    # applied AFTER surge/freeze -- reversing that order double-counts surge.
    partner_id = _alliance_partner(state, owner_id)
    if partner_id is not None:
        half = float(_round_ratio_half_up(int(rent), 2))
        if owner_income:
            return half
        if payer_id is not None and payer_id == partner_id:
            # The payer is the ally: it pays G, then receives the transfer back.
            return rent - half
    return rent


def _monopoly_upside(
    state: GameState,
    player_id: str,
    laps: float,
    r_long: float,
    *,
    hotel_ceiling: bool,
) -> float:
    """Future build-out upside, budget-limited, counted only as rent INCREMENTS."""
    if laps <= 0.0:
        return 0.0
    player = state.players[player_id]
    opponents = len([p for p in _active_players(state) if p.player_id != player_id])
    if opponents == 0:
        return 0.0
    budget = player.cash + min(float(K_LONG_LAPS), laps) * GO_SALARY + r_long - CASH_MIN
    if budget <= 0.0:
        return 0.0

    # Candidate build steps: (gain per dollar, cost, position, rent increment).
    steps: list[tuple[float, int, int, float]] = []
    for position in sorted(player.properties):
        space = BOARD_BY_POSITION[position]
        property_state = state.properties[position]
        if space.kind is not SpaceKind.STREET or property_state.mortgaged:
            continue
        cost = space.building_cost or 0
        if cost <= 0:
            continue
        for level in range(property_state.building_level, MAX_BUILDING_LEVEL):
            gain = float(space.rents[level + 1] - space.rents[level])
            steps.append((gain / cost, cost, position, gain))
    steps.sort(key=lambda item: item[0], reverse=True)

    rate = laps * UNIFORM_LANDING * opponents
    increment = 0.0
    for _, cost, _position, gain in steps:
        if not hotel_ceiling and budget < cost:
            continue
        budget -= cost
        increment += gain * rate
    return increment


def passes_pruning(
    state: GameState,
    player_id: str,
    cost_of_action: int,
) -> bool:
    """Apply the two section-6.2 bankruptcy screens over a 1-round (4-turn) window."""
    player = state.players[player_id]
    if player.bankrupt:
        return False
    owed = _one_round_income(state, player_id)
    pay = _one_round_payment(state, player_id)
    if player.cash + (owed - pay) - cost_of_action < CASH_MIN:
        return False
    worst = _worst_single_rent(state, player_id)
    scaled = _liquidation_value(state, player_id)
    return player.cash + owed + scaled - cost_of_action - worst > 0


def _one_round_income(state: GameState, player_id: str) -> float:
    """Expected rent collected over one complete round (each opponent moves once)."""
    chain = load_chain(1)
    total = 0.0
    for other in _active_players(state):
        if other.player_id == player_id or other.jail_status is not JailStatus.FREE:
            continue
        distribution = chain[0][other.position]
        for position in state.players[player_id].properties:
            rent = _rent_for(
                state,
                position,
                player_id,
                elapsed={},
                payer_id=other.player_id,
                owner_income=True,
            )
            total += float(distribution[position]) * rent
    return total


def _one_round_payment(state: GameState, player_id: str) -> float:
    player = state.players[player_id]
    if player.jail_status is not JailStatus.FREE:
        return 0.0
    chain = load_chain(1)
    distribution = chain[0][player.position]
    total = 0.0
    for position in range(BOARD_SIZE):
        property_state = state.properties.get(position)
        if property_state is None or property_state.owner_id is None:
            continue
        if property_state.owner_id == player_id:
            continue
        rent = _rent_for(state, position, property_state.owner_id, elapsed={}, payer_id=player_id)
        total += float(distribution[position]) * rent
    return total


def _worst_single_rent(state: GameState, player_id: str) -> float:
    """Highest single rent any opponent could charge right now."""
    worst = 0.0
    for position in range(BOARD_SIZE):
        property_state = state.properties.get(position)
        if property_state is None or property_state.owner_id is None:
            continue
        if property_state.owner_id == player_id:
            continue
        rent = _rent_for(state, position, property_state.owner_id, elapsed={}, payer_id=player_id)
        worst = max(worst, rent)
    return worst


def _liquidation_value(state: GameState, player_id: str) -> int:
    """Cash raisable now: buildings at half cost, then unmortgaged land at 50%."""
    total = 0
    for position in state.players[player_id].properties:
        space = BOARD_BY_POSITION[position]
        property_state = state.properties[position]
        if property_state.mortgaged:
            continue
        if property_state.building_level:
            total += property_state.building_level * ((space.building_cost or 0) // 2)
        total += _round_half_up((space.price or 0) * 50, 100)
    return total


def _round_half_up(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


def _round_ratio_half_up(numerator: int, denominator: int) -> int:
    """Mirror engine.py:58 C-028 half-up semantics for a ratio n/d."""
    return (numerator * 2 + denominator) // (denominator * 2)
