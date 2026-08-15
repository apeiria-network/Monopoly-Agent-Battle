"""Render player decision requests into stable, human-readable prompts."""

from __future__ import annotations

import json
from typing import Any

from monopoly_agent_battle.decision.models import DecisionRequest

PLAYER_INSTRUCTION = """你正在代表玩家「{player_id}」（座位 {seat}）参与一局大富翁。
你的目标：在回合上限结束时拥有最高净资产。
净资产 = 现金 + 未抵押地产的购买价 + 所有已建成建筑的价值（房屋单价 × 建筑层数）。
你只能从下方“合法候选操作”中选择一个，不得编造操作、修改参数、假设未提供的信息或请求隐藏信息。"""

_SPACE_KIND_CN = {
    "go": "起点",
    "street": "街道",
    "railroad": "铁路",
    "utility": "公共事业",
    "tax": "税",
    "chance": "机会",
    "community_chest": "社区基金",
    "jail": "监狱/探监",
    "free_parking": "免费停车",
    "go_to_jail": "入狱",
}

_MAX_JAIL_ROLL_ATTEMPTS = 3


def render_decision_prompt(request: DecisionRequest) -> str:
    """Render the complete decision message from a request."""
    context = {
        "complete_rounds": request.complete_rounds,
        "player_id": request.player_id,
        "phase": request.phase,
        "kind": request.kind.value,
    }
    options = [
        {
            "option_id": option.option_id,
            "summary": option.summary,
            "effect_preview": option.effect_preview,
            "is_default": option.is_default,
        }
        for option in request.options
    ]
    response_example = {
        "selected_option": "<合法候选项的 option_id>",
        "reasoning": "<简短、可审计的决策理由>",
    }
    visible: dict[str, Any] = request.visible_state
    return "\n\n".join(
        (
            PLAYER_INSTRUCTION.format(
                player_id=request.player_id,
                seat=visible["your_state"]["seat"],
            ),
            "## 决策上下文\n" + _json(context),
            "## 当前局面\n" + _render_situation(visible),
            "## 当前决策问题\n" + request.question,
            "## 合法候选操作\n" + _json(options),
            "## 输出要求\n"
            "只输出一个 JSON 对象，不要使用 Markdown 代码块，也不要附加额外文本。\n"
            + _json(response_example),
        )
    )


def _render_situation(visible: dict[str, Any]) -> str:
    """Render the situation overview and each player's natural-language state."""
    turn = visible["turn"]
    me = visible["your_state"]
    board = {space["position"]: space for space in visible["board"]}

    lines = [
        f"当前为第 {turn['complete_rounds']} 回合，"
        f"处于玩家「{turn['current_player_id']}」的行动回合。",
        "",
        "你的状态",
        f"现金：{me['cash']}",
        f"位置：{_space_location(me['position'], board)}",
        f"持有机会卡：{_card_ids(me['chance_cards'])}",
        f"持有出狱卡数量：{len(me['community_get_out_of_jail_cards'])}",
        f"持有地产：{_property_locations(me['property_positions'], board)}",
    ]
    alliance = _alliance_effects(visible, me["player_id"])
    if alliance:
        lines.append(f"持续效果：{alliance}")
    if me["jail_status"] != "free":
        lines.append(f"剩余监狱回合数：{_MAX_JAIL_ROLL_ATTEMPTS - me['jail_roll_attempts']}")

    lines.extend(["", "其他玩家状态", _render_other_players(visible, board)])

    lines.extend(["", "棋盘状态", _render_board(visible)])

    remaining = {
        key: value for key, value in visible.items() if key in {"ongoing_effects", "current_space"}
    }
    lines.extend(["", "其余可见状态（暂未自然语言化）：", _json(remaining)])
    return "\n".join(lines)


