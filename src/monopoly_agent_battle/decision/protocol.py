"""Parse untrusted controller output and convert selected options into engine commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from typing import cast

from monopoly_agent_battle.decision.models import (
    DecisionOption,
    DecisionRequest,
    DecisionResponse,
    DecisionValidation,
)
from monopoly_agent_battle.domain.commands import (
    Build,
    DeclareBankruptcy,
    DiscardChanceCard,
    EndTurn,
    GameCommand,
    Mortgage,
    PayJailFine,
    RedeemMortgage,
    ResolveRent,
    RollDice,
    SelectStolenChanceCard,
    SellBuilding,
    UseChanceCard,
    UseCommunityGetOutOfJailCard,
)

CommandFactory = Callable[[str, dict[str, object]], GameCommand]


_COMMAND_FACTORIES: dict[str, CommandFactory] = {
    "roll_dice": lambda player_id, parameters: RollDice(player_id),
    "build": lambda player_id, parameters: Build(player_id, _integer(parameters, "position")),
    "sell_building": lambda player_id, parameters: SellBuilding(
        player_id, _integer(parameters, "position")
    ),
    "mortgage": lambda player_id, parameters: Mortgage(player_id, _integer(parameters, "position")),
    "redeem_mortgage": lambda player_id, parameters: RedeemMortgage(
        player_id, _integer(parameters, "position")
    ),
    "end_turn": lambda player_id, parameters: EndTurn(player_id),
    "declare_bankruptcy": lambda player_id, parameters: DeclareBankruptcy(player_id),
    "pay_jail_fine": lambda player_id, parameters: PayJailFine(player_id),
    "resolve_rent": lambda player_id, parameters: ResolveRent(
        player_id, _boolean(parameters, "use_waiver")
    ),
    "discard_chance_card": lambda player_id, parameters: DiscardChanceCard(
        player_id, _string(parameters, "card_id")
    ),
    "select_stolen_chance_card": lambda player_id, parameters: SelectStolenChanceCard(
        player_id, _string(parameters, "card_id")
    ),
    "use_chance_card": lambda player_id, parameters: UseChanceCard(
        player_id,
        _string(parameters, "card_id"),
        target_player_id=_optional_string(parameters, "target_player_id"),
        target_position=_optional_integer(parameters, "target_position"),
        target_color_group=_optional_string(parameters, "target_color_group"),
        secondary_target_position=_optional_integer(parameters, "secondary_target_position"),
    ),
    "use_community_get_out_of_jail_card": lambda player_id, parameters: (
        UseCommunityGetOutOfJailCard(player_id, _string(parameters, "card_id"))
    ),
}


def parse_and_validate(raw_response: str, request: DecisionRequest) -> DecisionValidation:
    """Accept exactly one JSON object containing a known option and brief reason."""
    try:
        document = json.loads(raw_response)
    except json.JSONDecodeError:
        return DecisionValidation(None, None, "response is not valid JSON", raw_response)
    if not isinstance(document, dict):
        return DecisionValidation(None, None, "response must be a JSON object", raw_response)
    document = cast(dict[str, object], document)
    if set(document) != {"selected_option", "reasoning"}:
        return DecisionValidation(
            None, None, "response must contain exactly selected_option and reasoning", raw_response
        )
    selected_option = document["selected_option"]
    reasoning = document["reasoning"]
    if not isinstance(selected_option, str) or not isinstance(reasoning, str):
        return DecisionValidation(None, None, "response fields must be strings", raw_response)
    max_characters = request.output_constraints["reasoning_max_characters"]
    if not isinstance(max_characters, int):
        raise AssertionError("decision request has an invalid reasoning limit")
    if not reasoning or len(reasoning) > max_characters:
        return DecisionValidation(None, None, "reasoning has invalid length", raw_response)
    option = next((item for item in request.options if item.option_id == selected_option), None)
    if option is None:
        return DecisionValidation(
            None, None, "selected_option is not a legal candidate", raw_response
        )
    return DecisionValidation(
        DecisionResponse(selected_option, reasoning), option, None, raw_response
    )


def command_from_option(request: DecisionRequest, option: DecisionOption) -> GameCommand:
    """Materialize a frozen candidate as one existing engine command."""
    factory = _COMMAND_FACTORIES[option.command_type]
    return factory(request.player_id, option.parameters)


def option_command_payload(request: DecisionRequest, option: DecisionOption) -> dict[str, object]:
    """Return a JSON-safe materialized command payload for audit records."""
    command = command_from_option(request, option)
    if not is_dataclass(command) or isinstance(command, type):
        raise AssertionError("decision option did not produce a dataclass command")
    return {"command_type": type(command).__name__, "command": asdict(command)}


def _integer(parameters: dict[str, object], key: str) -> int:
    value = parameters[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_integer(parameters: dict[str, object], key: str) -> int | None:
    value = parameters.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer or null")
    return value


def _string(parameters: dict[str, object], key: str) -> str:
    value = parameters[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(parameters: dict[str, object], key: str) -> str | None:
    value = parameters.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _boolean(parameters: dict[str, object], key: str) -> bool:
    value = parameters[key]
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value
