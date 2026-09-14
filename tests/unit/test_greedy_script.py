"""Unit tests for the greedy scripted controller's policy rules."""

from __future__ import annotations

import random
from typing import Any, cast

from monopoly_agent_battle.agents.greedy_script import GreedyScriptController
from monopoly_agent_battle.decision.models import (
    DecisionKind,
    DecisionOption,
    DecisionRequest,
    OptionTarget,
)
from monopoly_agent_battle.decision.protocol import parse_and_validate
from monopoly_agent_battle.game.cards.classic_cards import CARDS_BY_ID


def _option(
    command_type: str,
    option_id: str | None = None,
    parameters: dict[str, object] | None = None,
    target_rows: list[tuple[object, ...]] | None = None,
    target_fields: tuple[str, ...] = ("target_player_id",),
) -> DecisionOption:
    """Build an option mirroring the engine's folded-option structure.

    When ``target_rows`` is given the option carries an ``OptionTarget`` whose
    ``legal_values`` list the candidate tuples and whose ``fields`` match the
    production command target (e.g. ``position`` for mortgage/redeem/sell,
    ``card_id`` for theft selection).  ``parameters`` never carries those
    target fields, exactly like ``decision/requests._split_command``.
    """
    return DecisionOption(
        option_id=option_id or command_type,
        command_type=command_type,
        parameters=parameters or {},
        title=command_type,
        preview="",
        response_format={},
        is_default=command_type == "end_turn",
        target=(
            None
            if target_rows is None
            else OptionTarget(
                kind="test",
                fields=target_fields,
                command_fields=target_fields,
                legal_values=tuple(target_rows),
            )
        ),
    )


def _request(
    kind: DecisionKind,
    options: list[DecisionOption],
    visible_state: dict[str, object],
) -> DecisionRequest:
    return DecisionRequest(
        decision_id="d1",
        game_id="greedy-unit",
        complete_rounds=1,
        player_id="a",
        phase="test",
        kind=kind,
        question="q",
        visible_state=visible_state,
        options=tuple(options),
        output_constraints={},
    )


def _visible_state(
    position: int = 0, cash: int = 1500, players: dict[str, int] | None = None
) -> dict[str, object]:
    return {
        "your_state": {"position": position, "cash": cash},
        "players": [
            {"player_id": player_id, "position": player_position}
            for player_id, player_position in (players or {"b": 5}).items()
        ],
        "board": [],
    }


def _board_entry(
    position: int, kind: str = "street", building_level: int | None = 0, price: int = 200
) -> dict[str, object]:
    return {
        "position": position,
        "kind": kind,
        "building_level": building_level,
        "price": price,
        "color_group": "brown",
    }


def _selected(request: DecisionRequest, seed: int = 1) -> tuple[DecisionOption, dict[str, object]]:
    validation = parse_and_validate(GreedyScriptController(random.Random(seed))(request), request)
    assert validation.valid
    assert validation.option is not None
    return validation.option, cast(dict[str, object], validation.target)


def test_jail_prefers_card_then_fine_then_roll() -> None:
    state = _visible_state()
    full = _request(
        DecisionKind.JAIL,
        [
            _option("roll_dice"),
            _option("pay_jail_fine"),
            _option("use_community_get_out_of_jail_card", "jail-card-1"),
        ],
        state,
    )
    assert _selected(full)[0].command_type == "use_community_get_out_of_jail_card"

    no_card = _request(DecisionKind.JAIL, [_option("roll_dice"), _option("pay_jail_fine")], state)
    assert _selected(no_card)[0].command_type == "pay_jail_fine"

    roll_only = _request(DecisionKind.JAIL, [_option("roll_dice")], state)
    assert _selected(roll_only)[0].command_type == "roll_dice"


def test_card_targets_nearest_player() -> None:
    state = _visible_state(position=10, players={"b": 13, "c": 37})
    request = _request(
        DecisionKind.ASSET_MANAGEMENT,
        [
            _option("end_turn"),
            _option(
                "use_chance_card",
                "use_chance_card-chance-tax",
                {"card_id": "chance-tax"},
                target_rows=[("b",), ("c",)],
            ),
        ],
        state,
    )

    option, target = _selected(request)

    assert option.command_type == "use_chance_card"
    assert target == {"target_player_id": "b"}


def test_taxi_moves_maximum_distance() -> None:
    state = _visible_state(position=10)
    rows: list[tuple[object, ...]] = [(10 + step,) for step in range(1, 7)]
    request = _request(
        DecisionKind.ASSET_MANAGEMENT,
        [
            _option("end_turn"),
            _option(
                "use_chance_card",
                "use_chance_card-chance-taxi",
                {"card_id": "chance-taxi"},
                target_rows=rows,
                target_fields=("target_position",),
            ),
        ],
        state,
    )

    _, target = _selected(request)

    assert CARDS_BY_ID["chance-taxi"].effect.value == "taxi_move"
    assert target == {"target_position": 16}


