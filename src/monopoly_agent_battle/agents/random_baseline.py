"""Fully random, deterministic controller without LLM or prompt dependencies."""

from __future__ import annotations

import json
import random

from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.protocol import option_json

_RANDOM_REASON = "从全部合法候选中随机选择。"


class RandomBaselineController:
    """Choose legal options and targets through an isolated seeded random stream."""

    uses_llm = False

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def select_option_index(self, option_count: int) -> int:
        """Return an index for one of the request's legal options."""
        return self._rng.randrange(option_count)

    def select_target_index(self, target_count: int) -> int:
        """Return an index for one selected option's legal target tuples."""
        return self._rng.randrange(target_count)

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        """Return a protocol-valid random decision without using feedback or an LLM."""
        del feedback
        option = request.options[self.select_option_index(len(request.options))]
        target_values = (
            option.target.legal_values[self.select_target_index(len(option.target.legal_values))]
            if option.target is not None
            else None
        )
        return json.dumps(
            {
                "selected_option": option_json(option, target_values),
                "reason": _RANDOM_REASON,
            },
            ensure_ascii=False,
        )