def _render_other_players(visible: dict[str, Any], board: dict[int, dict[str, Any]]) -> str:
    blocks: list[str] = []
    for other in visible["players"]:
        lines = [
            f"玩家「{other['player_id']}」",
            f"现金：{other['cash']}",
            f"位置：{_space_location(other['position'], board)}",
            f"持有机会卡数量：{other['chance_card_count']}",
            f"持有出狱卡数量：{other['community_get_out_of_jail_card_count']}",
            f"持有地产：{_property_locations(other['property_positions'], board)}",
        ]
        alliance = _alliance_effects(visible, other["player_id"])
        if alliance:
            lines.append(f"持续效果：{alliance}")
        if other["jail_status"] != "free":
            lines.append(f"剩余监狱回合数：{_MAX_JAIL_ROLL_ATTEMPTS - other['jail_roll_attempts']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "无"


def _render_board(visible: dict[str, Any]) -> str:
    color_effects = _color_group_effects(visible["ongoing_effects"])
    rows = [
        "| 格 | 名称 | 类型 | 颜色组 | 所有者 | 建筑 | 地块价格 | 房屋单价 | "
        "租金（无房 / 1房 / 2房 / 3房 / 4房 / 酒店） | 状态 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    rows.extend(_board_row(space, color_effects) for space in visible["board"])
    return "\n".join(rows)


def _board_row(space: dict[str, Any], color_effects: dict[str, dict[str, int]]) -> str:
    is_street = space["kind"] == "street"
    cells = [
        str(space["position"]),
        space["name"],
        _SPACE_KIND_CN[space["kind"]],
        space["color_group"] or "-",
        space["owner_id"] or "-",
        str(space["building_level"]) if is_street else "-",
        str(space["price"]) if space["price"] is not None else "-",
        str(space["building_cost"]) if space["building_cost"] is not None else "-",
        _rents_text(space["rents"]),
        _space_status(space, color_effects),
    ]
    return "| " + " | ".join(cells) + " |"


def _space_status(space: dict[str, Any], color_effects: dict[str, dict[str, int]]) -> str:
    statuses: list[str] = []
    if space["mortgaged"]:
        statuses.append("抵押")
    effects = color_effects.get(space["color_group"])
    if effects:
        if "rent_freeze" in effects:
            statuses.append(f"查封（剩余 {effects['rent_freeze']} 回合）")
        if "rent_surge" in effects:
            statuses.append(f"涨价（剩余 {effects['rent_surge']} 回合）")
    return "、".join(statuses) if statuses else "-"


def _color_group_effects(effects: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for effect in effects:
        color = effect["color_group"]
        if color is None:
            continue
        if color not in result:
            result[color] = {}
        result[color][effect["kind"]] = effect["remaining_turns"]
    return result


def _rents_text(rents: list[int]) -> str:
    return " / ".join(str(rent) for rent in rents) if rents else "-"


def _space_location(position: int, board: dict[int, dict[str, Any]]) -> str:
    space = board[position]
    return f"格子 {position}（{space['name']}，{_SPACE_KIND_CN[space['kind']]}）"


def _card_ids(cards: list[dict[str, Any]]) -> str:
    return "、".join(card["card_id"] for card in cards) or "无"


def _property_locations(positions: list[int], board: dict[int, dict[str, Any]]) -> str:
    if not positions:
        return "无"
    return "；".join(f"格子 {position}（{board[position]['name']}）" for position in positions)


def _alliance_effects(visible: dict[str, Any], player_id: str) -> str:
    parts: list[str] = []
    for effect in visible["ongoing_effects"]:
        if effect["kind"] != "alliance":
            continue
        if player_id not in {effect["source_player_id"], effect["target_player_id"]}:
            continue
        partner = (
            effect["target_player_id"]
            if effect["source_player_id"] == player_id
            else effect["source_player_id"]
        )
        parts.append(
            f"同盟效果剩余 {effect['remaining_turns']} 回合，期间与玩家「{partner}」平分收入"
        )
    return "；".join(parts)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