def test_redeems_lowest_position_when_cash_allows() -> None:
    state = _visible_state(cash=1000)
    state["board"] = [_board_entry(3, price=200), _board_entry(11, price=160)]
    request = _request(
        DecisionKind.ASSET_MANAGEMENT,
        [
            _option("end_turn"),
            _option(
                "redeem_mortgage",
                target_rows=[(11,), (3,)],
                target_fields=("position",),
            ),
        ],
        state,
    )

    option, target = _selected(request)

    assert option.command_type == "redeem_mortgage"
    assert target == {"position": 3}


def test_skips_redeem_without_safety_margin() -> None:
    state = _visible_state(cash=300)
    state["board"] = [_board_entry(3, price=200)]
    request = _request(
        DecisionKind.ASSET_MANAGEMENT,
        [
            _option("end_turn"),
            _option(
                "redeem_mortgage",
                target_rows=[(3,)],
                target_fields=("position",),
            ),
        ],
        state,
    )

    assert _selected(request)[0].command_type == "end_turn"


def test_disposal_mortgages_lowest_level_before_selling() -> None:
    state = _visible_state()
    state["board"] = [
        _board_entry(1, building_level=0),
        _board_entry(5, building_level=2),
        _board_entry(12, kind="utility", building_level=None),
    ]
    request = _request(
        DecisionKind.PAYMENT_RESOLUTION,
        [
            _option("sell_building", target_rows=[(5,)], target_fields=("position",)),
            _option(
                "mortgage",
                target_rows=[(12,), (1,)],
                target_fields=("position",),
            ),
        ],
        state,
    )

    option, target = _selected(request)

    assert option.command_type == "mortgage"
    assert target == {"position": 1}

    sell_only = _request(
        DecisionKind.PAYMENT_RESOLUTION,
        [
            _option(
                "sell_building",
                target_rows=[(5,), (1,)],
                target_fields=("position",),
            ),
        ],
        state,
    )

    option, target = _selected(sell_only)

    assert option.command_type == "sell_building"
    assert target == {"position": 1}


def test_disposal_level_ties_are_reproducible() -> None:
    state = _visible_state()
    state["board"] = [_board_entry(1, building_level=1), _board_entry(9, building_level=1)]
    request = _request(
        DecisionKind.PAYMENT_RESOLUTION,
        [
            _option(
                "sell_building",
                target_rows=[(1,), (9,)],
                target_fields=("position",),
            ),
        ],
        state,
    )

    first_positions = [_selected(request, seed=42)[1]["position"] for _ in range(3)]
    repeated = [_selected(request, seed=42)[1]["position"] for _ in range(3)]

    assert all(position in (1, 9) for position in first_positions)
    assert first_positions == repeated


def test_forced_discard_and_theft_pick_first_candidate() -> None:
    state = _visible_state()
    discard = _request(
        DecisionKind.FORCED_DISCARD,
        [
            _option("discard_chance_card", "discard-1", {"card_id": "chance-taxi"}),
            _option("discard_chance_card", "discard-2", {"card_id": "chance-steal-card"}),
        ],
        state,
    )
    theft = _request(
        DecisionKind.THEFT_CARD_SELECTION,
        [
            _option(
                "select_stolen_chance_card",
                target_rows=[("chance-taxi",), ("chance-steal-card",)],
                target_fields=("card_id",),
            ),
        ],
        state,
    )

    discard_option, discard_target = _selected(discard)
    assert discard_option.command_type == "discard_chance_card"
    assert discard_option.parameters["card_id"] == "chance-taxi"
    assert discard_target == {}

    theft_option, theft_target = _selected(theft)
    assert theft_option.command_type == "select_stolen_chance_card"
    assert theft_target == {"card_id": "chance-taxi"}


def test_greedy_script_is_reproducible_and_non_llm() -> None:
    state: dict[str, Any] = _visible_state(position=10, players={"b": 13, "c": 37})
    request = _request(
        DecisionKind.ASSET_MANAGEMENT,
        [
            _option("end_turn"),
            _option(
                "use_chance_card",
                "use_chance_card-chance-tax",
                {"card_id": "chance-tax"},
                target_rows=[("b",), ("c",)],
            ),
        ],
        state,
    )
    first = GreedyScriptController(random.Random(81))
    second = GreedyScriptController(random.Random(81))

    assert [first(request) for _ in range(8)] == [second(request) for _ in range(8)]
    assert GreedyScriptController(random.Random(1)).uses_llm is False
