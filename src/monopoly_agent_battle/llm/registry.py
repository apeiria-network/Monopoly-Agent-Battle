"""Pluggable registration of provider adapters by provider alias."""

from __future__ import annotations

from collections.abc import Callable

from monopoly_agent_battle.config.models import ModelProfile
from monopoly_agent_battle.llm.protocol import LLMClient

ClientFactory = Callable[[ModelProfile], LLMClient]

_factories: dict[str, ClientFactory] = {}


def register_client_factory(provider: str, factory: ClientFactory) -> None:
    """Register a factory that builds an LLM client for a provider alias."""
    _factories[provider] = factory


def create_client(profile: ModelProfile) -> LLMClient:
    """Build an LLM client for the profile's registered provider."""
    try:
        factory = _factories[profile.provider]
    except KeyError:
        msg = f"no client factory registered for provider: {profile.provider}"
        raise ValueError(msg) from None
    return factory(profile)
