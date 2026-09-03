"""Parse untrusted controller output and convert selected options into engine commands."""

from __future__ import annotations

import json
import re
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


_CODE_FENCE_RE = re.compile(
    r"^\s*```[^\n`]*\n(?P<body>.*?)\n?```\s*$",
    re.DOTALL,
)


def strip_code_fence(raw_response: str) -> str:
    """Unwrap a single Markdown code fence around a model reply, if present.

    Many models wrap their JSON in ```json ... ``` (or a bare ``` ... ```)
    despite the prompt asking for a raw object. The output contract still says
    "no code block", but tolerating one fence avoids a needless validation
    failure + retry. Only an outer fence that spans the whole trimmed reply is
    removed; text without a fence (or malformed fencing) is returned unchanged,
    so genuinely broken replies still fail downstream JSON parsing.

    Public so the dynasty sub-parsers (which parse raw model replies through
    their own bespoke schemas rather than ``parse_and_validate``) can share the
    exact same fence-tolerance behaviour.
    """
    match = _CODE_FENCE_RE.match(raw_response)
    return match.group("body") if match is not None else raw_response


def parse_and_validate(raw_response: str, request: DecisionRequest) -> DecisionValidation:
    """Parse an untrusted controller reply into one of the five outcomes.

    Sets ``DecisionValidation.error_category`` on failure so the feedback
    renderer can pick the right template:

    - ``not_json``          — JSON parsing or top-level structure is broken;
                              includes missing/non-string ``reason``.
    - ``missing_option``    — ``selected_option`` block or its ``option`` field
                              is absent or not a string.
    - ``invalid_option``    — ``option`` value does not match any candidate.
    - ``missing_target``    — the option requires a target but none supplied.
    - ``invalid_target``    — target was supplied but value(s) illegal.

    Extra top-level fields, extra keys inside ``selected_option`` and a
    ``target`` on an option that does not need one are all silently ignored.
    ``reason`` longer than ``_MAX_REASON_CHARS`` is truncated (not an error).
    """
    try:
        document = json.loads(strip_code_fence(raw_response))
    except json.JSONDecodeError:
        return _fail("not_json", "response is not valid JSON", raw_response)
    if not isinstance(document, dict):
        return _fail("not_json", "response must be a JSON object", raw_response)
    document = cast(dict[str, object], document)

    reason = document.get("reason")
    if not isinstance(reason, str):
        return _fail("not_json", "reason field is missing or not a string", raw_response)
    reason = reason[:_MAX_REASON_CHARS]

    selected = document.get("selected_option")
    if not isinstance(selected, dict):
        return _fail(
            "missing_option",
            "selected_option field is missing or not a JSON object",
            raw_response,
        )
    selected = cast(dict[str, object], selected)
    option_id = selected.get("option")
    if not isinstance(option_id, str):
        return _fail(
            "missing_option",
            "selected_option.option field is missing or not a string",
            raw_response,
        )

    option = next((item for item in request.options if item.option_id == option_id), None)
    if option is None:
        return _fail("invalid_option", "selected_option is not a legal candidate", raw_response)

    raw_target = selected.get("target")
    if option.target is not None and raw_target is None:
        return _fail(
            "missing_target",
            "target field is required for this option but was not provided",
            raw_response,
            option=option,
        )

    target = _validate_target(option.target, raw_target)
    if target is None:
        return _fail(
            "invalid_target",
            "target value is not legal for this option",
            raw_response,
            option=option,
        )
    return DecisionValidation(
        DecisionResponse(option_id, raw_target, reason),
        option,
        None,
        raw_response,
        target,
    )


def _fail(
    category: str,
    error: str,
    raw_response: str,
    *,
    option: DecisionOption | None = None,
) -> DecisionValidation:
    return DecisionValidation(
        response=None,
        option=option,
        error=error,
        raw_response=raw_response,
        target=None,
        error_category=category,
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


def option_json(
    option: DecisionOption, target_values: tuple[object, ...] | None = None
) -> dict[str, object]:
    """Encode one option and an optional legal target tuple for controller output."""
    selected: dict[str, object] = {"option": option.option_id}
    if option.target is not None:
        if target_values is None:
            msg = "target values are required for an option with a target"
            raise ValueError(msg)
        selected["target"] = _target_json(option.target, target_values)
    return selected


def default_option_json(option: DecisionOption) -> dict[str, object]:
    """Return the response object for an option using its first legal target, if any."""
    target_values = option.target.legal_values[0] if option.target is not None else None
    return option_json(option, target_values)


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
