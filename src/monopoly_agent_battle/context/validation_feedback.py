"""Validation-failure feedback template for Stage 4C conversation retries.

The feedback text is produced by ``build_feedback(validation, request)``. It
maps each validation ``error_category`` to a fixed Chinese template and,
where useful, appends context-dependent data (list of legal option ids for
``invalid_option``; per-field legal-value list for ``invalid_target``). The
resulting string is stored on ``AgentConversation`` as the user side of an
``ErrorEntry`` and replayed in segment 4 for the remainder of the turn.
"""

from __future__ import annotations

from monopoly_agent_battle.decision.models import (
    DecisionOption,
    DecisionRequest,
    DecisionValidation,
    OptionTarget,
)


def build_feedback(validation: DecisionValidation, request: DecisionRequest) -> str:
    """Return the user-facing validation-failure message for a retry."""
    category = validation.error_category
    if category == "not_json":
        return "Error: 决策回复必须是一个JSON"
    if category == "missing_option":
        return "Error: 未设定决策选项id"
    if category == "invalid_option":
        legal_ids = [option.option_id for option in request.options]
        return f"Error: 不合法的选项id。当前决策的合法范围为: {legal_ids}"
    if category == "missing_target":
        return "Error: 未设定决策目标"
    if category == "invalid_target":
        schema = _target_schema(validation.option)
        return f"Error: 错误的目标选择。目标字段结构为：{schema}"
    return f"Error: {validation.error or '未知错误'}"


def _target_schema(option: DecisionOption | None) -> str:
    """Describe the option's target field(s) using each field's full legal list.

    Never list a single concrete example — the AI must be shown all admissible
    values so it can freely pick a different one, not steered toward any
    specific choice (§4C-remake feedback rules).
    """
    if option is None or option.target is None:
        return "无目标"
    spec = option.target
    if len(spec.fields) == 1:
        legal_values = [values[0] for values in spec.legal_values]
        return f"{{{spec.fields[0]}: {legal_values}}}"
    per_field = _per_field_legal_values(spec)
    inner = ", ".join(f"{field}: {values}" for field, values in per_field.items())
    return f"{{{inner}}}"


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
