"""Game rules text loader for Stage 4C prompt segment 2.

The rules content lives in ``doc/monopoly_rules_basic.md`` and is loaded at
runtime (not baked into a constant) so rules edits are picked up without a
Python change. The file contents are cached after the first read so subsequent
prompt renderings do not repeat disk I/O.
"""

from __future__ import annotations

from pathlib import Path

GAME_RULES_VERSION = "v1"

_RULES_PATH = Path(__file__).resolve().parents[3] / "doc" / "monopoly_rules_basic.md"

_cache: str | None = None


def load_game_rules() -> str:
    """Return the game rules markdown text; cached after first read."""
    global _cache
    if _cache is None:
        _cache = _RULES_PATH.read_text(encoding="utf-8")
    return _cache


def reset_cache() -> None:
    """Clear the cached rules text (test helper)."""
    global _cache
    _cache = None
