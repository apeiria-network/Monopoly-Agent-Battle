"""Versioned card declarations for the classic Level 0 rules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from monopoly_agent_battle.domain.models import CardDeck


class CardEffect(StrEnum):
    STEAL_CARD = "steal_card"
    TAX_PLAYER = "tax_player"
    VACATE_PROPERTY = "vacate_property"
    ANGEL = "angel"
    SWAP_PROPERTY = "swap_property"
    EQUALIZE_CASH = "equalize_cash"
    SWAP_BUILDINGS = "swap_buildings"
    JAIL_PLAYER = "jail_player"
    NUCLEAR_RESET = "nuclear_reset"  # disabled; kept for backward compatibility
    TAXI_MOVE = "taxi_move"
    ALLIANCE = "alliance"
    RENT_WAIVER = "rent_waiver"
    MONSTER = "monster"
    RENT_SURGE = "rent_surge"
    BUY_PROPERTY = "buy_property"
    RENT_FREEZE = "rent_freeze"
    BUILD = "build"
    MOVE_TO_GO = "move_to_go"
    RECEIVE_CASH = "receive_cash"
    PAY_BANK = "pay_bank"
    GET_OUT_OF_JAIL = "get_out_of_jail"
    GO_TO_JAIL = "go_to_jail"
    BIRTHDAY = "birthday"
    REPAIRS = "repairs"


@dataclass(frozen=True, slots=True)
class Card:
    card_id: str
    deck: CardDeck
    name: str
    effect: CardEffect
    range: int | None = None
    amount: int | None = None
    turns: int | None = None


CHANCE_CARDS: tuple[Card, ...] = (
    Card("chance-steal", CardDeck.CHANCE, "抢夺卡", CardEffect.STEAL_CARD, range=5),
    Card("chance-tax", CardDeck.CHANCE, "查税卡", CardEffect.TAX_PLAYER, range=5),
    Card("chance-vacate", CardDeck.CHANCE, "空地卡", CardEffect.VACATE_PROPERTY, range=5),
    Card("chance-angel", CardDeck.CHANCE, "天使卡", CardEffect.ANGEL, range=5),
    Card("chance-swap-property", CardDeck.CHANCE, "换地卡", CardEffect.SWAP_PROPERTY, range=5),
    Card("chance-equalize", CardDeck.CHANCE, "均富卡", CardEffect.EQUALIZE_CASH, range=5),
    Card("chance-swap-buildings", CardDeck.CHANCE, "换屋卡", CardEffect.SWAP_BUILDINGS, range=5),
    Card("chance-jail", CardDeck.CHANCE, "陷害卡", CardEffect.JAIL_PLAYER, range=5),
    Card("chance-taxi", CardDeck.CHANCE, "出租车卡", CardEffect.TAXI_MOVE, range=6),
    Card("chance-alliance", CardDeck.CHANCE, "同盟卡", CardEffect.ALLIANCE, range=5, turns=3),
    Card("chance-waiver", CardDeck.CHANCE, "免费卡", CardEffect.RENT_WAIVER, amount=2),
    Card("chance-monster", CardDeck.CHANCE, "怪兽卡", CardEffect.MONSTER, range=5),
    Card("chance-surge", CardDeck.CHANCE, "涨价卡", CardEffect.RENT_SURGE, range=5, turns=3),
    Card("chance-buy", CardDeck.CHANCE, "购地卡", CardEffect.BUY_PROPERTY, range=5),
    Card("chance-freeze", CardDeck.CHANCE, "查封卡", CardEffect.RENT_FREEZE, range=5, turns=2),
    Card("chance-build", CardDeck.CHANCE, "建房卡", CardEffect.BUILD, range=5),
)

# Nuclear card removed from active deck; definition retained for replay backward compatibility.
_DISABLED_CHANCE_CARDS: tuple[Card, ...] = (
    Card("chance-nuclear", CardDeck.CHANCE, "核弹卡", CardEffect.NUCLEAR_RESET),
)

COMMUNITY_CHEST_CARDS: tuple[Card, ...] = (
    Card("community-go", CardDeck.COMMUNITY_CHEST, "前进到起点", CardEffect.MOVE_TO_GO),
    Card(
        "community-bank-error",
        CardDeck.COMMUNITY_CHEST,
        "银行错账",
        CardEffect.RECEIVE_CASH,
        amount=200,
    ),
    Card("community-doctor", CardDeck.COMMUNITY_CHEST, "医药费", CardEffect.PAY_BANK, amount=50),
    Card(
        "community-stock", CardDeck.COMMUNITY_CHEST, "出售股票", CardEffect.RECEIVE_CASH, amount=50
    ),
    Card("community-jail-free", CardDeck.COMMUNITY_CHEST, "出狱卡", CardEffect.GET_OUT_OF_JAIL),
    Card("community-jail", CardDeck.COMMUNITY_CHEST, "入狱卡", CardEffect.GO_TO_JAIL),
    Card(
        "community-holiday",
        CardDeck.COMMUNITY_CHEST,
        "假期基金到期",
        CardEffect.RECEIVE_CASH,
        amount=100,
    ),
    Card("community-refund", CardDeck.COMMUNITY_CHEST, "退税", CardEffect.RECEIVE_CASH, amount=20),
    Card("community-birthday", CardDeck.COMMUNITY_CHEST, "生日", CardEffect.BIRTHDAY, amount=10),
    Card(
        "community-insurance",
        CardDeck.COMMUNITY_CHEST,
        "寿险到期",
        CardEffect.RECEIVE_CASH,
        amount=100,
    ),
    Card(
        "community-hospital", CardDeck.COMMUNITY_CHEST, "医院账单", CardEffect.PAY_BANK, amount=100
    ),
    Card("community-school", CardDeck.COMMUNITY_CHEST, "学费", CardEffect.PAY_BANK, amount=50),
    Card(
        "community-consulting",
        CardDeck.COMMUNITY_CHEST,
        "顾问费",
        CardEffect.RECEIVE_CASH,
        amount=25,
    ),
    Card("community-repairs", CardDeck.COMMUNITY_CHEST, "街道维修", CardEffect.REPAIRS),
    Card(
        "community-pageant",
        CardDeck.COMMUNITY_CHEST,
        "选美亚军",
        CardEffect.RECEIVE_CASH,
        amount=10,
    ),
    Card(
        "community-inheritance",
        CardDeck.COMMUNITY_CHEST,
        "继承遗产",
        CardEffect.RECEIVE_CASH,
        amount=100,
    ),
)

CARDS_BY_ID = {
    card.card_id: card for card in CHANCE_CARDS + _DISABLED_CHANCE_CARDS + COMMUNITY_CHEST_CARDS
}
