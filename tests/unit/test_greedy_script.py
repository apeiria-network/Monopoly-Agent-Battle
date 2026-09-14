"""Unit tests for the greedy scripted controller's policy rules."""

from __future__ import annotations

import random
from typing import Any

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


def _selected(request: DecisionRequest, seed: int = 1) -> tuple[DecisionOption, object]:
    validation = parse_and_validate(GreedyScriptController(random.Random(seed))(request), request)
    assert validation.valid
    assert validation.option is not None
    return validation.option, validation.target


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
            _option("redeem_mortgage", "redeem_mortgage-11", {"position": 11}),
            _option("redeem_mortgage", "redeem_mortgage-3", {"position": 3}),
        ],
        state,
    )

    option, _ = _selected(request)

    assert option.command_type == "redeem_mortgage"
    assert option.parameters["position"] == 3


def test_skips_redeem_without_safety_margin() -> None:
    state = _visible_state(cash=300)
    state["board"] = [_board_entry(3, price=200)]
    request = _request(
        DecisionKind.ASSET_MANAGEMENT,
        [
            _option("end_turn"),
            _option("redeem_mortgage", "redeem_mortgage-3", {"position": 3}),
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
            _option("sell_building", "sell_building-5", {"position": 5}),
            _option("mortgage", "mortgage-12", {"position": 12}),
            _option("mortgage", "mortgage-1", {"position": 1}),
        ],
        state,
    )

    assert _selected(request)[0].parameters["position"] == 1

    sell_only = _request(
        DecisionKind.PAYMENT_RESOLUTION,
        [
            _option("sell_building", "sell_building-5", {"position": 5}),
            _option("sell_building", "sell_building-1", {"position": 1}),
        ],
        state,
    )

    assert _selected(sell_only)[0].parameters["position"] == 1


def test_disposal_level_ties_are_reproducible() -> None:
    state = _visible_state()
    state["board"] = [_board_entry(1, building_level=1), _board_entry(9, building_level=1)]
    request = _request(
        DecisionKind.PAYMENT_RESOLUTION,
        [
            _option("sell_building", "sell_building-1", {"position": 1}),
            _option("sell_building", "sell_building-9", {"position": 9}),
        ],
        state,
    )

    first_positions = [_selected(request, seed=42)[0].parameters["position"] for _ in range(3)]
    repeated = [_selected(request, seed=42)[0].parameters["position"] for _ in range(3)]

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
            _option("select_stolen_chance_card", "steal-1", {"card_id": "chance-taxi"}),
            _option("select_stolen_chance_card", "steal-2", {"card_id": "chance-steal-card"}),
        ],
        state,
    )

    assert _selected(discard)[0].parameters["card_id"] == "chance-taxi"
    assert _selected(theft)[0].parameters["card_id"] == "chance-taxi"


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
