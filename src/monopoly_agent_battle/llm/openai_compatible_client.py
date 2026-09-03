"""Synchronous client for OpenAI-compatible chat-completions endpoints."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, cast

from monopoly_agent_battle.config.models import ModelProfile
from monopoly_agent_battle.llm.protocol import (
    LLMCallError,
    LLMClient,
    LLMConnectionError,
    LLMRequest,
    LLMResponse,
    UsageMetrics,
)

_DEFAULT_TIMEOUT_SECONDS = 60.0
_RETRYABLE_HTTP_STATUS = {408, 409, 429, 500, 502, 503, 504}
# A browser-style User-Agent so requests are not blocked by CDN/bot filters
# (e.g. Cloudflare error 1010) that reject the default urllib signature. This
# header carries no credentials and does not change request semantics.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class OpenAICompatibleClient(LLMClient):
    """Call one independently configured OpenAI-compatible endpoint."""

    def __init__(self, profile: ModelProfile) -> None:
        if profile.provider != "openai_compatible":
            msg = "OpenAICompatibleClient requires provider=openai_compatible"
            raise ValueError(msg)
        assert profile.base_url is not None
        assert profile.api_key_env is not None
        api_key = os.environ.get(profile.api_key_env)
        if not api_key:
            msg = f"required API key environment variable is not set: {profile.api_key_env}"
            raise ValueError(msg)
        self._endpoint = f"{profile.base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._default_timeout = profile.timeout_seconds or _DEFAULT_TIMEOUT_SECONDS
        self._thinking = profile.thinking

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a chat-completions request and normalize its text and usage."""
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],

            "thinking": {"type": "enabled" if self._thinking else "disabled"},
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.seed is not None:
            payload["seed"] = request.seed

        http_request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
            method="POST",
        )
        timeout = request.timeout_seconds or self._default_timeout
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                loaded: Any = json.loads(response.read().decode("utf-8"))
                if not isinstance(loaded, dict):
                    raise LLMCallError(
                        "OpenAI-compatible endpoint returned an invalid response schema"
                    )
                document = cast(dict[str, Any], loaded)
        except urllib.error.HTTPError as exc:
            message = f"OpenAI-compatible endpoint returned HTTP {exc.code}"
            if exc.code in _RETRYABLE_HTTP_STATUS:
                raise LLMConnectionError(message) from None
            raise LLMCallError(message) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LLMConnectionError(
                f"OpenAI-compatible endpoint connection failed: {type(exc).__name__}"
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # A 2xx response whose body is empty, truncated, or non-JSON is a
            # transport/gateway hiccup (e.g. an unstable upstream channel), not a
            # permanent call error. Treat it as retryable so the runner can retry.
            raise LLMConnectionError(
                f"OpenAI-compatible endpoint returned invalid JSON: {type(exc).__name__}"
            ) from None

        try:
            choice = document["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            usage_value = document.get("usage", {})
            if not isinstance(usage_value, dict):
                raise TypeError
            usage = cast(dict[str, Any], usage_value)
            input_tokens = _integer_usage(usage, "prompt_tokens")
            output_tokens = _integer_usage(usage, "completion_tokens")
            thinking_tokens = _thinking_tokens(usage)
            cached_input_tokens = _cached_input_tokens(usage)
            response_model = document.get("model", request.model)
            if not isinstance(response_model, str):
                raise TypeError
        except (KeyError, IndexError, TypeError):
            raise LLMCallError(
                "OpenAI-compatible endpoint returned an invalid response schema"
            ) from None

        return LLMResponse(
            content=content,
            usage=UsageMetrics(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thinking_tokens=thinking_tokens,
                cached_input_tokens=cached_input_tokens,
            ),
            model=response_model,
        )


def _integer_usage(usage: dict[str, Any], field: str) -> int:
    value = usage.get(field, 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _thinking_tokens(usage: dict[str, Any]) -> int:
    details = usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        return 0
    typed_details = cast(dict[str, Any], details)
    for field in ("reasoning_tokens", "thinking_tokens"):
        value = typed_details.get(field)
        if isinstance(value, int) and value >= 0:
            return value
    return 0


def _cached_input_tokens(usage: dict[str, Any]) -> int:
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        return 0
    return _integer_usage(cast(dict[str, Any], details), "cached_tokens")
