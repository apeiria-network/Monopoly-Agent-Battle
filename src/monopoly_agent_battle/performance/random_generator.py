"""Temporary officer-performance text generator for court prompt integration."""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable

from monopoly_agent_battle.decision.models import DecisionRequest

_OFFICERS = ("丞相", "太尉")

PerformanceGenerator = Callable[[DecisionRequest], str | None]


def random_officer_performance(request: DecisionRequest) -> str | None:
    """Return deterministic random officer-performance text for the counsellor.

    ``complete_rounds`` is the number of completed rounds before the current
    decision.  The first/basic window becomes available after one completed
    round, and the long window after three.  This generator is deliberately
    kept outside the Qin workflow so it can later be replaced by the real
    performance calculator without changing Qin prompt assembly.
    """
    if request.complete_rounds < 1:
        return None

    seed_material = (
        f"officer-performance-v1:{request.game_id}:"
        f"{request.player_id}:{request.complete_rounds}:{request.decision_id}"
    ).encode()
    seed = int.from_bytes(hashlib.sha256(seed_material).digest(), "big")
    rng = random.Random(seed)

    basic = _poor_officers(rng)
    lines = ["## 官员绩效", f"最近1个回合中，{basic}的决策较差。"]
    if request.complete_rounds >= 3:
        long_term = _poor_officers(rng)
        lines.append(f"最近多个回合中，{long_term}的决策较差。")
    return "\n".join(lines)


def _poor_officers(rng: random.Random) -> str:
    count = 1 if rng.random() < 0.7 else 2
    selected = rng.sample(_OFFICERS, count)
    return "、".join(selected)
