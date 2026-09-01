"""Load local environment variables for the command-line application."""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env(path: Path | None = None) -> Path | None:
    """Load ``.env.local`` without overriding variables already in the environment."""
    env_path = path or Path.cwd() / ".env.local"
    if not env_path.is_file():
        return None
    for line_number, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid .env.local line {line_number}: expected NAME=VALUE")
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or not _valid_name(name):
            raise ValueError(f"invalid .env.local variable name on line {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(name, value)
    return env_path


def _valid_name(name: str) -> bool:
    return bool(
        name
        and (name[0].isalpha() or name[0] == "_")
        and all(character.isalnum() or character == "_" for character in name)
    )
