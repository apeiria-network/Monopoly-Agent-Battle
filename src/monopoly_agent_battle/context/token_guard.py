"""Token estimation and segment-3 event-level truncation for Stage 4C.

Deterministic character-based estimator so runs are reproducible without any
model tokenizer dependency:

- Every CJK character counts as 1 token.
- Every non-CJK character (including spaces, newlines, digits, punctuation)
  contributes 0.25 token; the sum is rounded up so a single non-CJK character
  still costs 1 token.

The segment-3 truncator drops rendered event sentences from the earliest end
until the total estimate fits the caller's budget. It never rewrites individual
sentences; the smallest unit is a full broadcast event.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContextWarning:
    """A non-fatal advisory logged when segment 3 cannot fit the budget."""

    kind: str
    detail: str


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
        or 0x3000 <= code <= 0x303F  # CJK Symbols and Punctuation
        or 0xFF00 <= code <= 0xFFEF  # Halfwidth/Fullwidth Forms
        or 0x3400 <= code <= 0x4DBF  # CJK Unified Ideographs Extension A
    )


def estimate_tokens(text: str) -> int:
    """Return an integer token estimate; deterministic and Unicode-aware."""
    if not text:
        return 0
    cjk = sum(1 for char in text if _is_cjk(char))
    others = len(text) - cjk
    return cjk + math.ceil(others / 4)


def truncate_events_to_budget(
    rendered_events: Sequence[str],
    budget_tokens: int,
) -> tuple[tuple[str, ...], ContextWarning | None]:
    """Truncate rendered event sentences from the earliest until they fit.

    Returns ``(kept_sentences, warning?)``:

    - If ``budget_tokens <= 0``: drop every sentence and emit an overflow warning
      unless the input is already empty.
    - Otherwise drop the earliest sentences one-by-one; keep the trailing tail
      that fits, accounting for the newlines that join retained events. A
      warning records every truncation, including the case where every event
      must be dropped to keep the cap strict.
    """
    if not rendered_events:
        return (), None
    if budget_tokens <= 0:
        return (), ContextWarning(
            kind="segment3_overflow",
            detail=(f"budget_tokens<=0; dropped all {len(rendered_events)} segment-3 events"),
        )

    total_tokens = [estimate_tokens(sentence) for sentence in rendered_events]
    separator_tokens = estimate_tokens("\n")
    running_total = sum(total_tokens) + separator_tokens * (len(rendered_events) - 1)
    if running_total <= budget_tokens:
        return tuple(rendered_events), None

    # Drop complete events from the earliest end until the retained, newline-
    # joined tail fits the strict cap.
    dropped = 0
    while dropped < len(rendered_events) and running_total > budget_tokens:
        running_total -= total_tokens[dropped]
        if dropped < len(rendered_events) - 1:
            running_total -= separator_tokens
        dropped += 1

    kept = tuple(rendered_events[dropped:])
    return kept, ContextWarning(
        kind="segment3_overflow",
        detail=(
            f"dropped {dropped} of {len(rendered_events)} segment-3 events "
            f"to fit strict budget {budget_tokens} (estimated {running_total})"
        ),
    )
