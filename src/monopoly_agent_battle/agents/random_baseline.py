"""Fully random, deterministic controllers without LLM or prompt dependencies."""

from __future__ import annotations

import json
import random

from monopoly_agent_battle.decision.models import (
    DecisionKind,
    DecisionOption,
    DecisionRequest,
)
from monopoly_agent_battle.decision.protocol import option_json

_RANDOM_REASON = "从全部合法候选中随机选择。"


class RandomBaselineController:
    """Choose legal options and targets through an isolated seeded random stream."""

    uses_llm = False

    _reason = _RANDOM_REASON

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def selectable_options(self, request: DecisionRequest) -> tuple[DecisionOption, ...]:
        """Return the options this controller draws from for the given request."""
        return request.options

    def select_option_index(self, option_count: int) -> int:
        """Return an index for one of the request's legal options."""
        return self._rng.randrange(option_count)

    def select_target_index(self, target_count: int) -> int:
        """Return an index for one selected option's legal target tuples."""
        return self._rng.randrange(target_count)

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        """Return a protocol-valid random decision without using feedback or an LLM."""
        del feedback
        options = self.selectable_options(request)
        option = options[self.select_option_index(len(options))]
        target_values = (
            option.target.legal_values[self.select_target_index(len(option.target.legal_values))]
            if option.target is not None
            else None
        )
        return json.dumps(
            {
                "selected_option": option_json(option, target_values),
                "reason": self._reason,
            },
            ensure_ascii=False,
        )


class SaneRandomController(RandomBaselineController):
    """Random controller that never voluntarily sells buildings or mortgages land.

    Asset-management requests drop ``sell_building`` and ``mortgage``; redeeming
    stays available and forced disposal during payment resolution stays fully
    random (Courts-Battle-config-details.md 8.1).
    """

    _reason = "理智随机：资产管理阶段排除主动出售与抵押，其余合法候选中随机选择。"

    blocked_asset_command_types = frozenset({"sell_building", "mortgage"})

    def selectable_options(self, request: DecisionRequest) -> tuple[DecisionOption, ...]:
        """Drop voluntary disposal options from asset-management requests only."""
        if request.kind is not DecisionKind.ASSET_MANAGEMENT:
            return request.options
        kept = tuple(
            option
            for option in request.options
            if option.command_type not in self.blocked_asset_command_types
        )
        return kept or request.options
