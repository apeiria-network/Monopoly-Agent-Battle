"""LLM client wrapper that persists every call for audit and cost tracking."""

from __future__ import annotations

import time
from collections.abc import Callable

from monopoly_agent_battle.llm.protocol import LLMClient, LLMRequest, LLMResponse
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


class RecordingLLMClient(LLMClient):
    """Wrap an inner client and append one ``llm_calls.jsonl`` record per call.

    The recorded ``response_summary`` preserves the full response content
    without truncation. When a ``round_provider`` is supplied, each record also
    carries the game's current ``complete_rounds`` so downstream digests can
    label a call by its real game round rather than a call sequence number.
    """

    def __init__(
        self,
        inner: LLMClient,
        artifacts: RunArtifacts,
        round_provider: Callable[[], int] | None = None,
    ) -> None:
        self._inner = inner
        self._artifacts = artifacts
        self._round_provider = round_provider

    def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        response: LLMResponse | None = None
        error: str | None = None
        try:
            response = self._inner.complete(request)
            return response
        except Exception as exc:  # record failures, then re-raise for retry logic
            error = str(exc)
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            usage = response.usage if response is not None else None
            self._artifacts.append_llm_call(
                {
                    "call_id": None,
                    "complete_rounds": (
                        self._round_provider() if self._round_provider is not None else None
                    ),
                    "caller_role": request.caller_role,
                    "model": request.model,
                    "seed": request.seed,
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "input_tokens": usage.input_tokens if usage is not None else 0,
                    "cached_input_tokens": (usage.cached_input_tokens if usage is not None else 0),
                    "uncached_input_tokens": (
                        usage.uncached_input_tokens if usage is not None else 0
                    ),
                    "output_tokens": usage.output_tokens if usage is not None else 0,
                    "thinking_tokens": usage.thinking_tokens if usage is not None else 0,
                    "duration_ms": duration_ms,
                    "tool_calls": 0,
                    "tool_call_failures": 0,
                    "response_summary": (response.content if response is not None else None),
                    "error": error,
                }
            )
