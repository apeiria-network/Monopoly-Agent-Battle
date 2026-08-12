"""Pure Level 0 game state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SpaceKind(StrEnum):
    GO = "go"
    STREET = "street"
    RAILROAD = "railroad"
    UTILITY = "utility"
    TAX = "tax"
    CHANCE = "chance"
    COMMUNITY_CHEST = "community_chest"
    JAIL = "jail"
    FREE_PARKING = "free_parking"
    GO_TO_JAIL = "go_to_jail"


class JailStatus(StrEnum):
    FREE = "free"
    WAITING = "waiting"
    ROLLING = "rolling"


class EndReason(StrEnum):
    LAST_SURVIVOR = "last_survivor"
    ROUND_LIMIT = "round_limit"


class TurnPhase(StrEnum):
    ROLLING = "rolling"
    ASSET_MANAGEMENT = "asset_management"
    PAYMENT_RESOLUTION = "payment_resolution"
    CARD_RESOLUTION = "card_resolution"
    TURN_COMPLETE = "turn_complete"


class CardDeck(StrEnum):
    CHANCE = "chance"
    COMMUNITY_CHEST = "community_chest"


class OngoingEffectKind(StrEnum):
    ALLIANCE = "alliance"
    RENT_SURGE = "rent_surge"
    RENT_FREEZE = "rent_freeze"


@dataclass(slots=True)
class OngoingEffect:
    kind: OngoingEffectKind
    source_player_id: str
    remaining_turns: int
    activation_turn: int
    target_player_id: str | None = None
    color_group: str | None = None


class SettlementOperationKind(StrEnum):
    PAYMENT = "payment"
    MOVE = "move"
    LANDING = "landing"
    CARD_DRAW = "card_draw"
    CARD_EFFECT = "card_effect"


class SettlementOperationStatus(StrEnum):
    PENDING = "pending"
    BLOCKED = "blocked"


@dataclass(slots=True)
class SettlementOperation:
    operation_id: int
    kind: SettlementOperationKind
    player_id: str
    source: str
    status: SettlementOperationStatus = SettlementOperationStatus.PENDING
    recipient_id: str | None = None
    amount: int | None = None
    steps: int | None = None
    destination: int | None = None
    dice_total: int | None = None
    collect_go_salary: bool = False
    allow_build: bool = False
    resume_phase: TurnPhase | None = None
    resume_player_id: str | None = None
    deck: CardDeck | None = None


@dataclass(frozen=True, slots=True)
class BoardSpace:
    position: int
    name: str
    kind: SpaceKind
    price: int | None = None
    color_group: str | None = None
    building_cost: int | None = None
    rents: tuple[int, ...] = ()
    tax: int | None = None

    @property
    def is_property(self) -> bool:
        return self.kind in {SpaceKind.STREET, SpaceKind.RAILROAD, SpaceKind.UTILITY}


@dataclass(slots=True)
class PropertyState:
    owner_id: str | None = None
    building_level: int = 0
    mortgaged: bool = False


@dataclass(slots=True)
class PlayerState:
    player_id: str
    seat: int
    cash: int
    position: int = 0
    properties: set[int] = field(default_factory=set[int])
    jail_status: JailStatus = JailStatus.FREE
    jail_roll_attempts: int = 0
    bankrupt: bool = False
    survived_turns: int = 0
    chance_cards: list[str] = field(default_factory=list[str])
    community_get_out_of_jail_cards: list[str] = field(default_factory=list[str])
    rent_waivers: int = 0
    pending_rent_position: int | None = None
    pending_rent_dice_total: int | None = None
    pending_rent_resume_phase: TurnPhase | None = None


@dataclass(slots=True)
class GameState:
    players: dict[str, PlayerState]
    properties: dict[int, PropertyState]
    current_player_id: str
    turn_phase: TurnPhase = TurnPhase.ROLLING
    settlement_operations: list[SettlementOperation] = field(
        default_factory=list[SettlementOperation]
    )
    next_settlement_operation_id: int = 1
    complete_rounds: int = 0
    finished: bool = False
    end_reason: EndReason | None = None
    rankings: tuple[str, ...] = ()
    chance_draw_pile: list[str] = field(default_factory=list[str])
    chance_discard_pile: list[str] = field(default_factory=list[str])
    community_chest_draw_pile: list[str] = field(default_factory=list[str])
    community_chest_discard_pile: list[str] = field(default_factory=list[str])
    ongoing_effects: list[OngoingEffect] = field(default_factory=list[OngoingEffect])
    buildable_position: int | None = None
    consecutive_doubles: int = 0
    round_player_ids: tuple[str, ...] = ()
    completed_round_player_ids: set[str] = field(default_factory=set[str])


@dataclass(frozen=True, slots=True)
class GameEvent:
    event_type: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class GameResult:
    end_reason: EndReason
    rankings: tuple[str, ...]
    complete_rounds: int
