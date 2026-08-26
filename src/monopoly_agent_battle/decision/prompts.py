"""Render player decision requests into stable, human-readable prompts."""

from __future__ import annotations

import json
from typing import Any, cast

from monopoly_agent_battle.context.rules import load_game_rules
from monopoly_agent_battle.decision.models import DecisionKind, DecisionRequest
from monopoly_agent_battle.game.board_data.classic_us_40 import (
    COLOR_GROUPS,
    RAILROAD_RENTS,
)

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


def render_role(request: DecisionRequest, role_instruction: str | None = None) -> str:
    """Segment 1: player identity, goal, and an optional court-role instruction."""
    role = PLAYER_INSTRUCTION.format(
        player_id=request.player_id,
        seat=cast(dict[str, Any], request.visible_state)["your_state"]["seat"],
    )
    return role if role_instruction is None else role + "\n\n" + role_instruction


def render_rules() -> str:
    """Segment 2: game rules text loaded from ``doc/monopoly_rules_basic.md``.

    The markdown file itself carries the static board reference (§十一), so no
    programmatic rendering is needed here — the file is the single source of
    truth for both the human-facing and Agent-facing rules text.
    """
    return load_game_rules().strip()


def render_system_prompt(
    request: DecisionRequest,
    *,
    role_instruction: str | None = None,
    output_guide: str | None = None,
) -> str:
    """Render segments 1-3, with optional role-specific segment-1 text."""
    return "\n\n".join(
        (
            render_role(request, role_instruction),
            render_rules(),
            "## 输出要求\n" + (output_guide or _OUTPUT_GUIDE),
        )
    )


def render_situation(visible: dict[str, Any]) -> str:
    """Segments 5+6+7: your state, other players, board table."""
    return "## 当前局面\n" + _render_situation(visible)


def render_decision_and_options(request: DecisionRequest) -> str:
    """Segments 8+9 for the current dynamic user message."""
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
        )
    )


def render_decision_question(request: DecisionRequest) -> str:
    """Segment 8 only — the "## 当前决策" text without candidates.

    Used as the segment-4 replay for decisions the AI has already answered:
    candidates are dropped because the AI has already committed to an answer,
    while the fixed output contract already lives in the system message.
    """
    visible: dict[str, Any] = request.visible_state
    return "## 当前决策\n" + _render_decision(request, visible)


def render_current_user_message(request: DecisionRequest) -> str:
    """Segments 5-9 merged for the final dynamic user message."""
    visible: dict[str, Any] = request.visible_state
    return render_situation(visible) + "\n\n" + render_decision_and_options(request)


def render_decision_prompt(request: DecisionRequest) -> str:
    """Return a compatibility single-string view of the current request.

    The view preserves the Stage 4C ordering: role, rules and fixed output
    contract precede the dynamic situation, decision and candidate options.
    Stage 4C conversations should instead use the messages from
    ``compose_prompt``.
    """
    return render_system_prompt(request) + "\n\n" + render_current_user_message(request)


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
    """Render only owned spaces with dynamic state; static data lives in segment 2."""
    color_effects = _color_group_effects(visible["ongoing_effects"])
    board_by_position: dict[int, dict[str, Any]] = {
        int(space["position"]): space for space in visible["board"]
    }
    owned = [space for space in visible["board"] if space.get("owner_id")]
    if not owned:
        return "（当前无地产被拥有。）"
    ownership_by_kind = _ownership_by_kind(visible["board"])
    jailed_players = _jailed_player_ids(visible)
    rows = [
        "| 格 | 类型 | 所有者 | 建筑 | 状态 | 当前租金 |",
        "|---|---|---|---|---|---|",
    ]
    rows.extend(
        _board_row(space, color_effects, ownership_by_kind, board_by_position, jailed_players)
        for space in owned
    )
    rows.append("（未列出的地产均无主。）")
    return "\n".join(rows)


def _jailed_player_ids(visible: dict[str, Any]) -> set[str]:
    """Player ids currently in jail; C-014 says such owners collect 0 rent."""
    result: set[str] = set()
    me = visible["your_state"]
    if me["jail_status"] != "free":
        result.add(str(me["player_id"]))
    for other in visible["players"]:
        if other["jail_status"] != "free":
            result.add(str(other["player_id"]))
    return result


def _ownership_by_kind(board: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Count owned railroads/utilities per player (needed for current-rent maths)."""
    result: dict[str, dict[str, int]] = {}
    for space in board:
        owner = space.get("owner_id")
        if not owner:
            continue
        kind = str(space["kind"])
        result.setdefault(str(owner), {}).setdefault(kind, 0)
        result[str(owner)][kind] += 1
    return result


def _board_row(
    space: dict[str, Any],
    color_effects: dict[str, dict[str, int]],
    ownership_by_kind: dict[str, dict[str, int]],
    board_by_position: dict[int, dict[str, Any]],
    jailed_players: set[str],
) -> str:
    is_street = space["kind"] == "street"
    cells = [
        str(space["position"]),
        _SPACE_KIND_CN[space["kind"]],
        space["owner_id"] or "-",
        str(space["building_level"]) if is_street else "-",
        _space_status(space, color_effects),
        _current_rent(space, color_effects, ownership_by_kind, board_by_position, jailed_players),
    ]
    return "| " + " | ".join(cells) + " |"


def _current_rent(
    space: dict[str, Any],
    color_effects: dict[str, dict[str, int]],
    ownership_by_kind: dict[str, dict[str, int]],
    board_by_position: dict[int, dict[str, Any]],
    jailed_players: set[str],
) -> str:
    """Return the rent the current owner would collect on a stopover.

    Mortgaged / rent-frozen / owner-in-jail spaces return "0". Utilities depend
    on the roller's dice, so return a formula string instead of a fixed value.
    """
    if space["mortgaged"]:
        return "0（抵押）"
    color_group = space.get("color_group")
    effects = color_effects.get(color_group) if color_group else None
    if effects and "rent_freeze" in effects:
        return "0（查封）"
    owner_id = str(space["owner_id"])
    if owner_id in jailed_players:
        return "0（业主入狱）"
    kind = space["kind"]
    if kind == "street":
        base = int(space["rents"][int(space["building_level"])])
        if space["building_level"] == 0:
            group_positions = COLOR_GROUPS.get(str(color_group), ())
            if group_positions and all(
                board_by_position.get(pos, {}).get("owner_id") == owner_id
                for pos in group_positions
            ):
                base *= 2
        if effects and "rent_surge" in effects:
            base *= 2
        return str(base)
    if kind == "railroad":
        count = ownership_by_kind.get(str(owner_id), {}).get("railroad", 0)
        return str(RAILROAD_RENTS[count - 1]) if 1 <= count <= 4 else "-"
    if kind == "utility":
        count = ownership_by_kind.get(str(owner_id), {}).get("utility", 0)
        multiplier = 10 if count == 2 else 4
        return f"{multiplier}×骰点"
    return "-"


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
