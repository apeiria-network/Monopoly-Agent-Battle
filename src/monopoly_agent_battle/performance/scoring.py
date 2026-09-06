"""Pure performance scoring models and calculations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.protocol import parse_and_validate


class PerformanceWindow(StrEnum):
    BASIC = "basic"
    LONG_TERM = "long_term"


class ChoiceComparison(StrEnum):
    SAME = "same_choice"
    SAME_OPTION_DIFFERENT_TARGET = "same_option_different_target"
    DIFFERENT = "different_choice"
    INVALID = "invalid_choice"


@dataclass(frozen=True, slots=True)
class DecisionSignature:
    """A protocol-normalized engine option and its complete target."""

    option: str
    target_json: str

    @classmethod
    def from_parts(cls, option: str, target: dict[str, object] | None) -> DecisionSignature:
        return cls(option, json.dumps(target or {}, ensure_ascii=False, sort_keys=True))

    @classmethod
    def from_selected(cls, selected: dict[str, Any]) -> DecisionSignature:
        target = {key: value for key, value in selected.items() if key != "option"}
        return cls.from_parts(str(selected.get("option")), target)

    def as_dict(self) -> dict[str, object]:
        target = json.loads(self.target_json)
        if not isinstance(target, dict):
            raise AssertionError("decision signature target must be an object")
        return {"option": self.option, "target": target}


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    """One executed imperial choice and each scorable officer's final opinion."""

    decision_id: str
    emperor: DecisionSignature
    officer_signatures: dict[str, DecisionSignature | None]
    special_agreement: dict[str, bool] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "emperor": self.emperor.as_dict(),
            "officer_opinions": {
                officer: signature.as_dict() if signature is not None else None
                for officer, signature in self.officer_signatures.items()
            },
            "special_agreement": dict(self.special_agreement or {}),
        }


@dataclass(frozen=True, slots=True)
class PerformanceWindowResult:
    game_id: str
    player_id: str
    court: str
    window: PerformanceWindow
    start_turn: int
    end_turn: int
    start_net_worth: int
    end_net_worth: int
    decisions: tuple[DecisionEvidence, ...]
    assessments: dict[str, dict[str, object]]
    no_scorable_officers: bool = False

    @property
    def delta(self) -> int:
        return self.end_net_worth - self.start_net_worth

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "court-performance-v1",
            "game_id": self.game_id,
            "player_id": self.player_id,
            "court": self.court,
            "window": self.window.value,
            "start_action_turn": self.start_turn,
            "end_action_turn": self.end_turn,
            "start_net_worth": self.start_net_worth,
            "end_net_worth": self.end_net_worth,
            "delta": self.delta,
            "result": "good" if self.delta >= 0 else "bad",
            "decision_ids": [item.decision_id for item in self.decisions],
            "decisions": [item.as_dict() for item in self.decisions],
            "assessments": self.assessments,
            "no_scorable_officers": self.no_scorable_officers,
        }


def canonicalize_choice(
    request: DecisionRequest, option_id: str, raw_target: object | None
) -> DecisionSignature | None:
    """Canonicalize a protocol choice using JSON-facing target semantics."""
    selected: dict[str, object] = {"option": option_id}
    if raw_target is not None:
        selected["target"] = raw_target
    reply = json.dumps(
        {"selected_option": selected, "reason": "canonicalization"}, ensure_ascii=False
    )
    validation = parse_and_validate(reply, request)
    if not validation.valid or validation.option is None:
        return None
    target = raw_target
    if validation.option.target is None:
        target_dict: dict[str, object] = {}
    elif len(validation.option.target.fields) == 1:
        target_dict = {validation.option.target.fields[0]: target}
    else:
        if not isinstance(target, dict):
            return None
        target_dict = {
            field: target[field] for field in validation.option.target.fields if field in target
        }
    return DecisionSignature.from_parts(validation.option.option_id, target_dict)


def compare_choices(
    advice: DecisionSignature | None, reference: DecisionSignature | None
) -> ChoiceComparison:
    """Compare canonical choices while distinguishing target disagreement."""
    if advice is None or reference is None:
        return ChoiceComparison.INVALID
    if advice == reference:
        return ChoiceComparison.SAME
    if advice.option == reference.option:
        return ChoiceComparison.SAME_OPTION_DIFFERENT_TARGET
    return ChoiceComparison.DIFFERENT


def score_window(
    *,
    game_id: str,
    player_id: str,
    court: str,
    window: PerformanceWindow,
    start_turn: int,
    end_turn: int,
    start_net_worth: int,
    end_net_worth: int,
    decisions: list[DecisionEvidence],
    officers: tuple[str, ...],
) -> PerformanceWindowResult:
    """Score one immutable 1-turn or 3-turn performance window."""
    assessments: dict[str, dict[str, object]] = {}
    for officer in officers:
        matches = [_is_consistent(evidence, officer) for evidence in decisions]
        count = sum(matches)
        total = len(matches)
        empty = total == 0
        above = 2 * count > total
        equal = 2 * count == total
        bad_result = end_net_worth < start_net_worth
        poor = False if (empty or equal) else (above == bad_result)
        assessments[officer] = {
            "consistent_count": count,
            "decision_count": total,
            "consistency_ratio": count / total if total else None,
            "ratio_relation": "empty"
            if empty
            else "equal"
            if equal
            else "above"
            if above
            else "below",
            "per_decision_consistent": matches,
            "bad_review": poor,
            "reason": "本窗口无决策记录，不记差评。"
            if empty
            else _reason(bad_result, equal, above),
        }
    return PerformanceWindowResult(
        game_id=game_id,
        player_id=player_id,
        court=court,
        window=window,
        start_turn=start_turn,
        end_turn=end_turn,
        start_net_worth=start_net_worth,
        end_net_worth=end_net_worth,
        decisions=tuple(decisions),
        assessments=assessments,
        no_scorable_officers=not officers,
    )


def _is_consistent(evidence: DecisionEvidence, officer: str) -> bool:
    if evidence.special_agreement and officer in evidence.special_agreement:
        return evidence.special_agreement[officer]
    opinion = evidence.officer_signatures.get(officer)
    return opinion is not None and opinion == evidence.emperor


def _reason(bad_result: bool, equal: bool, above: bool) -> str:
    if equal:
        return "一致率恰为50%，按制度不记差评。"
    if bad_result:
        if above:
            return "坏结果且多数意见与皇帝最终决策一致，记为差评。"
        return "坏结果且多数意见未与皇帝最终决策一致，不记差评。"
    if above:
        return "好结果且多数意见与皇帝最终决策一致，不记差评。"
    return "好结果且多数意见未与皇帝最终决策一致，记为差评。"
