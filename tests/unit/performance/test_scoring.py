"""Unit coverage for court performance scoring."""

from __future__ import annotations

from monopoly_agent_battle.decision.models import (
    DecisionKind,
    DecisionOption,
    DecisionRequest,
    OptionTarget,
)
from monopoly_agent_battle.performance.scoring import (
    ChoiceComparison,
    DecisionEvidence,
    DecisionSignature,
    PerformanceWindow,
    canonicalize_choice,
    compare_choices,
    score_window,
)


def _request() -> DecisionRequest:
    return DecisionRequest(
        decision_id="d1",
        game_id="g1",
        complete_rounds=0,
        player_id="p1",
        phase="asset_management",
        kind=DecisionKind.ASSET_MANAGEMENT,
        question="test",
        visible_state={},
        options=(
            DecisionOption(
                option_id="end_turn",
                command_type="end_turn",
                parameters={},
                title="end",
                preview="end",
                response_format={},
                is_default=True,
            ),
            DecisionOption(
                option_id="mortgage",
                command_type="mortgage",
                parameters={},
                title="mortgage",
                preview="mortgage",
                response_format={},
                target=OptionTarget(
                    kind="position",
                    fields=("position",),
                    command_fields=("position",),
                    legal_values=((1,), (3,)),
                ),
            ),
        ),
        output_constraints={},
    )


def _evidence(matches: list[bool]) -> list[DecisionEvidence]:
    yes = DecisionSignature.from_parts("end_turn", {})
    no = DecisionSignature.from_parts("mortgage", {"position": 1})
    return [
        DecisionEvidence(str(index), yes, {"officer": yes if match else no})
        for index, match in enumerate(matches, 1)
    ]


def _score(delta: int, matches: list[bool]) -> bool:
    result = score_window(
        game_id="g1",
        player_id="p1",
        court="qin_court",
        window=PerformanceWindow.BASIC,
        start_turn=1,
        end_turn=2,
        start_net_worth=100,
        end_net_worth=100 + delta,
        decisions=_evidence(matches),
        officers=("officer",),
    )
    return bool(result.assessments["officer"]["bad_review"])


def test_bad_review_matrix_and_exact_half_boundary() -> None:
    assert _score(-1, [True, True, False]) is True
    assert _score(-1, [True, False, False]) is False
    assert _score(0, [True, True, False]) is False
    assert _score(1, [True, False, False]) is True
    assert _score(-1, [True, False]) is False
    assert _score(1, [True, False]) is False


def test_canonical_choice_requires_same_option_and_complete_target() -> None:
    request = _request()
    first = canonicalize_choice(request, "mortgage", 1)
    same = canonicalize_choice(request, "mortgage", 1)
    different_target = canonicalize_choice(request, "mortgage", 3)
    different_option = canonicalize_choice(request, "end_turn", None)
    invalid = canonicalize_choice(request, "mortgage", 99)

    assert compare_choices(first, same) is ChoiceComparison.SAME
    assert compare_choices(first, different_target) is ChoiceComparison.SAME_OPTION_DIFFERENT_TARGET
    assert compare_choices(first, different_option) is ChoiceComparison.DIFFERENT
    assert compare_choices(first, invalid) is ChoiceComparison.INVALID


def test_empty_shang_window_is_recorded_without_officer_scores() -> None:
    result = score_window(
        game_id="g1",
        player_id="shang",
        court="shang_court",
        window=PerformanceWindow.LONG_TERM,
        start_turn=1,
        end_turn=4,
        start_net_worth=1500,
        end_net_worth=1500,
        decisions=[],
        officers=(),
    )
    assert result.no_scorable_officers is True
    assert result.assessments == {}
    assert result.as_dict()["result"] == "good"
