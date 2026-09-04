"""Unit tests for pre-experiment task status transitions."""

from __future__ import annotations

import pytest

from monopoly_agent_battle.experiments.state_machine import (
    IllegalTaskTransitionError,
    assert_transition,
)


def test_legal_transitions_do_not_raise() -> None:
    assert_transition("pending", "running")
    assert_transition("running", "completed")
    assert_transition("running", "invalid")
    assert_transition("running", "failed")


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("pending", "completed"),
        ("pending", "failed"),
        ("running", "pending"),
        ("completed", "running"),
        ("invalid", "running"),
        ("failed", "running"),
        ("completed", "completed"),
    ],
)
def test_illegal_transitions_raise(current: str, target: str) -> None:
    with pytest.raises(IllegalTaskTransitionError):
        assert_transition(current, target)  # type: ignore[arg-type]
