"""Fixed-sentence event broadcaster for Agent history context (Stage 4B).

render_event(event, viewer_id) -> str | None

- Whitelist events return a Chinese sentence.
- Exempt events return None.
- Any event_type not in WHITELIST or EXEMPT raises UnregisteredEventError.
- Same input always produces the same output (deterministic, no LLM).
- BROADCAST_VERSION participates in config_hash via GameConfig.sentence_template_version.
"""

from __future__ import annotations

from monopoly_agent_battle.domain.models import CardDeck, GameEvent
from monopoly_agent_battle.game.board_data.classic_us_40 import BOARD
from monopoly_agent_battle.game.cards.classic_cards import CARDS_BY_ID

BROADCAST_VERSION: str = "v1"

_BOARD_NAMES: dict[int, str] = {space.position: space.name for space in BOARD}
_CARD_NAMES: dict[str, str] = {card.card_id: card.name for card in CARDS_BY_ID.values()}
_CARD_DECKS: dict[str, str] = {card.card_id: card.deck.value for card in CARDS_BY_ID.values()}


class UnregisteredEventError(Exception):
    """Raised when an event_type has no template and is not explicitly exempt."""


WHITELIST: frozenset[str] = frozenset(
    {
        "dice_rolled",
        "player_moved",
        "go_salary_collected",
        "property_purchased",
        "property_purchased_from_player",
        "payment_made",
        "player_bankrupt",
        "player_jailed",
        "jail_released",
        "jail_roll_failed",
        "turn_started",
        "turn_ended",
        "card_drawn",
        "card_discarded",
        "chance_card_used",
        "card_die_rolled",
        "chance_card_stolen",
        "building_added",
        "building_sold",
        "building_level_changed",
        "property_mortgaged",
        "mortgage_redeemed",
        "property_reset",
        "property_vacated",
        "rent_waiver_used",
        "rent_waivers_granted",
        "cash_received",
        "cash_tax_transferred",
        "cash_equalized",
        "ongoing_effect_created",
        "ongoing_effect_expired",
        "automatic_build_skipped_insufficient_cash",
        "game_finished",
    }
)

EXEMPT: frozenset[str] = frozenset(
    {
        "chance_card_discard_required",
        "chance_card_hand_limit_resolved",
        "jail_wait_completed",
        "settlement_operation_queued",
        "settlement_operation_completed",
        "settlement_operation_cancelled",
        "payment_required",
        "rent_frozen",
        "card_held",
        "chance_card_theft_selection_required",
        "ongoing_effect_reset",
        "ongoing_effect_advanced",
        "space_landed",
        "alliance_rent_rounding_adjusted",
        "cash_rounding_adjusted",
        # TODO(4B-review): properties_swapped / buildings_swapped not in §五 whitelist;
        # add sentences during the post-4B §五 review session.
        "properties_swapped",
        "buildings_swapped",
    }
)


