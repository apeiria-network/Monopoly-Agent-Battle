"""Commands accepted by the deterministic game engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RollDice:
    player_id: str


@dataclass(frozen=True, slots=True)
class Build:
    player_id: str
    position: int


@dataclass(frozen=True, slots=True)
class SellBuilding:
    player_id: str
    position: int


@dataclass(frozen=True, slots=True)
class Mortgage:
    player_id: str
    position: int


@dataclass(frozen=True, slots=True)
class RedeemMortgage:
    player_id: str
    position: int


@dataclass(frozen=True, slots=True)
class EndTurn:
    player_id: str


@dataclass(frozen=True, slots=True)
class DeclareBankruptcy:
    player_id: str


@dataclass(frozen=True, slots=True)
class PayJailFine:
    player_id: str


@dataclass(frozen=True, slots=True)
class ResolveRent:
    player_id: str
    use_waiver: bool


@dataclass(frozen=True, slots=True)
class DiscardChanceCard:
    player_id: str
    card_id: str


@dataclass(frozen=True, slots=True)
class UseChanceCard:
    player_id: str
    card_id: str
    target_player_id: str | None = None
    target_position: int | None = None
    target_color_group: str | None = None
    secondary_target_position: int | None = None


@dataclass(frozen=True, slots=True)
class SelectStolenChanceCard:
    player_id: str
    card_id: str


@dataclass(frozen=True, slots=True)
class UseCommunityGetOutOfJailCard:
    player_id: str
    card_id: str


GameCommand = (
    Build
    | DeclareBankruptcy
    | DiscardChanceCard
    | EndTurn
    | Mortgage
    | PayJailFine
    | RedeemMortgage
    | ResolveRent
    | RollDice
    | SelectStolenChanceCard
    | SellBuilding
    | UseChanceCard
    | UseCommunityGetOutOfJailCard
)
