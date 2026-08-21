"""Unit tests for context broadcast renderer."""

from __future__ import annotations

import pytest

from monopoly_agent_battle.context.broadcast import (
    EXEMPT,
    WHITELIST,
    UnregisteredEventError,
    render_event,
)
from monopoly_agent_battle.domain.models import GameEvent


def _rendered(event: GameEvent, viewer_id: str | None = None) -> str:
    """Render a whitelisted event and narrow the optional return type for tests."""
    rendered = render_event(event, viewer_id)
    assert rendered is not None
    return rendered


def test_exempt_events_return_none() -> None:
    """Exempt events return None without raising."""
    for event_type in EXEMPT:
        result = render_event(GameEvent(event_type, {}), None)
        assert result is None, f"Exempt event {event_type} should return None"


def test_whitelist_and_exempt_cover_all_engine_events() -> None:
    """Every known engine event type must be in WHITELIST or EXEMPT."""
    all_engine_events = {
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
        "properties_swapped",
        "buildings_swapped",
    }

    covered = WHITELIST | EXEMPT
    assert all_engine_events == covered, f"Mismatch: {all_engine_events ^ covered}"


def test_unregistered_event_raises() -> None:
    """An event type not in WHITELIST or EXEMPT raises UnregisteredEventError."""
    with pytest.raises(UnregisteredEventError, match="totally_unknown_event"):
        render_event(GameEvent("totally_unknown_event", {}), None)


def test_render_deterministic() -> None:
    """Calling render_event twice with the same input returns the same string."""
    event = GameEvent("dice_rolled", {"player_id": "a", "dice": [3, 4]})
    result1 = _rendered(event)
    result2 = _rendered(event)
    assert result1 == result2
    assert result1 == "玩家a掷出3+4=7点。"


def test_card_drawn_chance_observer_hides_card_name() -> None:
    """Chance card drawn: observer sees generic, self sees card name."""
    event = GameEvent(
        "card_drawn",
        {
            "operation_id": 1,
            "player_id": "a",
            "card_id": "chance-waiver",
            "deck": "chance",
        },
    )

    observer_result = _rendered(event, "b")
    assert observer_result == "玩家a抽得一张机会卡。"
    assert "免费卡" not in observer_result

    self_result = _rendered(event, "a")
    assert "免费卡" in self_result
    assert self_result == "玩家a抽得机会卡「免费卡」。"


def test_card_drawn_community_chest_always_public() -> None:
    """Community chest cards are always public."""
    event = GameEvent(
        "card_drawn",
        {
            "operation_id": 1,
            "player_id": "a",
            "card_id": "community-jail-free",
            "deck": "community_chest",
        },
    )

    observer_result = _rendered(event, "b")
    assert "出狱卡" in observer_result
    assert "收入手牌" in observer_result

    self_result = _rendered(event, "a")
    assert "出狱卡" in self_result


def test_card_discarded_observer_hides_card_name() -> None:
    """card_discarded: observer sees deck only, self sees card name."""
    event = GameEvent(
        "card_discarded",
        {"player_id": "a", "card_id": "chance-waiver", "deck": "chance"},
    )

    observer_result = _rendered(event, "b")
    assert observer_result == "玩家a弃置一张机会卡。"
    assert "免费卡" not in observer_result

    self_result = _rendered(event, "a")
    assert "免费卡" in self_result
    assert self_result == "玩家a弃置了机会卡「免费卡」。"


def test_chance_card_stolen_observer_hides_card_name() -> None:
    """chance_card_stolen: observer sees generic, thief/victim see card name."""
    event = GameEvent(
        "chance_card_stolen",
        {
            "player_id": "a",
            "target_player_id": "b",
            "card_id": "chance-tax",
        },
    )

    observer_result = _rendered(event, "c")
    assert "一张机会卡" in observer_result
    assert "查税卡" not in observer_result

    thief_result = _rendered(event, "a")
    assert "查税卡" in thief_result

    victim_result = _rendered(event, "b")
    assert "查税卡" in victim_result


