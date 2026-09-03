"""Pre-experiment orchestration: task listing, batch running, and token estimation.

This package generates and runs pre-experiment batches from independent, fully
specified game YAML files. It does not expand a matrix template and does not
implement resume: a batch is expected to run every configured game once, in a
stable, uninterrupted pass. Individual game failures are isolated so one broken
game never aborts the remaining games in the batch.
"""

from __future__ import annotations
