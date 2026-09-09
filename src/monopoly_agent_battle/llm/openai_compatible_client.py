"""Synchronous client for OpenAI-compatible chat-completions endpoints."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, ClassVar, cast

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

    provider_name: ClassVar[str] = "openai_compatible"
    error_label: ClassVar[str] = "OpenAI-compatible endpoint"
    # GPT-5-style endpoints require the max_completion_tokens field name.
    max_tokens_field: ClassVar[str] = "max_tokens"

    def __init__(self, profile: ModelProfile) -> None:
        if profile.provider != self.provider_name:
            msg = f"{type(self).__name__} requires provider={self.provider_name}"
            raise ValueError(msg)
        assert profile.api_key_env is not None
        api_key = os.environ.get(profile.api_key_env)
        if not api_key:
            msg = f"required API key environment variable is not set: {profile.api_key_env}"
            raise ValueError(msg)
        if profile.base_url is not None:
            base_url = profile.base_url
        else:
            assert profile.base_url_env is not None
            base_url = os.environ.get(profile.base_url_env)
            if not base_url:
                msg = f"required base URL environment variable is not set: {profile.base_url_env}"
                raise ValueError(msg)
        if not base_url.startswith(("http://", "https://")):
            msg = f"{self.provider_name} base URL must use http:// or https://"
            raise ValueError(msg)
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
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
        }
        self._apply_vendor_parameters(payload)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload[self.max_tokens_field] = request.max_tokens
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
                    raise LLMCallError(f"{self.error_label} returned an invalid response schema")
                document = cast(dict[str, Any], loaded)
        except urllib.error.HTTPError as exc:
            message = f"{self.error_label} returned HTTP {exc.code}{_http_error_detail(exc)}"
            if exc.code in _RETRYABLE_HTTP_STATUS:
                raise LLMConnectionError(message) from None
            raise LLMCallError(message) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LLMConnectionError(
                f"{self.error_label} connection failed: {type(exc).__name__}"
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # A 2xx response whose body is empty, truncated, or non-JSON is a
            # transport/gateway hiccup (e.g. an unstable upstream channel), not a
            # permanent call error. Treat it as retryable so the runner can retry.
            raise LLMConnectionError(
                f"{self.error_label} returned invalid JSON: {type(exc).__name__}"
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
            raise LLMCallError(f"{self.error_label} returned an invalid response schema") from None

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

    def _apply_vendor_parameters(self, payload: dict[str, Any]) -> None:
        """Attach provider-specific thinking/sampling fields to the payload."""
        if self._thinking:
            payload["thinking"] = {"type": "enabled"}


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    """Return a compact, whitespace-collapsed excerpt of the error body.

    The vendor's error JSON names the offending parameter (e.g. an unknown
    ``reasoning`` field), which is essential for diagnosing rejected payloads;
    without it an HTTP 400 carries no actionable detail. Reading the body is
    best-effort: transport problems while reading degrade to no detail.
    """
    try:
        body = exc.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""
    collapsed = " ".join(body.split())
    if not collapsed:
        return ""
    return f": {collapsed[:300]}"


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
