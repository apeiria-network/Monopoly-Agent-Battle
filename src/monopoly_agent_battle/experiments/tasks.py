"""Task model and stable status vocabulary for pre-experiment batches.

A task represents one fully specified game configuration that the batch runner
executes exactly once. There is no resume: the status vocabulary only records
the outcome of a single pass so an interrupted batch can be inspected and
re-run wholesale later.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Status vocabulary for a single, non-resumable batch pass.
#   pending    - listed and pre-checked, not yet executed
#   running    - currently executing (transient, persisted for crash forensics)
#   completed  - finished and the produced run reported a valid game
#   invalid    - finished but the produced run reported validity_status=invalid
#   failed     - raised an exception during execution
TaskStatus = Literal["pending", "running", "completed", "invalid", "failed"]

TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "invalid", "failed"})


class ExperimentTask(BaseModel):
    """One planned game in a pre-experiment batch and its single-pass outcome."""

    model_config = ConfigDict(extra="forbid")

    game_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    config_path: str = Field(min_length=1)
    status: TaskStatus = "pending"
    # Human-readable reason for a non-completed terminal status. Preserved so an
    # interrupted or partly failing batch is fully auditable without re-running.
    reason: str | None = None
    # Directory of the produced run artifacts, set once a run has been created.
    run_directory: str | None = None
