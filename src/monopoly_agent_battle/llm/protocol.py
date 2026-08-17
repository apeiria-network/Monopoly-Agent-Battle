"""Provider-agnostic LLM call protocol shared by clients and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LLMMessage:
    """One chat message in a multi-turn conversation."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class UsageMetrics:
    """Normalized token and latency usage for a single LLM call."""

    input_tokens: int
    output_tokens: int
    thinking_tokens: int = 0
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """The input a client sends to one model invocation."""

    messages: tuple[LLMMessage, ...]
    model: str
    caller_role: str
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """A text response with normalized usage metrics."""

    content: str
    usage: UsageMetrics
    model: str


class LLMCallError(Exception):
    """Base error raised when an LLM invocation cannot be completed."""


class LLMConnectionError(LLMCallError, ConnectionError):
    """A transient connectivity/timeout failure that may be retried.

    Subclasses ``ConnectionError`` so the decision runner's existing reconnect
    logic (``except ConnectionError``) retries it without protocol changes.
    """


class LLMClient(Protocol):
    """Uniform interface every provider adapter must implement."""

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Invoke one model call and return its text response."""
        ...
