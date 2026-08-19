"""Render player decision requests into stable, human-readable prompts."""

from __future__ import annotations

import json
from typing import Any, cast

from monopoly_agent_battle.context.rules import load_game_rules
from monopoly_agent_battle.decision.models import DecisionKind, DecisionRequest

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

_OUTPUT_GUIDE = (
    "只输出一个 JSON 对象，不要使用 Markdown 代码块，也不要附加额外文本。\n"
    "- `selected_option` 为 JSON 对象：`option` 填候选的 option_id，`target` 填该选项所需的"
    "待指定目标。\n"
    "- 单目标（玩家id/目标格子编号/颜色组代号/机会卡id）用标量"
    '（`"b"` / `3` / `"brown"` / `"chance-waiver"`）；双目标（换地/换屋）用对象 '
    '`{"swap_in_position": 1, "swap_out_position": 3}`。\n'
    "- 不需要目标的选项若模型填了 `target`，按忽略处理。"
)


def render_role(request: DecisionRequest) -> str:
    """Segment 1: role and goal introduction (system prompt)."""
    visible: dict[str, Any] = request.visible_state
    return PLAYER_INSTRUCTION.format(
        player_id=request.player_id,
        seat=visible["your_state"]["seat"],
    )


def render_rules() -> str:
    """Segment 2: game rules text loaded from ``doc/monopoly_rules_basic.md``."""
    return "## 游戏规则\n" + load_game_rules().strip()


def render_system_prompt(request: DecisionRequest) -> str:
    """Segments 1+2 merged for the single system message."""
    return render_role(request) + "\n\n" + render_rules()


def render_situation(visible: dict[str, Any]) -> str:
    """Segments 5+6+7: your state, other players, board table."""
    return "## 当前局面\n" + _render_situation(visible)


def render_decision_and_options(request: DecisionRequest) -> str:
    """Segments 8+9+10 combined (the "14" snapshot for past decisions)."""
    visible: dict[str, Any] = request.visible_state
    options = [
        {
            "option_id": option.option_id,
            "title": option.title,
            "preview": option.preview,
            "response_format": _render_response_format(option.response_format, option.option_id),
        }
        for option in request.options
    ]
    return "\n\n".join(
        (
            "## 当前决策\n" + _render_decision(request, visible),
            "## 合法候选操作\n" + _json(options),
            "## 输出要求\n" + _OUTPUT_GUIDE,
        )
    )


def render_current_user_message(request: DecisionRequest) -> str:
    """Segments 5-10 merged for the final user message of a prompt."""
    visible: dict[str, Any] = request.visible_state
    return render_situation(visible) + "\n\n" + render_decision_and_options(request)


def render_decision_prompt(request: DecisionRequest) -> str:
    """Legacy single-string entry point retained for 4A/4B tests.

    Returns segments 1 + 5-10 concatenated (no segment 2/3/4) — matches the
    prompt shape accepted for the Stage 3 human review. Stage 4C conversations
    should use the messages produced by ``compose_prompt`` instead.
    """
    visible: dict[str, Any] = request.visible_state
    options = [
        {
            "option_id": option.option_id,
            "title": option.title,
            "preview": option.preview,
            "response_format": _render_response_format(option.response_format, option.option_id),
        }
        for option in request.options
    ]
    return "\n\n".join(
        (
            PLAYER_INSTRUCTION.format(
                player_id=request.player_id,
                seat=visible["your_state"]["seat"],
            ),
            "## 当前局面\n" + _render_situation(visible),
            "## 当前决策\n" + _render_decision(request, visible),
            "## 合法候选操作\n" + _json(options),
            "## 输出要求\n" + _OUTPUT_GUIDE,
        )
    )


def _render_response_format(value: object, option_id: str) -> object:
    """Fill the selected candidate ID into its wording-owned response template."""
    if isinstance(value, str):
        return value.replace("{option_id}", option_id)
    if isinstance(value, dict):
        document = cast(dict[str, object], value)
        return {key: _render_response_format(item, option_id) for key, item in document.items()}
    raise AssertionError("response format wording must be a JSON object or string")


def _render_decision(request: DecisionRequest, visible: dict[str, Any]) -> str:
    kind = request.kind
    if kind is DecisionKind.JAIL:
        jail = visible["jail"]
        remaining = _MAX_JAIL_ROLL_ATTEMPTS - jail["roll_attempts"]
        return (
            "你可以选择掷出双骰或支付 50 现金出狱。"
            f"你还有 {remaining} / {_MAX_JAIL_ROLL_ATTEMPTS} 次掷骰子，"
            "如果掷骰子判断全部失败，则立刻支付 50 现金并出狱。"
        )
    if kind is DecisionKind.PAYMENT_RESOLUTION:
        due = visible["payment_due"]
        recipient = due["recipient_id"] or "银行"
        return (
            f"你有一笔 {due['amount']} 元款项需支付（{due['reason']}，收款方 {recipient}），"
            "但现金不足，无法自动支付。\n请出售建筑或抵押地产来筹足款项。"
        )
    if kind is DecisionKind.FORCED_DISCARD:
        count = len(visible["your_state"]["chance_cards"])
        return f"当前持有 {count} 张机会卡，超过 4 张上限，必须弃置到 4 张后才能结束回合。"
    if kind is DecisionKind.THEFT_CARD_SELECTION:
        theft = visible["theft_selection"]
        cards = "、".join(card["card_id"] for card in theft["target_chance_cards"])
        return (
            f"你对玩家 {theft['target_player_id']} 使用抢夺卡成功。"
            f"对方持有以下机会卡：{cards}。\n请选择其中一张拿走。"
        )
    if kind is DecisionKind.ASSET_MANAGEMENT:
        return "现在是你的资产管理阶段，你可以出售建筑、抵押或赎回地产、使用机会卡，或结束本回合。"
    raise AssertionError(f"unknown decision kind: {kind}")


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
    return json.dumps(value, ensure_ascii=False, indent=2)


_OPTIONS_HEADER = "## 合法候选操作\n"


def options_from_prompt(prompt: str) -> list[dict[str, object]]:
    """Parse the rendered candidate options out of a decision prompt.

    Used by deterministic mock clients; the prompt format is owned here, so any
    format change to the candidates section must keep this parser in sync.
    """
    start = prompt.index(_OPTIONS_HEADER) + len(_OPTIONS_HEADER)
    end = prompt.find("\n## ", start)
    section = prompt[start : end if end != -1 else len(prompt)]
    document = json.loads(section)
    if not isinstance(document, list):
        raise ValueError("rendered decision options must be a JSON array")
    return cast(list[dict[str, object]], document)
