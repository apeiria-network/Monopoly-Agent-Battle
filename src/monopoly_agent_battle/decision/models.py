"""Structured decision data passed between controllers and the game engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class DecisionKind(StrEnum):
    JAIL = "jail"
    ASSET_MANAGEMENT = "asset_management"
    PAYMENT_RESOLUTION = "payment_resolution"
    RENT_WAIVER = "rent_waiver"


@dataclass(frozen=True, slots=True)
class DecisionOption:
    """One fully specified, engine-legal command candidate."""

    option_id: str
    command_type: str
    parameters: dict[str, object]
    summary: str
    effect_preview: dict[str, object]
    is_default: bool = False


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    """A player-scoped choice with only legal options and visible state."""

    decision_id: str
    game_id: str
    complete_rounds: int
    player_id: str
    phase: str
    kind: DecisionKind
    question: str
    visible_state: dict[str, object]
    options: tuple[DecisionOption, ...]
    output_constraints: dict[str, object]


@dataclass(frozen=True, slots=True)
class DecisionResponse:
    """Untrusted structured response submitted by a controller."""

    selected_option: str
    reasoning: str


@dataclass(frozen=True, slots=True)
class DecisionValidation:
    """The validation outcome before a command is sent to the engine."""

    response: DecisionResponse | None
    option: DecisionOption | None
    error: str | None
    raw_response: str

    @property
    def valid(self) -> bool:
        return self.response is not None and self.option is not None and self.error is None


def decision_request_record(request: DecisionRequest) -> dict[str, object]:
    """Return a JSON-safe audit representation without hidden runtime state."""
    return {
        "decision_id": request.decision_id,
        "game_id": request.game_id,
        "complete_rounds": request.complete_rounds,
        "player_id": request.player_id,
        "phase": request.phase,
        "kind": request.kind.value,
        "question": request.question,
        "visible_state": request.visible_state,
        "options": [
            {
                "option_id": option.option_id,
                "command_type": option.command_type,
                "parameters": option.parameters,
                "summary": option.summary,
                "effect_preview": option.effect_preview,
                "is_default": option.is_default,
            }
            for option in request.options
        ],
        "output_constraints": request.output_constraints,
    }


def validation_record(validation: DecisionValidation) -> dict[str, Any]:
    """Return a JSON-safe validation representation for decision records."""
    return {
        "raw_response": validation.raw_response,
        "parsed_response": (
            {
                "selected_option": validation.response.selected_option,
                "reasoning": validation.response.reasoning,
            }
            if validation.response is not None
            else None
        ),
        "validation_error": validation.error,
        "selected_option": validation.option.option_id if validation.option is not None else None,
    }
