"""Landing-probability tables for the 1-step-lookahead value function V(s).

WHAT THIS COMPUTES
------------------
Other_analysis_field.md section 6.2 defines the short-term rent term ``R_s`` over
a 40-space 2d6 transition chain and the long-term term ``R_l`` over a uniform
board distribution. This module builds both tables. It is a pure-math library:
no engine, no run artifacts, no I/O except the optional cache.

1. ``transition_matrix()`` -> 40x40 row-stochastic ``T``.
   ``T[a, b]`` = P(a player standing on ``a`` ends its NEXT move on ``b``).
   Modelled per section 6.2 ("40-space 2d6 chain, including jail displacement"):

   - 2d6 over the 36 equiprobable ordered die pairs, moving (a + total) mod 40.
   - Space 30 (Go To Jail) displaces to space 10 (engine.py:364-370), so column
     30 is always 0 and its mass is folded into column 10.
   - Three consecutive doubles also jail the mover (engine.py:249-254). A
     memoryless per-step chain cannot carry a doubles counter, so this is
     approximated: with probability (1/6)^3 = 1/216 a move that would have been
     the third double lands on 10 instead. See LIMITATIONS.

2. ``chain(k)`` -> ``T`` raised to powers 1..k, stacked as ``(k, 40, 40)``.
   ``chain(k)[j]`` is the j+1-step-ahead distribution, i.e. ``Pr(a->p, j+1)``.

3. ``UNIFORM_LANDING`` -> the section-6.2 long-run rate, a flat 1/7 per space
   per lap (a lap averages 40/5.71 = 7 moves, so each space is hit 5.71 times
   per 40 spaces => 5.71/40 = 1/7 per space per lap).

4. ``stationary()`` -> the true stationary distribution of ``T``, provided ONLY
   as a diagnostic contrast against the flat 1/7 (jail makes the real
   distribution markedly non-uniform). V(s) uses the flat rate as specified;
   this function exists so the deviation can be reported, not silently ignored.

HOW TO READ THE OUTPUT
----------------------
``T`` and ``chain`` rows sum to 1.0 (assert-checked). Larger ``T[a, b]`` means a
player on ``a`` is likelier to land on ``b`` next move, so a property at ``b`` is
worth more against an opponent at ``a``. ``stationary()`` typically shows space
10 far above 1/40 (jail is an absorbing-ish attractor) and space 30 at exactly
0; if you compare it against the flat 1/7 used by ``R_l``, expect the jail
neighbourhood to be over-weighted by the flat rate and the post-jail stretch to
be under-weighted. That is a known, documented approximation, not a bug.

LIMITATIONS (must be carried into any write-up)
-----------------------------------------------
- Doubles are folded in as an unconditional 1/216 jail leak; the real process is
  history-dependent (needs a 3-state doubles counter).
- A player already IN jail does not move at all; the chain models free movers
  only. V(s) handles jailed players separately.
- Card-driven movement (taxi, go-to-jail cards, advance-to-GO) is not in the
  chain. Section 6.2 scopes the chain to dice movement.
- The chain ignores that a mover stops paying rent once bankrupt.

Library only, no main. Cached to stat/judge/data/landing_chain.npz by
``load_chain``; delete that file to force a rebuild.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import numpy.typing as npt

BOARD_SIZE = 40
JAIL_POSITION = 10
GO_TO_JAIL_POSITION = 30
DOUBLES_JAIL_PROBABILITY = 1.0 / 216.0

DATA_DIR = Path(__file__).resolve().parent / "data"
_CACHE_PATH = DATA_DIR / "landing_chain.npz"

#: Section 6.2 long-run rate: 1/7 landings per space per lap.
UNIFORM_LANDING = 1.0 / 7.0

_FloatArray = npt.NDArray[np.float64]


def dice_distribution() -> dict[int, float]:
    """Return P(total) for 2d6 over the 36 equiprobable ordered pairs."""
    totals: dict[int, float] = {}
    for first in range(1, 7):
        for second in range(1, 7):
            totals[first + second] = totals.get(first + second, 0.0) + 1.0 / 36.0
    return totals


def transition_matrix() -> _FloatArray:
    """Build the 40x40 one-move landing matrix described in the module docstring."""
    matrix = np.zeros((BOARD_SIZE, BOARD_SIZE), dtype=np.float64)
    totals = dice_distribution()
    for origin in range(BOARD_SIZE):
        for total, probability in totals.items():
            destination = (origin + total) % BOARD_SIZE
            if destination == GO_TO_JAIL_POSITION:
                destination = JAIL_POSITION
            matrix[origin, destination] += probability
    # Third-double jailing, approximated as a memoryless leak (see LIMITATIONS).
    leaked = matrix * DOUBLES_JAIL_PROBABILITY
    matrix -= leaked
    matrix[:, JAIL_POSITION] += leaked.sum(axis=1)
    _assert_stochastic(matrix)
    return matrix


def chain(steps: int, matrix: _FloatArray | None = None) -> _FloatArray:
    """Return powers 1..steps of the transition matrix, shaped (steps, 40, 40)."""
    if steps < 1:
        raise ValueError("steps must be >= 1")
    base = transition_matrix() if matrix is None else matrix
    stack = np.empty((steps, BOARD_SIZE, BOARD_SIZE), dtype=np.float64)
    current = base.copy()
    for index in range(steps):
        stack[index] = current
        if index + 1 < steps:
            current = current @ base
    _assert_stochastic(stack.reshape(-1, BOARD_SIZE))
    return stack


def stationary(matrix: _FloatArray | None = None, iterations: int = 10_000) -> _FloatArray:
    """Return the stationary landing distribution (diagnostic contrast to 1/7)."""
    base = transition_matrix() if matrix is None else matrix
    vector = np.full(BOARD_SIZE, 1.0 / BOARD_SIZE, dtype=np.float64)
    for _ in range(iterations):
        updated = vector @ base
        if np.allclose(updated, vector, atol=1e-15):
            return updated
        vector = updated
    return vector


def load_chain(steps: int, *, use_cache: bool = True) -> _FloatArray:
    """Return ``chain(steps)``, cached under stat/judge/data/."""
    if use_cache and _CACHE_PATH.exists():
        with np.load(_CACHE_PATH) as archive:
            cached = archive["chain"]
            if cached.shape[0] >= steps:
                return np.asarray(cached[:steps], dtype=np.float64)
    stack = chain(steps)
    if use_cache:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(_CACHE_PATH, chain=stack)
    return stack


def _assert_stochastic(matrix: _FloatArray) -> None:
    sums = matrix.sum(axis=1)
    if not np.allclose(sums, 1.0, atol=1e-12):
        raise AssertionError("landing matrix rows must sum to 1")
    if float(matrix.min()) < 0.0:
        raise AssertionError("landing matrix must be non-negative")