def render_event(event: GameEvent, viewer_id: str | None) -> str | None:
    """Return broadcast sentence for viewer_id, or None if exempt.

    Raises UnregisteredEventError for event_types not in WHITELIST or EXEMPT.
    viewer_id=None means a global/public view (same as non-player observer).
    """
    if event.event_type in EXEMPT:
        return None
    if event.event_type not in WHITELIST:
        raise UnregisteredEventError(
            f"Event type '{event.event_type}' is not registered in WHITELIST or EXEMPT"
        )

    payload = event.payload

    # Simple events with no viewer-dependent logic
    if event.event_type == "dice_rolled":
        dice = payload["dice"]
        # Engine emits dice as tuple, convert for indexing
        if isinstance(dice, tuple):
            d1, d2 = int(dice[0]), int(dice[1])  # type: ignore[arg-type]
        else:
            assert isinstance(dice, list)
            d1, d2 = int(dice[0]), int(dice[1])  # type: ignore[arg-type]
        player_id = str(payload["player_id"])
        return f"玩家{player_id}掷出{d1}+{d2}={d1 + d2}点。"

    if event.event_type == "player_moved":
        to_pos = int(payload["to"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(to_pos, str(to_pos))
        return f"玩家{payload['player_id']}移动到第{to_pos}格（{name}）。"

    if event.event_type == "go_salary_collected":
        return f"玩家{payload['player_id']}经过起点，获得{payload['amount']}资金。"

    if event.event_type == "property_purchased":
        pos = int(payload["position"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(pos, str(pos))
        return f"玩家{payload['player_id']}购买第{pos}格（{name}），支付{payload['price']}。"

    if event.event_type == "property_purchased_from_player":
        pos = int(payload["position"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(pos, str(pos))
        buyer = str(payload["player_id"])
        seller = str(payload["owner_id"])
        price = int(payload["price"])  # type: ignore[arg-type]
        return f"玩家{buyer}向玩家{seller}购买第{pos}格（{name}），支付{price}。"

    if event.event_type == "payment_made":
        recipient = "银行" if payload["recipient_id"] is None else f"玩家{payload['recipient_id']}"
        payer = str(payload["payer_id"])
        amount = int(payload["amount"])  # type: ignore[arg-type]
        reason = str(payload["reason"])
        # Translate common payment reasons
        reason_map = {"rent": "租金", "tax": "税费", "jail_fine": "监狱罚款"}
        reason_text = reason_map.get(reason, _CARD_NAMES.get(reason, reason))
        return f"玩家{payer}支付{amount}给{recipient}（原因：{reason_text}）。"

    if event.event_type == "player_bankrupt":
        return f"玩家{payload['player_id']}破产出局。"

    if event.event_type == "player_jailed":
        reason = str(payload["reason"])
        reason_text = (
            "连续三次双骰"
            if reason == "third_doubles"
            else "踩到入狱格"
            if reason == "go_to_jail"
            else _CARD_NAMES.get(reason, reason)
        )
        return f"玩家{payload['player_id']}被送进监狱（原因：{reason_text}）。"

    if event.event_type == "jail_released":
        method = str(payload["method"])
        method_map = {"doubles": "掷出对子", "card": "使用出狱卡", "fine": "缴纳罚款"}
        method_desc = method_map.get(method, method)
        return f"玩家{payload['player_id']}出狱（方式：{method_desc}）。"

    if event.event_type == "jail_roll_failed":
        return f"玩家{payload['player_id']}掷骰未出狱。"

    if event.event_type == "turn_started":
        return f"玩家{payload['player_id']}开始行动回合。"

    if event.event_type == "turn_ended":
        return f"玩家{payload['player_id']}结束行动回合。"

    if event.event_type == "card_drawn":
        player_id = str(payload["player_id"])
        card_id = str(payload["card_id"])
        deck = str(payload["deck"])
        card_name = _CARD_NAMES.get(card_id, card_id)

        if deck == CardDeck.CHANCE.value:
            # Chance card: observer sees generic, self sees card name
            if viewer_id == player_id:
                return f"玩家{player_id}抽得机会卡「{card_name}」。"
            return f"玩家{player_id}抽得一张机会卡。"
        # Community chest: always public
        card_obj = CARDS_BY_ID.get(card_id)
        is_jail_free = card_obj and card_obj.effect.value == "get_out_of_jail"
        held_text = "收入手牌" if is_jail_free else "即时生效"
        return f"玩家{player_id}抽得一张公益基金卡「{card_name}」（{held_text}）。"

    if event.event_type == "card_discarded":
        player_id = str(payload["player_id"])
        card_id = str(payload["card_id"])
        deck = str(payload["deck"])
        deck_label = "机会" if deck == CardDeck.CHANCE.value else "公益基金"
        if payload.get("reason") == "played":
            return None
        reason = str(payload.get("reason", "hand_limit"))
        if reason == "bankruptcy":
            return f"玩家{player_id}破产，{deck_label}卡「{card_id}」进入弃牌堆。"
        if viewer_id == player_id:
            card_name = _CARD_NAMES.get(card_id, card_id)
            return f"玩家{player_id}弃置了{deck_label}卡「{card_name}」。"
        return f"玩家{player_id}弃置一张{deck_label}卡。"

    if event.event_type == "chance_card_used":
        player_id = str(payload["player_id"])
        card_id = str(payload["card_id"])
        card_name = _CARD_NAMES.get(card_id, card_id)

        target_parts: list[str] = []
        if payload.get("target_player_id"):
            target_parts.append(f"，目标：玩家{payload['target_player_id']}")
        if payload.get("target_position") is not None:
            pos = int(payload["target_position"])  # type: ignore[arg-type]
            name = _BOARD_NAMES.get(pos, str(pos))
            target_parts.append(f"，目标：第{pos}格（{name}）")
        if payload.get("target_color_group"):
            target_parts.append(f"，目标颜色组：{payload['target_color_group']}")

        target_desc = "".join(target_parts)
        return f"玩家{player_id}使用了机会卡「{card_name}」{target_desc}。"

    if event.event_type == "card_die_rolled":
        return f"玩家{payload['player_id']}抢夺掷骰结果为{payload['die']}。"

    if event.event_type == "chance_card_stolen":
        player_id = str(payload["player_id"])
        target_id = str(payload["target_player_id"])
        card_id = str(payload["card_id"])

        if viewer_id in (player_id, target_id):
            card_name = _CARD_NAMES.get(card_id, card_id)
            return f"玩家{player_id}从玩家{target_id}手中拿走了机会卡「{card_name}」。"
        return f"玩家{player_id}从玩家{target_id}手中拿走了一张机会卡。"

    if event.event_type == "building_added":
        pos = int(payload["position"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(pos, str(pos))
        player_id = str(payload["player_id"])
        cost = int(payload["cost"])  # type: ignore[arg-type]
        return f"玩家{player_id}在第{pos}格（{name}）自动加建房屋，花费{cost}。"

    if event.event_type == "building_sold":
        pos = int(payload["position"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(pos, str(pos))
        return f"玩家{payload['player_id']}出售第{pos}格（{name}）房屋，获得{payload['amount']}。"

    if event.event_type == "building_level_changed":
        pos = int(payload["position"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(pos, str(pos))
        level = int(payload["building_level"])  # type: ignore[arg-type]
        reason = str(payload["reason"])
        reason_text = _CARD_NAMES.get(reason, reason)
        return f"第{pos}格（{name}）房屋层数变为{level}（原因：{reason_text}）。"

    if event.event_type == "property_mortgaged":
        pos = int(payload["position"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(pos, str(pos))
        return f"玩家{payload['player_id']}抵押第{pos}格（{name}），获得{payload['amount']}。"

    if event.event_type == "mortgage_redeemed":
        pos = int(payload["position"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(pos, str(pos))
        return f"玩家{payload['player_id']}赎回第{pos}格（{name}）抵押，支付{payload['amount']}。"

    if event.event_type == "property_reset":
        pos = int(payload["position"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(pos, str(pos))
        reason = str(payload["reason"])
        reason_text = _CARD_NAMES.get(reason, reason)
        return f"第{pos}格（{name}）地产被清空（原因：{reason_text}）。"

    if event.event_type == "property_vacated":
        pos = int(payload["position"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(pos, str(pos))
        owner_id = str(payload["owner_id"])
        price = int(payload["price"])  # type: ignore[arg-type]
        return f"玩家{owner_id}的第{pos}格（{name}）被清退，{owner_id}获得{price}。"

    if event.event_type == "rent_waiver_used":
        pos = int(payload["position"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(pos, str(pos))
        player_id = str(payload["player_id"])
        remaining = int(payload["remaining_waivers"])  # type: ignore[arg-type]
        return f"玩家{player_id}使用免租权免除第{pos}格（{name}）租金（剩余{remaining}次）。"

    if event.event_type == "rent_waivers_granted":
        return f"玩家{payload['player_id']}获得{payload['amount']}次免租权。"

    if event.event_type == "cash_received":
        reason = str(payload["reason"])
        reason_text = _CARD_NAMES.get(reason, reason)
        return f"玩家{payload['player_id']}获得{payload['amount']}资金（原因：{reason_text}）。"

    if event.event_type == "cash_tax_transferred":
        player_id = str(payload["player_id"])
        target_id = str(payload["target_player_id"])
        amount = int(payload["amount"])  # type: ignore[arg-type]
        return f"玩家{player_id}通过查税卡获得{amount}资金，玩家{target_id}失去{amount}资金。"

    if event.event_type == "cash_equalized":
        player_id = str(payload["player_id"])
        target_id = str(payload["target_player_id"])
        share = int(payload["player_cash"])  # type: ignore[arg-type]
        return f"玩家{player_id}与玩家{target_id}资金均分，各得{share}。"

    if event.event_type == "ongoing_effect_created":
        kind = str(payload["kind"])
        source = str(payload["source_player_id"])
        turns = int(payload["remaining_turns"])  # type: ignore[arg-type]
        # Translate effect kinds
        kind_map = {"alliance": "同盟", "rent_surge": "涨价", "rent_freeze": "查封"}
        kind_text = kind_map.get(kind, kind)
        parts: list[str] = [f"{kind_text}效果创建（来源{source}，剩余{turns}回合"]

        if payload.get("target_player_id"):
            parts.append(f"，目标：玩家{payload['target_player_id']}")
        if payload.get("color_group"):
            parts.append(f"，颜色组：{payload['color_group']}")

        parts.append("）。")
        return "".join(parts)

    if event.event_type == "ongoing_effect_expired":
        kind = str(payload["kind"])
        # Translate effect kinds
        kind_map = {"alliance": "同盟", "rent_surge": "涨价", "rent_freeze": "查封"}
        kind_text = kind_map.get(kind, kind)
        if payload.get("reason") == "bankruptcy":
            return f"{kind_text}效果因玩家破产而解除（来源{payload['source_player_id']}）。"
        return f"{kind_text}效果到期（来源{payload['source_player_id']}）。"

    if event.event_type == "automatic_build_skipped_insufficient_cash":
        pos = int(payload["position"])  # type: ignore[arg-type]
        name = _BOARD_NAMES.get(pos, str(pos))
        return f"玩家{payload['player_id']}现金不足，第{pos}格（{name}）不自动加建。"

    if event.event_type == "game_finished":
        reason = str(payload["reason"])
        # Translate game finish reasons
        reason_map = {"round_limit": "回合数上限"}
        reason_text = reason_map.get(reason, reason)
        return f"游戏结束（原因：{reason_text}）。"

    # Should never reach here if WHITELIST is properly maintained
    raise UnregisteredEventError(f"Whitelist event '{event.event_type}' has no template")
