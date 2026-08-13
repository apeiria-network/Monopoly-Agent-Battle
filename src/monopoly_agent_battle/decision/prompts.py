"""Render player decision requests into stable, human-readable prompts."""

from __future__ import annotations

import json

from monopoly_agent_battle.decision.models import DecisionRequest

PLAYER_INSTRUCTION = """你正在代表玩家「{player_id}」参与一局大富翁。

你必须仅根据下方提供的信息，在“合法候选操作”中选择一个操作。不得自行发明操作、修改参数、假设未提供的信息或请求隐藏信息。请选择更有利于本玩家长期净资产和胜利概率的操作。"""


def render_decision_prompt(request: DecisionRequest) -> str:
    """Render the complete baseline/emperor decision message from a request."""
    context = {
        "decision_id": request.decision_id,
        "game_id": request.game_id,
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
    return "\n\n".join(
        (
            PLAYER_INSTRUCTION.format(player_id=request.player_id),
            "## 决策上下文\n" + _json(context),
            "## 可见游戏状态\n" + _json(request.visible_state),
            "## 当前决策问题\n" + request.question,
            "## 合法候选操作\n" + _json(options),
            "## 输出要求\n"
            "只输出一个 JSON 对象，不要使用 Markdown 代码块，也不要附加额外文本。\n"
            + _json(response_example),
        )
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
