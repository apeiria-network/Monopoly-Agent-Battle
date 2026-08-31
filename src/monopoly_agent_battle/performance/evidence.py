"""Court decision evidence extraction helpers."""

from __future__ import annotations

import json
from typing import Any, cast

from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.protocol import parse_and_validate
from monopoly_agent_battle.performance.scoring import DecisionSignature


def signature_from_reply(request: DecisionRequest, reply: str) -> DecisionSignature | None:
    """Return the protocol-normalized option and complete target for one reply."""
    validation = parse_and_validate(reply, request)
    if not validation.valid or validation.option is None:
        return None
    return DecisionSignature.from_parts(validation.option.option_id, validation.target or {})


def selected_option(reply: str) -> dict[str, Any] | None:
    """Read a trusted workflow-specific selected_option object."""
    document_value = json.loads(reply)
    if not isinstance(document_value, dict):
        return None
    document = cast(dict[str, Any], document_value)
    selected_value = document.get("selected_option")
    if not isinstance(selected_value, dict):
        return None
    return cast(dict[str, Any], selected_value)
