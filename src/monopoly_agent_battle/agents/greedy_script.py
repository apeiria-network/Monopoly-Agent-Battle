"""Deterministic greedy scripted controller without LLM or prompt dependencies."""

from __future__ import annotations

import json
import random
from typing import Any, cast

from monopoly_agent_battle.decision.models import (
    DecisionKind,
    DecisionOption,
    DecisionRequest,
    OptionTarget,
)
from monopoly_agent_battle.decision.protocol import option_json
from monopoly_agent_battle.game.cards.classic_cards import CARDS_BY_ID, CardEffect

_REASON = "贪心脚本：按固定策略规则选择。"
_REDEEM_SAFETY_MARGIN = 200
_NON_STREET_LEVEL = 1
_BOARD_SIZE = 40


class GreedyScriptController:
    """Greedy expansion policy for the zero-cost scripted benchmark games.

    Rules (Courts-Battle-config-details.md 8.2, plus aligned decisions): buying,
    building and rent waivers are engine-automatic; play the first playable
    chance card choosing the clockwise-nearest legal target (taxi always moves
    the maximum distance); when no card is playable, redeem the lowest-position
    mortgaged property while cash covers the redeem cost plus a safety margin;
    in jail prefer the get-out-of-jail card, then the fine, then rolling; during
    forced disposal mortgage before selling, always at the lowest building
    level (railroads/utilities count as level 1) with seeded-random tie-breaks.

    The engine folds every legal target of a command (which property to
    mortgage/redeem/sell, which stolen card to take) into a single option whose
    ``option.target.legal_values`` lists the candidate tuples; ``option.parameters``
    never carries those target fields.  This controller therefore selects a
    target tuple from ``option.target.legal_values`` and returns it alongside the
    option so ``option_json`` can encode it.
    """

    uses_llm = False

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def __call__(self, request: DecisionRequest, feedback: str | None = None) -> str:
        """Return a protocol-valid scripted decision without using feedback or an LLM."""
        del feedback
        option, target_values = self._choose(request)
        return json.dumps(
            {
                "selected_option": option_json(option, target_values),
                "reason": _REASON,
            },
            ensure_ascii=False,
        )

    def _choose(self, request: DecisionRequest) -> tuple[DecisionOption, tuple[object, ...] | None]:
        if request.kind is DecisionKind.JAIL:
            option = self._choose_jail(request)
            return option, self._first_target(option)
        if request.kind is DecisionKind.PAYMENT_RESOLUTION:
            return self._choose_disposal(request)
        if request.kind is DecisionKind.ASSET_MANAGEMENT:
            return self._choose_asset_management(request)
        # FORCED_DISCARD, THEFT_CARD_SELECTION and any other kind: pick the first
        # candidate (option[0] with its first legal target), matching 8.2.
        return self._first_candidate(request)

    def _first_candidate(
        self, request: DecisionRequest
    ) -> tuple[DecisionOption, tuple[object, ...] | None]:
        option = request.options[0]
        return option, self._first_target(option)

    @staticmethod
    def _first_target(option: DecisionOption) -> tuple[object, ...] | None:
        """Return the first legal target tuple, or None when the option has no target."""
        if option.target is None:
            return None
        return option.target.legal_values[0]

    def _choose_jail(self, request: DecisionRequest) -> DecisionOption:
        for command_type in ("use_community_get_out_of_jail_card", "pay_jail_fine"):
            for option in request.options:
                if option.command_type == command_type:
                    return option
        return request.options[0]

    def _choose_asset_management(
        self, request: DecisionRequest
    ) -> tuple[DecisionOption, tuple[object, ...] | None]:
        card_option = next(
            (option for option in request.options if option.command_type == "use_chance_card"),
            None,
        )
        if card_option is not None:
            return card_option, self._card_target(request, card_option)
        redeem = self._choose_redeem(request)
        if redeem is not None:
            return redeem
        return self._default_option(request)

    def _card_target(
        self, request: DecisionRequest, option: DecisionOption
    ) -> tuple[object, ...] | None:
        target = option.target
        if target is None:
            return None
        rows = list(target.legal_values)
        distances = [self._row_distance(request, target.fields, row) for row in rows]
        card_id = cast(str, option.parameters["card_id"])
        if CARDS_BY_ID[card_id].effect is CardEffect.TAXI_MOVE:
            return rows[distances.index(max(distances))]
        best = min(distances)
        tied = [row for row, distance in zip(rows, distances, strict=True) if distance == best]
        return tied[0] if len(tied) == 1 else self._rng.choice(tied)

    def _row_distance(
        self,
        request: DecisionRequest,
        fields: tuple[str, ...],
        row: tuple[object, ...],
    ) -> tuple[int, ...]:
        state = request.visible_state
        my_position = self._own_position(state)
        parts: list[int] = []
        for field, value in zip(fields, row, strict=True):
            if field == "target_player_id":
                position = self._player_position(state, cast(str, value))
                parts.append(self._clockwise_distance(my_position, position))
            elif "position" in field:
                parts.append(self._clockwise_distance(my_position, cast(int, value)))
            elif field == "target_color_group":
                parts.append(
                    min(
                        self._clockwise_distance(my_position, member)
                        for member in self._color_group_positions(state, cast(str, value))
                    )
                )
        return tuple(parts)

    def _choose_redeem(
        self, request: DecisionRequest
    ) -> tuple[DecisionOption, tuple[object, ...] | None] | None:
        option = next((o for o in request.options if o.command_type == "redeem_mortgage"), None)
        if option is None or option.target is None:
            return None
        cash = self._own_cash(request.visible_state)
        redeemable: list[tuple[int, tuple[object, ...]]] = []
        for row in option.target.legal_values:
            position = cast(int, row[0])
            price = cast(int, self._board_entry(request.visible_state, position)["price"] or 0)
            if cash >= _redeem_cost(price) + _REDEEM_SAFETY_MARGIN:
                redeemable.append((position, row))
        if not redeemable:
            return None
        redeemable.sort(key=lambda item: item[0])
        return option, redeemable[0][1]

    def _choose_disposal(
        self, request: DecisionRequest
    ) -> tuple[DecisionOption, tuple[object, ...] | None]:
        for command_type in ("mortgage", "sell_building"):
            option = next((o for o in request.options if o.command_type == command_type), None)
            if option is None or option.target is None:
                continue
            position = self._pick_lowest_level_position(request, option.target)
            return option, (position,)
        return self._default_option(request)

    def _pick_lowest_level_position(self, request: DecisionRequest, target: OptionTarget) -> int:
        scored = [
            (self._virtual_level(request, cast(int, row[0])), cast(int, row[0]))
            for row in target.legal_values
        ]
        lowest = min(level for level, _ in scored)
        tied = [position for level, position in scored if level == lowest]
        return tied[0] if len(tied) == 1 else self._rng.choice(tied)

    def _virtual_level(self, request: DecisionRequest, position: int) -> int:
        entry = self._board_entry(request.visible_state, position)
        if entry["kind"] != "street":
            return _NON_STREET_LEVEL
        return cast(int, entry["building_level"] or 0)

    def _default_option(
        self, request: DecisionRequest
    ) -> tuple[DecisionOption, tuple[object, ...] | None]:
        option = next(
            (option for option in request.options if option.is_default),
            request.options[0],
        )
        return option, self._first_target(option)

    def _own_position(self, state: dict[str, object]) -> int:
        return cast(int, cast(dict[str, object], state["your_state"])["position"])

    def _own_cash(self, state: dict[str, object]) -> int:
        return cast(int, cast(dict[str, object], state["your_state"])["cash"])

    def _player_position(self, state: dict[str, object], player_id: str) -> int:
        players = cast(list[dict[str, object]], state["players"])
        return cast(
            int,
            next(player["position"] for player in players if player["player_id"] == player_id),
        )

    def _color_group_positions(self, state: dict[str, object], color_group: str) -> list[int]:
        board = cast(list[dict[str, object]], state["board"])
        return [
            cast(int, entry["position"]) for entry in board if entry["color_group"] == color_group
        ]

    def _board_entry(self, state: dict[str, object], position: int) -> dict[str, Any]:
        board = cast(list[dict[str, object]], state["board"])
        return cast(
            dict[str, Any],
            next(entry for entry in board if entry["position"] == position),
        )

    @staticmethod
    def _clockwise_distance(origin: int, destination: int) -> int:
        return (destination - origin) % _BOARD_SIZE


def _redeem_cost(price: int) -> int:
    """Mirror GameEngine._redeem: half-up rounding of 55% of the purchase price."""
    return (price * 55 + 50) // 100
