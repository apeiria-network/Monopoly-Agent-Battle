"""Load and cache game rules text (Stage 4C)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_game_rules() -> str:
    """Load the game rules from doc/monopoly_rules_basic.md.

    Returns:
        The complete rules text as a string

    Raises:
        FileNotFoundError: If the rules file doesn't exist
    """
    # Navigate from src/monopoly_agent_battle/context to doc/
    rules_path = Path(__file__).parent.parent.parent.parent / "doc" / "monopoly_rules_basic.md"
    return rules_path.read_text(encoding="utf-8")
