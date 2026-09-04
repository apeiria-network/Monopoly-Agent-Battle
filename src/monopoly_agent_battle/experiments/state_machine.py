"""Legal status transitions for a single, non-resumable batch pass.

The batch runner drives one task at a time through a linear lifecycle. Because
resume is intentionally out of scope, there is no transition back out of a
terminal status; a fully re-run batch starts every task at ``pending`` again.

    pending  -> running
    running  -> completed | invalid | failed

``completed`` tasks are never automatically re-run. A batch is only started
after every listed configuration passes an up-front pre-check, so there is no
credential/load state to represent during the run itself.
"""

from __future__ import annotations

from monopoly_agent_battle.experiments.tasks import TaskStatus

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running"}),
    "running": frozenset({"completed", "invalid", "failed"}),
    "completed": frozenset(),
    "invalid": frozenset(),
    "failed": frozenset(),
}


class IllegalTaskTransitionError(RuntimeError):
    """Raised when a task is moved between statuses that are not connected."""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"illegal task transition: {current} -> {target}")
        self.current = current
        self.target = target


def assert_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Raise ``IllegalTaskTransitionError`` unless the transition is allowed."""
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise IllegalTaskTransitionError(current, target)
