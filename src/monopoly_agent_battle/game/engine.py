"""Seeded game engine for the non-card Level 0 vertical slice."""

from __future__ import annotations

import random

from monopoly_agent_battle.config.models import GameConfig
from monopoly_agent_battle.domain.commands import (
    Build,
    DeclareBankruptcy,
    DiscardChanceCard,
    EndTurn,
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
from monopoly_agent_battle.domain.models import (
    CardDeck,
    EndReason,
    GameEvent,
    GameResult,
    GameState,
    JailStatus,
    OngoingEffect,
    OngoingEffectKind,
    PlayerState,
    PropertyState,
    SettlementOperation,
    SettlementOperationKind,
    SettlementOperationStatus,
    SpaceKind,
    TurnPhase,
)
from monopoly_agent_battle.game.board_data.classic_us_40 import (
    BOARD,
    BOARD_BY_POSITION,
    COLOR_GROUPS,
)
from monopoly_agent_battle.game.cards.classic_cards import (
    CARDS_BY_ID,
    CHANCE_CARDS,
    COMMUNITY_CHEST_CARDS,
    CardEffect,
)
from monopoly_agent_battle.game.rules.classic_level0 import net_worth, rent_due


class GameRuleError(ValueError):
    """Raised for an illegal engine command."""


def _round_ratio_half_up(numerator: int, denominator: int) -> int:
    """Round a non-negative rational amount using C-028 half-up semantics."""
    if numerator < 0 or denominator <= 0:
        raise ValueError("rounding requires a non-negative numerator and positive denominator")
    return (numerator * 2 + denominator) // (denominator * 2)


class GameEngine:
    """Own the only mutable state for a deterministic classic-board game."""

    def __init__(self, config: GameConfig) -> None:
        players = {
            item.player_id: PlayerState(item.player_id, item.seat, config.initial_cash)
            for item in config.players
        }
        first_player = min(players.values(), key=lambda player: player.seat)
        self.config = config
        self.random = random.Random(config.seed)
        chance_draw_pile = [card.card_id for card in CHANCE_CARDS]
        community_chest_draw_pile = [card.card_id for card in COMMUNITY_CHEST_CARDS]
        self.random.shuffle(chance_draw_pile)
        self.random.shuffle(community_chest_draw_pile)
        if config.initial_chance_cards > 0:
            seated_players = sorted(players.values(), key=lambda player: player.seat)
            total_deal = config.initial_chance_cards * len(seated_players)
            if total_deal > len(chance_draw_pile):
                raise ValueError("initial chance card deal exceeds the draw pile size")
            for _ in range(config.initial_chance_cards):
                for player in seated_players:
                    player.chance_cards.append(chance_draw_pile.pop())
        self.state = GameState(
            players=players,
            properties={space.position: PropertyState() for space in BOARD if space.is_property},
            current_player_id=first_player.player_id,
            chance_draw_pile=chance_draw_pile,
            community_chest_draw_pile=community_chest_draw_pile,
            round_player_ids=tuple(
                player.player_id for player in sorted(players.values(), key=lambda item: item.seat)
            ),
        )

    def execute(
        self,
        command: RollDice
        | Build
        | SellBuilding
        | Mortgage
        | RedeemMortgage
        | PayJailFine
        | ResolveRent
        | EndTurn
        | DeclareBankruptcy
        | DiscardChanceCard
        | SelectStolenChanceCard
        | UseChanceCard
        | UseCommunityGetOutOfJailCard,
    ) -> list[GameEvent]:
        if self.state.finished:
            raise GameRuleError("game is already finished")
        blocked_payment = self._blocked_payment()
        if command.player_id != self.state.current_player_id and (
            self.state.turn_phase is not TurnPhase.PAYMENT_RESOLUTION
            or blocked_payment is None
            or command.player_id != blocked_payment.player_id
        ):
            raise GameRuleError("command must be issued by the current player")
        player = self.state.players[command.player_id]
        if player.bankrupt:
            raise GameRuleError("bankrupt player cannot act")
        if isinstance(command, RollDice):
            if self.state.turn_phase is not TurnPhase.ROLLING:
                raise GameRuleError("dice can only be rolled during the rolling phase")
            events = self._roll(player)
        elif isinstance(command, EndTurn):
            if self.state.turn_phase not in {TurnPhase.ASSET_MANAGEMENT, TurnPhase.TURN_COMPLETE}:
                raise GameRuleError("turn cannot end during the current phase")
            if len(player.chance_cards) > 3:
                self.state.turn_phase = TurnPhase.FORCED_DISCARD
                events = [
                    GameEvent(
                        "chance_card_discard_required",
                        {"player_id": player.player_id, "card_count": len(player.chance_cards)},
                    )
                ]
            else:
                events = self.advance_turn()
        elif isinstance(command, DeclareBankruptcy):
            raise GameRuleError("bankruptcy resolves automatically when liquidation is exhausted")
        elif isinstance(command, Build):
            if self.state.turn_phase is not TurnPhase.ASSET_MANAGEMENT:
                raise GameRuleError("building requires the asset management phase")
            events = self._build(player, command.position)
        elif isinstance(command, SellBuilding):
            if self.state.turn_phase not in {
                TurnPhase.ASSET_MANAGEMENT,
                TurnPhase.PAYMENT_RESOLUTION,
            }:
                raise GameRuleError("building sale is unavailable during the current phase")
            events = self._sell_building(player, command.position)
        elif isinstance(command, Mortgage):
            if self.state.turn_phase not in {
                TurnPhase.ASSET_MANAGEMENT,
                TurnPhase.PAYMENT_RESOLUTION,
            }:
                raise GameRuleError("mortgage is unavailable during the current phase")
            events = self._mortgage(player, command.position)
        elif isinstance(command, RedeemMortgage):
            if self.state.turn_phase is not TurnPhase.ASSET_MANAGEMENT:
                raise GameRuleError("mortgage redemption requires the asset management phase")
            events = self._redeem(player, command.position)
        elif isinstance(command, ResolveRent):
            raise GameRuleError("rent waivers resolve automatically")
        elif isinstance(command, DiscardChanceCard):
            if self.state.turn_phase is not TurnPhase.FORCED_DISCARD:
                raise GameRuleError(
                    "chance cards can only be discarded after an over-limit end turn"
                )
            events = self._discard_held_chance_card(player, command.card_id)
            if len(player.chance_cards) <= 3:
                self.state.turn_phase = TurnPhase.TURN_COMPLETE
                events.append(
                    GameEvent("chance_card_hand_limit_resolved", {"player_id": player.player_id})
                )
        elif isinstance(command, SelectStolenChanceCard):
            if self.state.turn_phase is not TurnPhase.THEFT_CARD_SELECTION:
                raise GameRuleError("stolen card selection is unavailable during the current phase")
            events = self._select_stolen_chance_card(player, command.card_id)
        elif isinstance(command, UseChanceCard):
            if self.state.turn_phase is not TurnPhase.ASSET_MANAGEMENT:
                raise GameRuleError("chance cards require the asset management phase")
            events = self._use_chance_card(player, command)
        elif isinstance(command, UseCommunityGetOutOfJailCard):
            if self.state.turn_phase is not TurnPhase.ROLLING:
                raise GameRuleError("get-out-of-jail cards require the rolling phase")
            events = self._use_community_get_out_of_jail_card(player, command.card_id)
        else:
            if self.state.turn_phase is not TurnPhase.ROLLING:
                raise GameRuleError("jail fine requires the rolling phase")
            events = self._pay_jail_fine(player)
        self._validate_invariants()
        return events

    def advance_turn(self) -> list[GameEvent]:
        """Complete the current turn and select the next living player."""
        current = self.state.players[self.state.current_player_id]
        self.state.consecutive_doubles = 0
        current.survived_turns += 1
        events = self._advance_ongoing_effects(current)
        self.state.completed_round_player_ids.add(current.player_id)
        events.append(GameEvent("turn_ended", {"player_id": current.player_id}))
        living = self._living_players()
        if len(living) == 1:
            self._finish(EndReason.LAST_SURVIVOR)
            return events + [GameEvent("game_finished", {"reason": EndReason.LAST_SURVIVOR.value})]
        if set(self.state.round_player_ids) <= self.state.completed_round_player_ids:
            self.state.complete_rounds += 1
            if self.state.complete_rounds >= self.config.max_complete_rounds:
                self._finish(EndReason.ROUND_LIMIT)
                return events + [
                    GameEvent("game_finished", {"reason": EndReason.ROUND_LIMIT.value})
                ]
            self.state.round_player_ids = tuple(
                player.player_id for player in sorted(living, key=lambda item: item.seat)
            )
            self.state.completed_round_player_ids.clear()
        next_player = self._next_living_player(current)
        self.state.current_player_id = next_player.player_id
        self.state.turn_phase = TurnPhase.ROLLING
        return events + [GameEvent("turn_started", {"player_id": next_player.player_id})]

    def result(self) -> GameResult:
        if not self.state.finished or self.state.end_reason is None:
            raise GameRuleError("game has not finished")
        return GameResult(self.state.end_reason, self.state.rankings, self.state.complete_rounds)

    def _roll(self, player: PlayerState) -> list[GameEvent]:
        if player.jail_status is JailStatus.WAITING:
            player.jail_status = JailStatus.ROLLING
            self.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
            return [GameEvent("jail_wait_completed", {"player_id": player.player_id})]
        first, second = self.random.randint(1, 6), self.random.randint(1, 6)
        total = first + second
        events = [
            GameEvent("dice_rolled", {"player_id": player.player_id, "dice": (first, second)})
        ]
        if first == second:
            self.state.consecutive_doubles += 1
            if self.state.consecutive_doubles == 3:
                player.position = 10
                player.jail_status = JailStatus.WAITING
                player.jail_roll_attempts = 0
                self.state.consecutive_doubles = 0
                self.state.turn_phase = TurnPhase.TURN_COMPLETE
                return events + [
                    GameEvent(
                        "player_jailed", {"player_id": player.player_id, "reason": "third_doubles"}
                    )
                ]
        else:
            self.state.consecutive_doubles = 0
        released_from_jail = False
        if player.jail_status is JailStatus.ROLLING:
            player.jail_roll_attempts += 1
            if first == second:
                player.jail_status = JailStatus.FREE
                player.jail_roll_attempts = 0
                released_from_jail = True
                events.append(
                    GameEvent("jail_released", {"player_id": player.player_id, "method": "doubles"})
                )
            elif player.jail_roll_attempts < 3:
                self.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
                return events + [GameEvent("jail_roll_failed", {"player_id": player.player_id})]
            else:
                self._pay(player, 50, None, "jail_fine", events, pending_movement_steps=total)
                return events
        resume_phase = (
            TurnPhase.ROLLING
            if first == second and not released_from_jail
            else TurnPhase.ASSET_MANAGEMENT
        )
        events.extend(self._move_and_resolve(player, total, resume_phase=resume_phase))
        if self.state.turn_phase is TurnPhase.ROLLING:
            self.state.turn_phase = resume_phase
        return events

    def _move_and_resolve(
        self,
        player: PlayerState,
        steps: int,
        *,
        resume_phase: TurnPhase = TurnPhase.ASSET_MANAGEMENT,
    ) -> list[GameEvent]:
        start = player.position
        player.position = (start + steps) % 40
        events = [
            GameEvent(
                "player_moved",
                {
                    "player_id": player.player_id,
                    "from": start,
                    "to": player.position,
                    "steps": steps,
                },
            )
        ]
        if start + steps >= 40:
            player.cash += 200
            events.append(
                GameEvent("go_salary_collected", {"player_id": player.player_id, "amount": 200})
            )
        return events + self._resolve_space(
            player, steps, allow_build=True, resume_phase=resume_phase
        )

    def _resolve_space(
        self,
        player: PlayerState,
        dice_total: int,
        *,
        allow_build: bool = False,
        resume_phase: TurnPhase = TurnPhase.ASSET_MANAGEMENT,
    ) -> list[GameEvent]:
        space = BOARD_BY_POSITION[player.position]
        events: list[GameEvent] = [
            GameEvent("space_landed", {"player_id": player.player_id, "position": space.position})
        ]
        was_owned_by_player = False
        if space.is_property:
            property_state = self.state.properties[space.position]
            was_owned_by_player = property_state.owner_id == player.player_id
            if property_state.owner_id is None and player.cash >= (space.price or 0):
                player.cash -= space.price or 0
                player.properties.add(space.position)
                property_state.owner_id = player.player_id
                events.append(
                    GameEvent(
                        "property_purchased",
                        {
                            "player_id": player.player_id,
                            "position": space.position,
                            "price": space.price,
                        },
                    )
                )
            elif (
                property_state.owner_id is not None and property_state.owner_id != player.player_id
            ):
                owner = self.state.players[property_state.owner_id]
                self._collect_rent(
                    player, owner, space.position, dice_total, events, resume_phase=resume_phase
                )
        elif space.kind is SpaceKind.TAX:
            self._pay(
                player,
                space.tax or 0,
                None,
                "tax",
                events,
                resume_phase=resume_phase,
            )
        elif space.kind is SpaceKind.GO_TO_JAIL:
            player.position = 10
            player.jail_status = JailStatus.WAITING
            player.jail_roll_attempts = 0
            self.state.turn_phase = TurnPhase.TURN_COMPLETE
            events.append(
                GameEvent("player_jailed", {"player_id": player.player_id, "reason": "go_to_jail"})
            )
        elif space.kind in {SpaceKind.CHANCE, SpaceKind.COMMUNITY_CHEST}:
            deck = CardDeck.CHANCE if space.kind is SpaceKind.CHANCE else CardDeck.COMMUNITY_CHEST
            self._queue_card_draw(player, deck, events, resume_phase=resume_phase)
            self._drain_settlement_operations(events)
        if allow_build and space.kind is SpaceKind.STREET and was_owned_by_player:
            events.extend(self._automatically_build(player, space.position))
        return events

    def _automatically_build(self, player: PlayerState, position: int) -> list[GameEvent]:
        """Build one ordinary level after a qualifying dice landing, if affordable."""
        property_state = self.state.properties[position]
        space = BOARD_BY_POSITION[position]
        if (
            property_state.owner_id != player.player_id
            or property_state.mortgaged
            or property_state.building_level == 5
        ):
            return []
        cost = space.building_cost or 0
        if player.cash < cost:
            return [
                GameEvent(
                    "automatic_build_skipped_insufficient_cash",
                    {"player_id": player.player_id, "position": position, "cost": cost},
                )
            ]
        player.cash -= cost
        property_state.building_level += 1
        return [
            GameEvent(
                "building_added",
                {
                    "player_id": player.player_id,
                    "position": position,
                    "cost": cost,
                    "reason": "dice_landing",
                },
            )
        ]

    def _pay(
        self,
        payer: PlayerState,
        amount: int,
        recipient: PlayerState | None,
        reason: str,
        events: list[GameEvent],
        pending_movement_steps: int | None = None,
        resume_phase: TurnPhase = TurnPhase.ASSET_MANAGEMENT,
    ) -> None:
        if amount <= 0:
            return
        operation = self._queue_payment(
            payer,
            amount,
            recipient,
            reason,
            resume_phase,
            pending_movement_steps,
            events,
        )
        self._drain_settlement_operations(events)
        if operation.status is SettlementOperationStatus.BLOCKED:
            self.state.turn_phase = TurnPhase.PAYMENT_RESOLUTION

    def _queue_payment(
        self,
        payer: PlayerState,
        amount: int,
        recipient: PlayerState | None,
        reason: str,
        resume_phase: TurnPhase,
        movement_steps: int | None,
        events: list[GameEvent],
        resume_player_id: str | None = None,
    ) -> SettlementOperation:
        operation = SettlementOperation(
            operation_id=self.state.next_settlement_operation_id,
            kind=SettlementOperationKind.PAYMENT,
            player_id=payer.player_id,
            recipient_id=recipient.player_id if recipient else None,
            amount=amount,
            source=reason,
            steps=movement_steps,
            resume_phase=resume_phase,
            resume_player_id=resume_player_id,
        )
        self.state.next_settlement_operation_id += 1
        self.state.settlement_operations.append(operation)
        events.append(
            GameEvent(
                "settlement_operation_queued",
                {
                    "operation_id": operation.operation_id,
                    "kind": operation.kind.value,
                    "payer_id": operation.player_id,
                    "recipient_id": operation.recipient_id,
                    "amount": operation.amount,
                    "reason": operation.source,
                },
            )
        )
        return operation

    def _collect_rent(
        self,
        payer: PlayerState,
        owner: PlayerState,
        position: int,
        dice_total: int,
        events: list[GameEvent],
        *,
        resume_phase: TurnPhase = TurnPhase.ASSET_MANAGEMENT,
    ) -> None:
        amount = rent_due(position, owner, self.state, dice_total)
        color_group = BOARD_BY_POSITION[position].color_group
        if color_group is not None and self._has_effect(OngoingEffectKind.RENT_FREEZE, color_group):
            events.append(GameEvent("rent_frozen", {"position": position}))
            return
        if color_group is not None and self._has_effect(OngoingEffectKind.RENT_SURGE, color_group):
            amount *= 2
        if amount <= 0:
            return
        if payer.rent_waivers:
            payer.rent_waivers -= 1
            events.append(
                GameEvent(
                    "rent_waiver_used",
                    {
                        "player_id": payer.player_id,
                        "position": position,
                        "remaining_waivers": payer.rent_waivers,
                    },
                )
            )
            return
        alliance = self._alliance_for(owner.player_id)
        if alliance is None:
            self._pay(payer, amount, owner, "rent", events, resume_phase=resume_phase)
            return
        partner_id = (
            alliance.target_player_id
            if alliance.source_player_id == owner.player_id
            else alliance.source_player_id
        )
        if partner_id is None:
            raise AssertionError("alliance has no partner")
        operation = self._queue_payment(payer, amount, owner, "rent", resume_phase, None, events)
        operation.alliance_partner_id = partner_id
        self._drain_settlement_operations(events)

    def _has_effect(self, kind: OngoingEffectKind, color_group: str) -> bool:
        return any(
            effect.kind is kind and effect.color_group == color_group
            for effect in self.state.ongoing_effects
        )

    def _alliance_for(self, player_id: str) -> OngoingEffect | None:
        return next(
            (
                effect
                for effect in self.state.ongoing_effects
                if effect.kind is OngoingEffectKind.ALLIANCE
                and player_id in {effect.source_player_id, effect.target_player_id}
            ),
            None,
        )

    def _discard_held_chance_card(self, player: PlayerState, card_id: str) -> list[GameEvent]:
        if card_id not in player.chance_cards:
            raise GameRuleError("player does not hold the chance card")
        player.chance_cards.remove(card_id)
        self._discard_card(card_id, CardDeck.CHANCE)
        return [
            GameEvent(
                "card_discarded",
                {
                    "player_id": player.player_id,
                    "card_id": card_id,
                    "deck": CardDeck.CHANCE.value,
                    "reason": "hand_limit",
                },
            )
        ]

    def _use_community_get_out_of_jail_card(
        self, player: PlayerState, card_id: str
    ) -> list[GameEvent]:
        if player.jail_status is JailStatus.FREE:
            raise GameRuleError("get-out-of-jail card is unavailable while free")
        if player.jail_status is JailStatus.WAITING:
            raise GameRuleError("get-out-of-jail card is unavailable during the wait turn")
        if card_id not in player.community_get_out_of_jail_cards:
            raise GameRuleError("player does not hold the selected get-out-of-jail card")
        player.community_get_out_of_jail_cards.remove(card_id)
        self._discard_card(card_id, CardDeck.COMMUNITY_CHEST)
        player.jail_status = JailStatus.FREE
        player.jail_roll_attempts = 0
        return [
            GameEvent(
                "card_discarded",
                {
                    "player_id": player.player_id,
                    "card_id": card_id,
                    "deck": CardDeck.COMMUNITY_CHEST.value,
                    "reason": "played",
                },
            ),
            GameEvent("jail_released", {"player_id": player.player_id, "method": "card"}),
        ]

    def _select_stolen_chance_card(self, player: PlayerState, card_id: str) -> list[GameEvent]:
        if self.state.pending_theft_thief_id != player.player_id:
            raise GameRuleError("only the successful thief may select a card")
        target_id = self.state.pending_theft_target_id
        if target_id is None:
            raise AssertionError("theft selection has no target")
        target = self.state.players[target_id]
        if card_id not in target.chance_cards:
            raise GameRuleError("target does not hold the selected chance card")
        theft_card_id = self.state.pending_theft_source_card_id
        if theft_card_id is None:
            raise AssertionError("theft selection has no source card")
        if theft_card_id not in player.chance_cards:
            raise AssertionError("successful thief no longer holds the source card")
        target.chance_cards.remove(card_id)
        player.chance_cards.append(card_id)
        player.chance_cards.remove(theft_card_id)
        self._discard_card(theft_card_id, CardDeck.CHANCE)
        self.state.pending_theft_thief_id = None
        self.state.pending_theft_target_id = None
        self.state.pending_theft_source_card_id = None
        self.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
        return [
            GameEvent(
                "chance_card_stolen",
                {
                    "player_id": player.player_id,
                    "target_player_id": target.player_id,
                    "card_id": card_id,
                },
            ),
            GameEvent(
                "card_discarded",
                {
                    "player_id": player.player_id,
                    "card_id": theft_card_id,
                    "deck": CardDeck.CHANCE.value,
                    "reason": "played",
                },
            ),
        ]

    def _use_chance_card(self, player: PlayerState, command: UseChanceCard) -> list[GameEvent]:
        if command.card_id not in player.chance_cards:
            raise GameRuleError("player does not hold the chance card")
        card = CARDS_BY_ID.get(command.card_id)
        if card is None or card.deck is not CardDeck.CHANCE:
            raise GameRuleError("unknown chance card")
        events = [
            GameEvent(
                "chance_card_used",
                {
                    "player_id": player.player_id,
                    "card_id": card.card_id,
                    "target_player_id": command.target_player_id,
                    "target_position": command.target_position,
                    "target_color_group": command.target_color_group,
                },
            )
        ]
        if card.effect is CardEffect.STEAL_CARD:
            target = self._player_target(player, command.target_player_id, card.range or 0)
            if not target.chance_cards:
                raise GameRuleError("target does not hold a chance card")
            self.state.pending_theft_thief_id = player.player_id
            self.state.pending_theft_target_id = target.player_id
            self.state.pending_theft_source_card_id = card.card_id
            self.state.turn_phase = TurnPhase.THEFT_CARD_SELECTION
            events.append(
                GameEvent(
                    "chance_card_theft_selection_required",
                    {
                        "player_id": player.player_id,
                        "target_player_id": target.player_id,
                        "card_count": len(target.chance_cards),
                    },
                )
            )
            return events
        elif card.effect is CardEffect.ALLIANCE:
            target = self._player_target(player, command.target_player_id, card.range or 0)
            self._add_ongoing_effect(
                OngoingEffectKind.ALLIANCE,
                player,
                card.turns or 0,
                events,
                target_player_id=target.player_id,
            )
        elif card.effect is CardEffect.RENT_SURGE:
            color_group = self._color_group_target(
                player, command.target_color_group, card.range or 0
            )
            self._add_ongoing_effect(
                OngoingEffectKind.RENT_SURGE,
                player,
                card.turns or 0,
                events,
                color_group=color_group,
            )
        elif card.effect is CardEffect.RENT_FREEZE:
            color_group = self._color_group_target(
                player, command.target_color_group, card.range or 0
            )
            self._add_ongoing_effect(
                OngoingEffectKind.RENT_FREEZE,
                player,
                card.turns or 0,
                events,
                color_group=color_group,
            )
        elif card.effect is CardEffect.RENT_WAIVER:
            player.rent_waivers += card.amount or 0
            events.append(
                GameEvent(
                    "rent_waivers_granted",
                    {"player_id": player.player_id, "amount": card.amount or 0},
                )
            )
        elif card.effect is CardEffect.JAIL_PLAYER:
            target = self._player_target(player, command.target_player_id, card.range or 0)
            target.position = 10
            target.jail_status = JailStatus.WAITING
            target.jail_roll_attempts = 0
            events.append(
                GameEvent("player_jailed", {"player_id": target.player_id, "reason": card.card_id})
            )
        elif card.effect is CardEffect.NUCLEAR_RESET:
            if command.target_position is not None or command.target_player_id is not None:
                raise GameRuleError("nuclear card does not accept a target")
            die = self.random.randint(1, 6)
            center = (player.position + die) % 40
            events.append(
                GameEvent(
                    "card_die_rolled",
                    {"player_id": player.player_id, "card_id": card.card_id, "die": die},
                )
            )
            for position in ((center - 1) % 40, center, (center + 1) % 40):
                space = BOARD_BY_POSITION[position]
                if space.kind is SpaceKind.STREET:
                    self._reset_property(position, events, card.card_id)
        elif card.effect is CardEffect.MONSTER:
            color_group = self._color_group_target(
                player, command.target_color_group, card.range or 0
            )
            for position in COLOR_GROUPS[color_group]:
                property_state = self.state.properties[position]
                if property_state.building_level > 0:
                    property_state.building_level -= 1
                    events.append(
                        GameEvent(
                            "building_level_changed",
                            {
                                "position": position,
                                "building_level": property_state.building_level,
                                "reason": card.card_id,
                            },
                        )
                    )
        elif card.effect is CardEffect.ANGEL:
            color_group = self._color_group_target(
                player, command.target_color_group, card.range or 0
            )
            for position in COLOR_GROUPS[color_group]:
                property_state = self.state.properties[position]
                if (
                    property_state.owner_id is not None
                    and not property_state.mortgaged
                    and property_state.building_level < 5
                ):
                    property_state.building_level += 1
                    events.append(
                        GameEvent(
                            "building_level_changed",
                            {
                                "position": position,
                                "building_level": property_state.building_level,
                                "reason": card.card_id,
                            },
                        )
                    )
        elif card.effect is CardEffect.EQUALIZE_CASH:
            target = self._player_target(player, command.target_player_id, card.range or 0)
            total = player.cash + target.cash
            share = _round_ratio_half_up(total, 2)
            bank_adjustment = share * 2 - total
            player.cash = share
            target.cash = share
            events.append(
                GameEvent(
                    "cash_equalized",
                    {
                        "player_id": player.player_id,
                        "target_player_id": target.player_id,
                        "original_total": total,
                        "player_cash": player.cash,
                        "target_cash": target.cash,
                    },
                )
            )
            if bank_adjustment:
                events.append(
                    GameEvent(
                        "cash_rounding_adjusted",
                        {
                            "reason": card.card_id,
                            "amount": bank_adjustment,
                            "source": "bank" if bank_adjustment > 0 else "bank_recovery",
                        },
                    )
                )
        elif card.effect is CardEffect.TAX_PLAYER:
            target = self._player_target(player, command.target_player_id, card.range or 0)
            amount = _round_ratio_half_up(target.cash * 35, 100)
            target.cash -= amount
            player.cash += amount
            events.append(
                GameEvent(
                    "cash_tax_transferred",
                    {
                        "player_id": player.player_id,
                        "target_player_id": target.player_id,
                        "amount": amount,
                        "reason": card.card_id,
                    },
                )
            )
        elif card.effect is CardEffect.VACATE_PROPERTY:
            position, owner = self._other_vacant_street_target(
                player, command.target_position, card.range or 0
            )
            price = BOARD_BY_POSITION[position].price or 0
            owner.properties.remove(position)
            property_state = self.state.properties[position]
            property_state.owner_id = None
            owner.cash += price
            events.append(
                GameEvent(
                    "property_vacated",
                    {
                        "player_id": player.player_id,
                        "owner_id": owner.player_id,
                        "position": position,
                        "price": price,
                    },
                )
            )
        elif card.effect is CardEffect.SWAP_PROPERTY:
            target_position, target_owner = self._other_vacant_street_target(
                player, command.target_position, card.range or 0
            )
            own_position = self._own_vacant_street_target(player, command.secondary_target_position)
            target_owner.properties.remove(target_position)
            target_owner.properties.add(own_position)
            player.properties.remove(own_position)
            player.properties.add(target_position)
            self.state.properties[target_position].owner_id = player.player_id
            self.state.properties[own_position].owner_id = target_owner.player_id
            events.append(
                GameEvent(
                    "properties_swapped",
                    {
                        "player_id": player.player_id,
                        "target_player_id": target_owner.player_id,
                        "player_position": own_position,
                        "target_position": target_position,
                    },
                )
            )
        elif card.effect is CardEffect.SWAP_BUILDINGS:
            target_position = self._street_target(player, command.target_position, card.range or 0)
            own_position = self._own_street_target(player, command.secondary_target_position)
            if target_position == own_position:
                raise GameRuleError("building swap requires two different streets")
            target_state = self.state.properties[target_position]
            own_state = self.state.properties[own_position]
            if target_state.owner_id is None:
                raise GameRuleError("building swap requires an owned target street")
            if target_state.mortgaged or own_state.mortgaged:
                raise GameRuleError("building swap requires unmortgaged streets")
            target_level, own_level = target_state.building_level, own_state.building_level
            target_state.building_level, own_state.building_level = own_level, target_level
            events.append(
                GameEvent(
                    "buildings_swapped",
                    {
                        "player_id": player.player_id,
                        "player_position": own_position,
                        "target_position": target_position,
                        "player_building_level": own_level,
                        "target_building_level": target_level,
                    },
                )
            )
        elif card.effect is CardEffect.BUY_PROPERTY:
            position, owner = self._other_vacant_street_target(
                player, command.target_position, card.range or 0
            )
            price = _round_ratio_half_up((BOARD_BY_POSITION[position].price or 0) * 150, 100)
            if player.cash < price:
                raise GameRuleError("insufficient cash to buy the target property")
            player.cash -= price
            owner.cash += price
            self._transfer_property(player, owner, position, price, card.card_id, events)
        elif card.effect is CardEffect.BUILD:
            position = self._own_street_target(player, command.target_position)
            if abs(position - player.position) > (card.range or 0):
                raise GameRuleError("target street is out of range")
            property_state = self.state.properties[position]
            if property_state.mortgaged:
                raise GameRuleError("target street must be unmortgaged")
            if property_state.building_level == 5:
                raise GameRuleError("target street already has a hotel")
            property_state.building_level += 1
            events.append(
                GameEvent(
                    "building_level_changed",
                    {
                        "position": position,
                        "building_level": property_state.building_level,
                        "reason": card.card_id,
                    },
                )
            )
        else:
            raise GameRuleError("chance card effect is not implemented")
        player.chance_cards.remove(card.card_id)
        self._discard_card(card.card_id, CardDeck.CHANCE)
        events.append(
            GameEvent(
                "card_discarded",
                {
                    "player_id": player.player_id,
                    "card_id": card.card_id,
                    "deck": CardDeck.CHANCE.value,
                    "reason": "played",
                },
            )
        )
        return events

    def _add_ongoing_effect(
        self,
        kind: OngoingEffectKind,
        player: PlayerState,
        turns: int,
        events: list[GameEvent],
        *,
        target_player_id: str | None = None,
        color_group: str | None = None,
    ) -> None:
        matching = next(
            (
                effect
                for effect in self.state.ongoing_effects
                if effect.kind is kind
                and (
                    effect.color_group == color_group
                    if color_group is not None
                    else {effect.source_player_id, effect.target_player_id}
                    == {player.player_id, target_player_id}
                )
            ),
            None,
        )
        if matching is not None:
            matching.source_player_id = player.player_id
            matching.remaining_turns = turns
            matching.activation_turn = player.survived_turns + 1
            matching.target_player_id = target_player_id
            matching.color_group = color_group
            event_type = "ongoing_effect_reset"
        else:
            self.state.ongoing_effects.append(
                OngoingEffect(
                    kind,
                    player.player_id,
                    turns,
                    player.survived_turns + 1,
                    target_player_id,
                    color_group,
                )
            )
            event_type = "ongoing_effect_created"
        events.append(
            GameEvent(
                event_type,
                {
                    "kind": kind.value,
                    "source_player_id": player.player_id,
                    "remaining_turns": turns,
                    "target_player_id": target_player_id,
                    "color_group": color_group,
                },
            )
        )

    def _advance_ongoing_effects(self, player: PlayerState) -> list[GameEvent]:
        events: list[GameEvent] = []
        remaining: list[OngoingEffect] = []
        for effect in self.state.ongoing_effects:
            if (
                effect.source_player_id != player.player_id
                or effect.activation_turn == player.survived_turns
            ):
                remaining.append(effect)
                continue
            effect.remaining_turns -= 1
            if effect.remaining_turns:
                remaining.append(effect)
                events.append(
                    GameEvent(
                        "ongoing_effect_advanced",
                        {
                            "kind": effect.kind.value,
                            "source_player_id": effect.source_player_id,
                            "remaining_turns": effect.remaining_turns,
                        },
                    )
                )
            else:
                events.append(
                    GameEvent(
                        "ongoing_effect_expired",
                        {"kind": effect.kind.value, "source_player_id": effect.source_player_id},
                    )
                )
        self.state.ongoing_effects = remaining
        return events

    def _player_target(
        self, player: PlayerState, target_player_id: str | None, range_limit: int
    ) -> PlayerState:
        if target_player_id is None or target_player_id == player.player_id:
            raise GameRuleError("a different player target is required")
        target = self.state.players.get(target_player_id)
        if (
            target is None
            or target.bankrupt
            or abs(target.position - player.position) > range_limit
        ):
            raise GameRuleError("target player is not legal")
        return target

    def _color_group_target(
        self, player: PlayerState, color_group: str | None, range_limit: int
    ) -> str:
        if color_group not in COLOR_GROUPS:
            raise GameRuleError("target color group is not legal")
        if not any(
            abs(position - player.position) <= range_limit for position in COLOR_GROUPS[color_group]
        ):
            raise GameRuleError("target color group is out of range")
        return color_group

    def _transfer_property(
        self,
        buyer: PlayerState,
        seller: PlayerState,
        position: int,
        price: int,
        reason: str,
        events: list[GameEvent],
    ) -> None:
        seller.properties.remove(position)
        buyer.properties.add(position)
        self.state.properties[position].owner_id = buyer.player_id
        events.append(
            GameEvent(
                "property_purchased_from_player",
                {
                    "player_id": buyer.player_id,
                    "owner_id": seller.player_id,
                    "position": position,
                    "price": price,
                    "reason": reason,
                },
            )
        )

    def _street_target(self, player: PlayerState, position: int | None, range_limit: int) -> int:
        if position is None:
            raise GameRuleError("target street is required")
        space = BOARD_BY_POSITION.get(position)
        if space is None or space.kind is not SpaceKind.STREET:
            raise GameRuleError("target must be a street")
        if abs(position - player.position) > range_limit:
            raise GameRuleError("target street is out of range")
        return position

    def _own_street_target(self, player: PlayerState, position: int | None) -> int:
        if position is None:
            raise GameRuleError("owned street target is required")
        space = BOARD_BY_POSITION.get(position)
        if space is None or space.kind is not SpaceKind.STREET or position not in player.properties:
            raise GameRuleError("target must be an owned street")
        return position

    def _own_vacant_street_target(self, player: PlayerState, position: int | None) -> int:
        position = self._own_street_target(player, position)
        property_state = self.state.properties[position]
        if property_state.building_level or property_state.mortgaged:
            raise GameRuleError("target street must be vacant and unmortgaged")
        return position

    def _other_vacant_street_target(
        self, player: PlayerState, position: int | None, range_limit: int
    ) -> tuple[int, PlayerState]:
        position = self._street_target(player, position, range_limit)
        property_state = self.state.properties[position]
        owner_id = property_state.owner_id
        if (
            owner_id is None
            or owner_id == player.player_id
            or property_state.building_level
            or property_state.mortgaged
        ):
            raise GameRuleError("target must be another player's vacant unmortgaged street")
        owner = self.state.players[owner_id]
        if owner.bankrupt:
            raise GameRuleError("target property owner is bankrupt")
        return position, owner

    def _reset_property(self, position: int, events: list[GameEvent], reason: str) -> None:
        property_state = self.state.properties[position]
        if property_state.owner_id is not None:
            self.state.players[property_state.owner_id].properties.remove(position)
        property_state.owner_id = None
        property_state.building_level = 0
        property_state.mortgaged = False
        events.append(GameEvent("property_reset", {"position": position, "reason": reason}))

    def _queue_card_move(
        self,
        player: PlayerState,
        destination: int,
        source: str,
        events: list[GameEvent],
        *,
        collect_go_salary: bool,
        resume_phase: TurnPhase,
        resume_player_id: str | None,
    ) -> None:
        operation = SettlementOperation(
            operation_id=self.state.next_settlement_operation_id,
            kind=SettlementOperationKind.MOVE,
            player_id=player.player_id,
            source=source,
            destination=destination,
            dice_total=0,
            collect_go_salary=collect_go_salary,
            allow_build=False,
            resume_phase=resume_phase,
            resume_player_id=resume_player_id,
        )
        self.state.next_settlement_operation_id += 1
        self.state.settlement_operations.append(operation)
        events.append(
            GameEvent(
                "settlement_operation_queued",
                {
                    "operation_id": operation.operation_id,
                    "kind": operation.kind.value,
                    "player_id": player.player_id,
                    "destination": destination,
                    "source": source,
                },
            )
        )

    def _queue_card_draw(
        self,
        player: PlayerState,
        deck: CardDeck,
        events: list[GameEvent],
        *,
        resume_phase: TurnPhase,
    ) -> None:
        operation = SettlementOperation(
            operation_id=self.state.next_settlement_operation_id,
            kind=SettlementOperationKind.CARD_DRAW,
            player_id=player.player_id,
            source="board_card_draw",
            resume_phase=resume_phase,
            resume_player_id=self.state.current_player_id,
            deck=deck,
        )
        self.state.next_settlement_operation_id += 1
        self.state.settlement_operations.append(operation)
        events.append(
            GameEvent(
                "settlement_operation_queued",
                {
                    "operation_id": operation.operation_id,
                    "kind": operation.kind.value,
                    "player_id": player.player_id,
                    "deck": deck.value,
                },
            )
        )

    def _draw_card(self, deck: CardDeck) -> str | None:
        if deck is CardDeck.CHANCE:
            draw_pile = self.state.chance_draw_pile
            discard_pile = self.state.chance_discard_pile
        else:
            draw_pile = self.state.community_chest_draw_pile
            discard_pile = self.state.community_chest_discard_pile
        if not draw_pile:
            self.random.shuffle(discard_pile)
            draw_pile.extend(discard_pile)
            discard_pile.clear()
        if not draw_pile:
            return None
        return draw_pile.pop()

    def _discard_card(self, card_id: str, deck: CardDeck) -> None:
        if deck is CardDeck.CHANCE:
            self.state.chance_discard_pile.append(card_id)
        else:
            self.state.community_chest_discard_pile.append(card_id)

    def _resolve_card_draw(self, operation: SettlementOperation, events: list[GameEvent]) -> None:
        if operation.deck is None:
            raise AssertionError("card draw operation has no deck")
        card_id = self._draw_card(operation.deck)
        if card_id is None:
            return
        card = CARDS_BY_ID[card_id]
        events.append(
            GameEvent(
                "card_drawn",
                {
                    "operation_id": operation.operation_id,
                    "player_id": operation.player_id,
                    "card_id": card.card_id,
                    "deck": operation.deck.value,
                },
            )
        )
        player = self.state.players[operation.player_id]
        if operation.deck is CardDeck.CHANCE:
            player.chance_cards.append(card.card_id)
            events.append(
                GameEvent(
                    "card_held",
                    {"player_id": player.player_id, "card_id": card.card_id},
                )
            )
            return
        if card.effect is CardEffect.GET_OUT_OF_JAIL:
            player.community_get_out_of_jail_cards.append(card.card_id)
            events.append(
                GameEvent(
                    "card_held",
                    {"player_id": player.player_id, "card_id": card.card_id},
                )
            )
            return
        if card.effect is CardEffect.RECEIVE_CASH:
            if card.amount is None:
                raise AssertionError("cash card has no amount")
            player.cash += card.amount
            events.append(
                GameEvent(
                    "cash_received",
                    {"player_id": player.player_id, "amount": card.amount, "reason": card.card_id},
                )
            )
        elif card.effect is CardEffect.PAY_BANK:
            if card.amount is None:
                raise AssertionError("payment card has no amount")
            self._queue_payment(
                player,
                card.amount,
                None,
                card.card_id,
                TurnPhase.ASSET_MANAGEMENT,
                None,
                events,
            )
        elif card.effect is CardEffect.GO_TO_JAIL:
            player.position = 10
            player.jail_status = JailStatus.WAITING
            player.jail_roll_attempts = 0
            self.state.turn_phase = TurnPhase.TURN_COMPLETE
            events.append(
                GameEvent("player_jailed", {"player_id": player.player_id, "reason": card.card_id})
            )
        elif card.effect is CardEffect.MOVE_TO_GO:
            self._queue_card_move(
                player,
                0,
                card.card_id,
                events,
                collect_go_salary=True,
                resume_phase=operation.resume_phase or TurnPhase.ASSET_MANAGEMENT,
                resume_player_id=operation.resume_player_id,
            )
        elif card.effect is CardEffect.REPAIRS:
            amount = sum(
                115
                if self.state.properties[position].building_level == 5
                else 40 * self.state.properties[position].building_level
                for position in player.properties
            )
            self._queue_payment(
                player,
                amount,
                None,
                card.card_id,
                TurnPhase.ASSET_MANAGEMENT,
                None,
                events,
            )
        elif card.effect is CardEffect.BIRTHDAY:
            for payer in sorted(self._living_players(), key=lambda item: item.seat):
                if payer.player_id != player.player_id:
                    self._queue_payment(
                        payer,
                        card.amount or 0,
                        player,
                        card.card_id,
                        TurnPhase.ASSET_MANAGEMENT,
                        None,
                        events,
                        resume_player_id=player.player_id,
                    )
        else:
            raise AssertionError(f"card effect not implemented: {card.effect}")
        self._discard_card(card.card_id, operation.deck)
        events.append(
            GameEvent(
                "card_discarded",
                {
                    "player_id": operation.player_id,
                    "card_id": card.card_id,
                    "deck": operation.deck.value,
                    "reason": "played",
                },
            )
        )
        self._drain_settlement_operations(events)

    def _drain_settlement_operations(self, events: list[GameEvent]) -> None:
        while self.state.settlement_operations:
            operation = self.state.settlement_operations[0]
            was_blocked = operation.status is SettlementOperationStatus.BLOCKED
            if was_blocked:
                if operation.amount is None:
                    raise AssertionError("payment operation has no amount")
                payer = self.state.players[operation.player_id]
                if payer.cash < operation.amount:
                    if not self._has_liquidation_option(payer):
                        self._bankrupt(payer, events)
                        events.extend(self._after_bankruptcy(payer.player_id))
                        if self.state.finished:
                            return
                        continue
                    return
                operation.status = SettlementOperationStatus.PENDING
            if operation.kind is SettlementOperationKind.CARD_DRAW:
                self.state.settlement_operations.pop(0)
                self._resolve_card_draw(operation, events)
                events.append(
                    GameEvent(
                        "settlement_operation_completed",
                        {"operation_id": operation.operation_id, "kind": operation.kind.value},
                    )
                )
                continue
            if operation.kind is SettlementOperationKind.MOVE:
                self.state.settlement_operations.pop(0)
                player = self.state.players[operation.player_id]
                start = player.position
                if operation.destination is None:
                    raise AssertionError("move operation has no destination")
                destination = operation.destination % 40
                player.position = destination
                events.append(
                    GameEvent(
                        "player_moved",
                        {
                            "player_id": player.player_id,
                            "from": start,
                            "to": destination,
                            "steps": operation.steps,
                            "source": operation.source,
                        },
                    )
                )
                if operation.collect_go_salary:
                    player.cash += 200
                    events.append(
                        GameEvent(
                            "go_salary_collected",
                            {"player_id": player.player_id, "amount": 200},
                        )
                    )
                events.extend(
                    self._resolve_space(
                        player,
                        operation.dice_total or 0,
                        allow_build=operation.allow_build,
                        resume_phase=operation.resume_phase or TurnPhase.ASSET_MANAGEMENT,
                    )
                )
                if self.state.turn_phase not in {
                    TurnPhase.PAYMENT_RESOLUTION,
                    TurnPhase.THEFT_CARD_SELECTION,
                    TurnPhase.TURN_COMPLETE,
                }:
                    self.state.turn_phase = operation.resume_phase or TurnPhase.ASSET_MANAGEMENT
                if operation.resume_player_id is not None:
                    self.state.current_player_id = operation.resume_player_id
                events.append(
                    GameEvent(
                        "settlement_operation_completed",
                        {"operation_id": operation.operation_id, "kind": operation.kind.value},
                    )
                )
                continue
            if operation.kind is not SettlementOperationKind.PAYMENT:
                raise AssertionError("unsupported settlement operation")
            if operation.amount is None:
                raise AssertionError("payment operation has no amount")
            payer = self.state.players[operation.player_id]
            if payer.cash < operation.amount:
                if not self._has_liquidation_option(payer):
                    self._bankrupt(payer, events)
                    events.extend(self._after_bankruptcy(payer.player_id))
                    if self.state.finished:
                        return
                    continue
                operation.status = SettlementOperationStatus.BLOCKED
                self.state.current_player_id = operation.player_id
                self.state.turn_phase = TurnPhase.PAYMENT_RESOLUTION
                events.append(
                    GameEvent(
                        "payment_required",
                        {
                            "operation_id": operation.operation_id,
                            "payer_id": operation.player_id,
                            "amount": operation.amount,
                            "reason": operation.source,
                        },
                    )
                )
                return
            payer.cash -= operation.amount
            if operation.recipient_id is not None:
                self.state.players[operation.recipient_id].cash += operation.amount
            if operation.alliance_partner_id is not None:
                if operation.recipient_id is None:
                    raise AssertionError("alliance rent has no owner recipient")
                owner = self.state.players[operation.recipient_id]
                partner = self.state.players[operation.alliance_partner_id]
                owner_share = _round_ratio_half_up(operation.amount, 2)
                partner_share = _round_ratio_half_up(operation.amount, 2)
                transfer = operation.amount - owner_share
                owner.cash -= transfer
                partner.cash += transfer
                bank_adjustment = partner_share - transfer
                if bank_adjustment:
                    partner.cash += bank_adjustment
                    events.append(
                        GameEvent(
                            "alliance_rent_rounding_adjusted",
                            {
                                "player_id": operation.alliance_partner_id,
                                "amount": bank_adjustment,
                                "source": "bank" if bank_adjustment > 0 else "bank_recovery",
                            },
                        )
                    )
            self.state.settlement_operations.pop(0)
            events.extend(
                (
                    GameEvent(
                        "payment_made",
                        {
                            "operation_id": operation.operation_id,
                            "payer_id": operation.player_id,
                            "recipient_id": operation.recipient_id,
                            "amount": operation.amount,
                            "reason": operation.source,
                        },
                    ),
                    GameEvent(
                        "settlement_operation_completed",
                        {"operation_id": operation.operation_id, "kind": operation.kind.value},
                    ),
                )
            )
            if operation.resume_player_id is not None:
                self.state.current_player_id = operation.resume_player_id
            if operation.steps is not None:
                payer.jail_status = JailStatus.FREE
                payer.jail_roll_attempts = 0
                if operation.resume_phase is not None:
                    self.state.turn_phase = operation.resume_phase
                events.extend(self._move_and_resolve(payer, operation.steps))
            elif operation.resume_phase is not None and (
                was_blocked or not self.state.settlement_operations
            ):
                self.state.turn_phase = operation.resume_phase

    def _blocked_payment(self) -> SettlementOperation | None:
        if not self.state.settlement_operations:
            return None
        operation = self.state.settlement_operations[0]
        if (
            operation.kind is SettlementOperationKind.PAYMENT
            and operation.status is SettlementOperationStatus.BLOCKED
        ):
            return operation
        return None

    def _settle_pending_payment(self, events: list[GameEvent]) -> None:
        self._drain_settlement_operations(events)

    def _has_liquidation_option(self, player: PlayerState) -> bool:
        return any(
            self.state.properties[position].building_level > 0
            or (
                not self.state.properties[position].mortgaged
                and self.state.properties[position].building_level == 0
            )
            for position in player.properties
        )

    def _after_bankruptcy(self, player_id: str) -> list[GameEvent]:
        retained: list[SettlementOperation] = []
        events: list[GameEvent] = []
        for operation in self.state.settlement_operations:
            if operation.player_id == player_id:
                events.append(
                    GameEvent(
                        "settlement_operation_cancelled",
                        {"operation_id": operation.operation_id, "reason": "payer_bankrupt"},
                    )
                )
            else:
                retained.append(operation)
        self.state.settlement_operations = retained
        living = self._living_players()
        if len(living) == 1:
            for operation in retained:
                events.append(
                    GameEvent(
                        "settlement_operation_cancelled",
                        {"operation_id": operation.operation_id, "reason": "game_finished"},
                    )
                )
            self.state.settlement_operations.clear()
            self._finish(EndReason.LAST_SURVIVOR)
            return events + [GameEvent("game_finished", {"reason": EndReason.LAST_SURVIVOR.value})]
        if retained:
            self.state.current_player_id = retained[0].player_id
        else:
            self.state.turn_phase = TurnPhase.TURN_COMPLETE
        return events

    def _bankrupt(self, player: PlayerState, events: list[GameEvent]) -> None:
        if player.chance_cards:
            self.state.chance_discard_pile.extend(player.chance_cards)
            for card_id in player.chance_cards:
                events.append(
                    GameEvent(
                        "card_discarded",
                        {
                            "player_id": player.player_id,
                            "card_id": card_id,
                            "deck": CardDeck.CHANCE.value,
                            "reason": "bankruptcy",
                        },
                    )
                )
            player.chance_cards.clear()
        if player.community_get_out_of_jail_cards:
            self.state.community_chest_discard_pile.extend(player.community_get_out_of_jail_cards)
            for card_id in player.community_get_out_of_jail_cards:
                events.append(
                    GameEvent(
                        "card_discarded",
                        {
                            "player_id": player.player_id,
                            "card_id": card_id,
                            "deck": CardDeck.COMMUNITY_CHEST.value,
                            "reason": "bankruptcy",
                        },
                    )
                )
            player.community_get_out_of_jail_cards.clear()
        player.cash = 0
        for position in tuple(player.properties):
            property_state = self.state.properties[position]
            property_state.owner_id = None
            property_state.building_level = 0
            property_state.mortgaged = False
        player.properties.clear()
        player.bankrupt = True
        events.append(GameEvent("player_bankrupt", {"player_id": player.player_id}))

    def _build(self, player: PlayerState, position: int) -> list[GameEvent]:
        raise GameRuleError("ordinary building resolves automatically after dice landings")

    def _sell_building(self, player: PlayerState, position: int) -> list[GameEvent]:
        space = self._owned_street(player, position)
        property_state = self.state.properties[position]
        if property_state.building_level == 0:
            raise GameRuleError("property has no building to sell")
        property_state.building_level -= 1
        amount = (space.building_cost or 0) // 2
        player.cash += amount
        events = [
            GameEvent(
                "building_sold",
                {"player_id": player.player_id, "position": position, "amount": amount},
            )
        ]
        self._settle_pending_payment(events)
        return events

    def _mortgage(self, player: PlayerState, position: int) -> list[GameEvent]:
        space = BOARD_BY_POSITION[position]
        property_state = self._owned_property(player, position)
        if property_state.mortgaged or property_state.building_level > 0:
            raise GameRuleError("property cannot be mortgaged")
        amount = space.price or 0
        property_state.mortgaged = True
        player.cash += amount
        events = [
            GameEvent(
                "property_mortgaged",
                {"player_id": player.player_id, "position": position, "amount": amount},
            )
        ]
        self._settle_pending_payment(events)
        return events

    def _redeem(self, player: PlayerState, position: int) -> list[GameEvent]:
        space = BOARD_BY_POSITION[position]
        property_state = self._owned_property(player, position)
        amount = (space.price or 0) * 110 // 100
        if not property_state.mortgaged or player.cash < amount:
            raise GameRuleError("property cannot be redeemed")
        property_state.mortgaged = False
        player.cash -= amount
        return [
            GameEvent(
                "mortgage_redeemed",
                {"player_id": player.player_id, "position": position, "amount": amount},
            )
        ]

    def _pay_jail_fine(self, player: PlayerState) -> list[GameEvent]:
        if player.jail_status is not JailStatus.ROLLING or player.cash < 50:
            raise GameRuleError("jail fine is not payable")
        player.cash -= 50
        player.jail_status = JailStatus.FREE
        player.jail_roll_attempts = 0
        self.state.turn_phase = TurnPhase.ROLLING
        return [GameEvent("jail_released", {"player_id": player.player_id, "method": "fine"})]

    def _owned_property(self, player: PlayerState, position: int) -> PropertyState:
        if position not in player.properties:
            raise GameRuleError("player does not own property")
        return self.state.properties[position]

    def _owned_street(self, player: PlayerState, position: int):
        space = BOARD_BY_POSITION[position]
        if space.kind is not SpaceKind.STREET:
            raise GameRuleError("property must be a street")
        self._owned_property(player, position)
        return space

    def _living_players(self) -> list[PlayerState]:
        return [player for player in self.state.players.values() if not player.bankrupt]

    def _next_living_player(self, current: PlayerState) -> PlayerState:
        ordered = sorted(self._living_players(), key=lambda player: player.seat)
        for player in ordered:
            if player.seat > current.seat:
                return player
        return ordered[0]

    def _finish(self, reason: EndReason) -> None:
        self.state.finished = True
        self.state.end_reason = reason
        self.state.rankings = tuple(
            player.player_id
            for player in sorted(
                self._living_players(),
                key=lambda item: (net_worth(item, self.state), item.cash),
                reverse=True,
            )
        )

    def _validate_invariants(self) -> None:
        for _position, property_state in self.state.properties.items():
            if not 0 <= property_state.building_level <= 5:
                raise AssertionError("invalid building level")
            if property_state.owner_id is None and (
                property_state.building_level or property_state.mortgaged
            ):
                raise AssertionError("unowned property has state")
        for player in self.state.players.values():
            if player.cash < 0:
                raise AssertionError("cash cannot be negative")
            for position in player.properties:
                if position not in self.state.properties:
                    raise AssertionError("player has non-property position")
                if self.state.properties[position].owner_id != player.player_id:
                    raise AssertionError("property ownership mismatch")
        for position, property_state in self.state.properties.items():
            if (
                property_state.owner_id is not None
                and position not in self.state.players[property_state.owner_id].properties
            ):
                raise AssertionError("owned property is absent from player holdings")
        if self.state.turn_phase is TurnPhase.THEFT_CARD_SELECTION:
            thief_id = self.state.pending_theft_thief_id
            target_id = self.state.pending_theft_target_id
            source_card_id = self.state.pending_theft_source_card_id
            if thief_id is None or target_id is None or source_card_id is None:
                raise AssertionError("theft selection has incomplete pending state")
            if source_card_id not in self.state.players[thief_id].chance_cards:
                raise AssertionError("theft source card is not held by thief")
            if not self.state.players[target_id].chance_cards:
                raise AssertionError("theft target has no chance cards")
        elif any(
            value is not None
            for value in (
                self.state.pending_theft_thief_id,
                self.state.pending_theft_target_id,
                self.state.pending_theft_source_card_id,
            )
        ):
            raise AssertionError("pending theft state exists outside theft selection")
        if self.state.settlement_operations:
            operation_ids = [
                operation.operation_id for operation in self.state.settlement_operations
            ]
            if len(operation_ids) != len(set(operation_ids)):
                raise AssertionError("settlement operation IDs must be unique")
            payment = self._blocked_payment()
            if self.state.turn_phase is TurnPhase.PAYMENT_RESOLUTION:
                if payment is None:
                    raise AssertionError("payment resolution has no blocked payment")
                if payment.player_id != self.state.current_player_id:
                    raise AssertionError("blocked payment has invalid payer")
            elif payment is not None:
                raise AssertionError("blocked payment has invalid phase")
