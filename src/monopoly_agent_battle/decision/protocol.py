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
    OptionTarget,
)
from monopoly_agent_battle.domain.commands import (
    DiscardChanceCard,
    EndTurn,
    GameCommand,
    Mortgage,
    PayJailFine,
    RedeemMortgage,
    RollDice,
    SelectStolenChanceCard,
    SellBuilding,
    UseChanceCard,
    UseCommunityGetOutOfJailCard,
)

CommandFactory = Callable[[str, dict[str, object]], GameCommand]

_MAX_REASON_CHARS = 400


_COMMAND_FACTORIES: dict[str, CommandFactory] = {
    "roll_dice": lambda player_id, parameters: RollDice(player_id),
    "sell_building": lambda player_id, parameters: SellBuilding(
        player_id, _integer(parameters, "position")
    ),
    "mortgage": lambda player_id, parameters: Mortgage(player_id, _integer(parameters, "position")),
    "redeem_mortgage": lambda player_id, parameters: RedeemMortgage(
        player_id, _integer(parameters, "position")
    ),
    "end_turn": lambda player_id, parameters: EndTurn(player_id),
    "pay_jail_fine": lambda player_id, parameters: PayJailFine(player_id),
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
    """Accept exactly one JSON object containing a known option, its target, and a reason."""
    try:
        document = json.loads(raw_response)
    except json.JSONDecodeError:
        return DecisionValidation(None, None, "response is not valid JSON", raw_response)
    if not isinstance(document, dict):
        return DecisionValidation(None, None, "response must be a JSON object", raw_response)
    document = cast(dict[str, object], document)
    if set(document) != {"selected_option", "reason"}:
        return DecisionValidation(
            None, None, "response must contain exactly selected_option and reason", raw_response
        )
    selected = document["selected_option"]
    reason = document["reason"]
    if not isinstance(selected, dict) or not isinstance(reason, str):
        return DecisionValidation(
            None,
            None,
            "selected_option must be an object and reason must be a string",
            raw_response,
        )
    selected = cast(dict[str, object], selected)
    if "option" not in selected or not set(selected) <= {"option", "target"}:
        return DecisionValidation(
            None, None, "selected_option must contain option and an optional target", raw_response
        )
    option_id = selected["option"]
    if not isinstance(option_id, str):
        return DecisionValidation(
            None, None, "selected_option.option must be a string", raw_response
        )
    reason = reason[:_MAX_REASON_CHARS]
    option = next((item for item in request.options if item.option_id == option_id), None)
    if option is None:
        return DecisionValidation(
            None, None, "selected_option is not a legal candidate", raw_response
        )
    raw_target = selected.get("target")
    target = _validate_target(option.target, raw_target)
    if target is None:
        return DecisionValidation(None, None, "target is not a legal value", raw_response)
    return DecisionValidation(
        DecisionResponse(option_id, raw_target, reason), option, None, raw_response, target
    )


def command_from_option(
    request: DecisionRequest, option: DecisionOption, target: dict[str, object] | None = None
) -> GameCommand:
    """Materialize a frozen candidate and its selected target as one engine command."""
    factory = _COMMAND_FACTORIES[option.command_type]
    parameters = {**option.parameters, **(target or {})}
    return factory(request.player_id, parameters)


def option_command_payload(
    request: DecisionRequest, option: DecisionOption, target: dict[str, object] | None = None
) -> dict[str, object]:
    """Return a JSON-safe materialized command payload for audit records."""
    command = command_from_option(request, option, target)
    if not is_dataclass(command) or isinstance(command, type):
        raise AssertionError("decision option did not produce a dataclass command")
    return {"command_type": type(command).__name__, "command": asdict(command)}


def default_option_json(option: DecisionOption) -> dict[str, object]:
    """Return the response object for an option using its first legal target, if any."""
    selected: dict[str, object] = {"option": option.option_id}
    if option.target is not None and option.target.legal_values:
        selected["target"] = _target_json(option.target, option.target.legal_values[0])
    return selected


def _validate_target(
    target_spec: OptionTarget | None, raw_target: object | None
) -> dict[str, object] | None:
    if target_spec is None:
        return {}
    if len(target_spec.fields) == 1:
        if (raw_target,) not in target_spec.legal_values:
            return None
        return {target_spec.command_fields[0]: raw_target}
    if not isinstance(raw_target, dict):
        return None
    raw_target = cast(dict[str, object], raw_target)
    values = tuple(raw_target.get(field) for field in target_spec.fields)
    if values not in target_spec.legal_values:
        return None
    return dict(zip(target_spec.command_fields, values, strict=True))


def _target_json(target_spec: OptionTarget, values: tuple[object, ...]) -> object:
    if len(target_spec.fields) == 1:
        return values[0]
    return dict(zip(target_spec.fields, values, strict=True))


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
