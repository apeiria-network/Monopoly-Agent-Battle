"""YAML loading and stable configuration fingerprints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from monopoly_agent_battle.config.models import GameConfig


def load_game_config(path: Path) -> GameConfig:
    """Load one strict game configuration from a YAML document."""
    with path.open(encoding="utf-8") as config_file:
        raw_config: Any = yaml.safe_load(config_file)
    if not isinstance(raw_config, dict):
        msg = "game configuration must be a YAML mapping"
        raise ValueError(msg)
    return GameConfig.model_validate(raw_config)


def canonical_config_json(config: GameConfig) -> str:
    """Return a stable JSON representation suitable for hashing and persistence."""
    return json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def config_hash(config: GameConfig) -> str:
    """Return the SHA-256 fingerprint of a frozen configuration."""
    return hashlib.sha256(canonical_config_json(config).encode("utf-8")).hexdigest()