def test_payment_made_to_bank() -> None:
    """payment_made with recipient_id=None shows '银行'."""
    event = GameEvent(
        "payment_made",
        {
            "operation_id": 1,
            "payer_id": "a",
            "recipient_id": None,
            "amount": 50,
            "reason": "tax",
        },
    )

    result = _rendered(event)
    assert "银行" in result
    assert result == "玩家a支付50给银行（原因：税费）。"


def test_payment_made_to_player() -> None:
    """payment_made with recipient_id shows player."""
    event = GameEvent(
        "payment_made",
        {
            "operation_id": 1,
            "payer_id": "a",
            "recipient_id": "b",
            "amount": 100,
            "reason": "rent",
        },
    )

    result = _rendered(event)
    assert "玩家b" in result
    assert result == "玩家a支付100给玩家b（原因：租金）。"


def test_player_jailed_reason_mapping() -> None:
    """player_jailed maps reason to human-readable text."""
    event_doubles = GameEvent("player_jailed", {"player_id": "a", "reason": "third_doubles"})
    result_doubles = _rendered(event_doubles)
    assert "连续三次双骰" in result_doubles

    event_go_to_jail = GameEvent("player_jailed", {"player_id": "a", "reason": "go_to_jail"})
    result_go_to_jail = _rendered(event_go_to_jail)
    assert "踩到入狱格" in result_go_to_jail

    event_card = GameEvent("player_jailed", {"player_id": "a", "reason": "chance-jail"})
    result_card = _rendered(event_card)
    assert "陷害卡" in result_card


def test_jail_released_method_mapping() -> None:
    """jail_released maps method to human-readable text."""
    for method, expected_text in [
        ("doubles", "掷出对子"),
        ("card", "使用出狱卡"),
        ("fine", "缴纳罚款"),
    ]:
        event = GameEvent("jail_released", {"player_id": "a", "method": method})
        result = _rendered(event)
        assert expected_text in result


def test_chance_card_used_with_targets() -> None:
    """chance_card_used includes target description."""
    event_player_target = GameEvent(
        "chance_card_used",
        {
            "player_id": "a",
            "card_id": "chance-tax",
            "target_player_id": "b",
            "target_position": None,
            "target_color_group": None,
        },
    )
    result = _rendered(event_player_target)
    assert "查税卡" in result
    assert "目标：玩家b" in result

    event_position_target = GameEvent(
        "chance_card_used",
        {
            "player_id": "a",
            "card_id": "chance-vacate",
            "target_player_id": None,
            "target_position": 1,
            "target_color_group": None,
        },
    )
    result = _rendered(event_position_target)
    assert "空地卡" in result
    assert "目标：第1格" in result

    event_no_target = GameEvent(
        "chance_card_used",
        {
            "player_id": "a",
            "card_id": "chance-nuclear",
            "target_player_id": None,
            "target_position": None,
            "target_color_group": None,
        },
    )
    result = _rendered(event_no_target)
    assert "核弹卡" in result
    assert "目标" not in result


def test_ongoing_effect_created_with_targets() -> None:
    """ongoing_effect_created includes target/color_group when present."""
    event_with_player = GameEvent(
        "ongoing_effect_created",
        {
            "kind": "alliance",
            "source_player_id": "a",
            "remaining_turns": 3,
            "target_player_id": "b",
            "color_group": None,
        },
    )
    result = _rendered(event_with_player)
    assert "同盟效果创建" in result
    assert "目标：玩家b" in result

    event_with_color = GameEvent(
        "ongoing_effect_created",
        {
            "kind": "rent_surge",
            "source_player_id": "a",
            "remaining_turns": 3,
            "target_player_id": None,
            "color_group": "red",
        },
    )
    result = _rendered(event_with_color)
    assert "涨价效果创建" in result
    assert "颜色组：red" in result


def test_property_events_include_board_names() -> None:
    """Property-related events include board space names."""
    event = GameEvent(
        "property_purchased",
        {"player_id": "a", "position": 1, "price": 60},
    )
    result = _rendered(event)
    assert "Mediterranean Avenue" in result or "第1格" in result


def test_board_position_fallback() -> None:
    """Unknown board positions fall back to numeric display."""
    event = GameEvent(
        "player_moved",
        {"player_id": "a", "from": 0, "to": 999, "steps": 999},
    )
    result = _rendered(event)
    assert "第999格" in result
