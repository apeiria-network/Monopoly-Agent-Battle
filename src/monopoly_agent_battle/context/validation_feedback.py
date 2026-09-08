"""Validation-failure feedback template for Stage 4C conversation retries.

The feedback text is produced by ``build_feedback(validation, request)``. It
maps each validation ``error_category`` to a fixed Chinese template and,
where useful, appends context-dependent data (list of legal option ids for
``invalid_option``; per-field legal-value list for ``invalid_target`` and
``missing_target``). The resulting string is stored on ``AgentConversation``
as the user side of an ``ErrorEntry`` and replayed in segment 4 for the
remainder of the turn.
"""

from __future__ import annotations

import json

from monopoly_agent_battle.decision.models import (
    DecisionOption,
    DecisionRequest,
    DecisionValidation,
    OptionTarget,
)
from monopoly_agent_battle.decision.protocol import strip_code_fence


def build_feedback(validation: DecisionValidation, request: DecisionRequest) -> str:
    """Return the user-facing validation-failure message for a retry."""
    category = validation.error_category
    if category == "not_json":
        return "Error: 决策回复必须是一个JSON"
    if category == "missing_reason":
        return "Error: 回复缺少必填的 reason 字段，reason 必须是字符串"
    if category == "missing_option":
        return "Error: 未设定决策选项id"
    if category == "invalid_option":
        legal_ids = ", ".join(
            json.dumps(option.option_id, ensure_ascii=False) for option in request.options
        )
        return f"Error: 不合法的选项id。当前决策的合法范围为: [{legal_ids}]"
    if category == "missing_target":
        schema = _target_schema(validation.option)
        return f"Error: 未设定决策目标。本选项必须提供 target，合法值：{schema}。"
    if category == "invalid_target":
        schema = _target_schema(validation.option)
        chosen = _chosen_target_text(validation)
        if chosen is None:
            return (
                f"Error: 错误的目标选择：target 格式不合法。"
                f"本选项的合法 target 值：{schema}，请从中重新选择。"
            )
        return (
            f"Error: 错误的目标选择：你给出的 target「{chosen}」不合法。"
            f"本选项的合法 target 值：{schema}，请从中重新选择。"
        )
    return f"Error: {validation.error or '未知错误'}"


def _target_schema(option: DecisionOption | None) -> str:
    """Describe the option's target field(s) using each field's full legal list.

    Never list a single concrete example — the AI must be shown all admissible
    values so it can freely pick a different one, not steered toward any
    specific choice (§4C-remake feedback rules). String values are quoted
    JSON-style; numeric values are rendered bare.
    """
    if option is None or option.target is None:
        return "无目标"
    per_field = _per_field_legal_values(option.target)
    return "，".join(f"{field} ∈ {_format_values(values)}" for field, values in per_field.items())


def _chosen_target_text(validation: DecisionValidation) -> str | None:
    """Echo the model's own target value from its failed reply, if parseable."""
    try:
        document = json.loads(strip_code_fence(validation.raw_response))
        target = document["selected_option"]["target"]
    except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
        return None
    if isinstance(target, dict):
        return json.dumps(target, ensure_ascii=False)
    return str(target)


def _per_field_legal_values(spec: OptionTarget) -> dict[str, list[object]]:
    """Project the ``(v1, v2, ...)`` tuple space back to per-field value sets.

    Order preserving dedup so the AI sees a stable field-wise list.
    """
    projections: dict[str, list[object]] = {field: [] for field in spec.fields}
    for values in spec.legal_values:
        for field, value in zip(spec.fields, values, strict=True):
            if value not in projections[field]:
                projections[field].append(value)
    return projections


def _format_values(values: list[object]) -> str:
    rendered = ", ".join(_format_value(value) for value in values)
    return f"{{{rendered}}}"


def _format_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
