"""YAML loading and stable configuration fingerprints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from monopoly_agent_battle.config.models import SUPPORTED_REMOTE_MODELS, GameConfig


def load_game_config(path: Path) -> GameConfig:
    """Load one strict game configuration from a YAML document."""
    with path.open(encoding="utf-8") as config_file:
        raw_config: Any = yaml.safe_load(config_file)
    if not isinstance(raw_config, dict):
        msg = "game configuration must be a YAML mapping"
        raise ValueError(msg)
    config = GameConfig.model_validate(raw_config)
    _reject_unsupported_remote_models(config)
    return config


def _reject_unsupported_remote_models(config: GameConfig) -> None:
    allowed = ", ".join(sorted(SUPPORTED_REMOTE_MODELS))
    for name, profile in config.model_profiles.items():
        if profile.provider != "openai_compatible":
            continue
        if profile.model not in SUPPORTED_REMOTE_MODELS:
            msg = (
                f"model profile '{name}' uses unsupported model '{profile.model}' "
                f"for provider openai_compatible; supported models: {allowed}"
            )
            raise ValueError(msg)


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
