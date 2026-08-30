"""LLM client wrapper that persists every call for audit and cost tracking."""

from __future__ import annotations

import time

from monopoly_agent_battle.llm.protocol import LLMClient, LLMRequest, LLMResponse
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts

_SUMMARY_LIMIT = 200


def _summary(text: str) -> str:
    if len(text) <= _SUMMARY_LIMIT:
        return text
    return text[:_SUMMARY_LIMIT] + "…[truncated]"


class RecordingLLMClient(LLMClient):
    """Wrap an inner client and append one ``llm_calls.jsonl`` record per call."""

    def __init__(self, inner: LLMClient, artifacts: RunArtifacts) -> None:
        self._inner = inner
        self._artifacts = artifacts

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
                    "caller_role": request.caller_role,
                    "model": request.model,
                    "seed": request.seed,
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "input_tokens": usage.input_tokens if usage is not None else 0,
                    "output_tokens": usage.output_tokens if usage is not None else 0,
                    "thinking_tokens": usage.thinking_tokens if usage is not None else 0,
                    "duration_ms": duration_ms,
                    "tool_calls": 0,
                    "tool_call_failures": 0,
                    "response_summary": (
                        _summary(response.content) if response is not None else None
                    ),
                    "error": error,
                }
            )
