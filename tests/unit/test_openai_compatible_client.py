"""Unit tests for independently configured OpenAI-compatible LLM clients."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from email.message import Message
from io import BytesIO
from typing import Any, cast

import pytest

from monopoly_agent_battle.config.models import ModelProfile
from monopoly_agent_battle.llm.openai_compatible_client import OpenAICompatibleClient
from monopoly_agent_battle.llm.protocol import (
    LLMCallError,
    LLMConnectionError,
    LLMMessage,
    LLMRequest,
)


class FakeHTTPResponse:
    def __init__(self, document: dict[str, Any]) -> None:
        self._payload = json.dumps(document).encode("utf-8")

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def profile(**overrides: object) -> ModelProfile:
    data: dict[str, object] = {
        "provider": "openai_compatible",
        "base_url": "https://example.test/v1/",
        "api_key_env": "TEST_LLM_API_KEY",
        "model": "GLM-5-Turbo",
        "temperature": 0.4,
        "max_tokens": 321,
        "timeout_seconds": 12,
    }
    data.update(overrides)
    return ModelProfile.model_validate(data)


def request() -> LLMRequest:
    return LLMRequest(
        messages=(
            LLMMessage(role="system", content="rules"),
            LLMMessage(role="user", content="decision"),
        ),
        model="request-model",
        caller_role="court.emperor",
        seed=42,
        temperature=0.3,
        max_tokens=123,
        timeout_seconds=7,
    )


def test_client_sends_independent_endpoint_credentials_and_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_LLM_API_KEY", "top-secret-key")
    captured: dict[str, object] = {}

    def fake_urlopen(http_request: urllib.request.Request, timeout: float) -> FakeHTTPResponse:
        captured["url"] = http_request.full_url
        captured["authorization"] = http_request.headers["Authorization"]
        captured["user_agent"] = http_request.headers.get("User-agent")
        raw_data = http_request.data
        assert isinstance(raw_data, bytes)
        captured["payload"] = json.loads(raw_data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeHTTPResponse(
            {
                "model": "actual-model",
                "choices": [{"message": {"content": "answer"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 6},
                    "completion_tokens": 4,
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    response = OpenAICompatibleClient(profile()).complete(request())

    assert captured == {
        "url": "https://example.test/v1/chat/completions",
        "authorization": "Bearer top-secret-key",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "payload": {
            "model": "request-model",
            "messages": [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "decision"},
            ],
            "temperature": 0.3,
            "max_tokens": 123,
            "seed": 42,
            "thinking": {"type": "disabled"},
        },
        "timeout": 7,
    }
    assert response.content == "answer"
    assert response.model == "actual-model"
    assert response.usage.input_tokens == 10
    assert response.usage.cached_input_tokens == 6
    assert response.usage.uncached_input_tokens == 4
    assert response.usage.output_tokens == 4
    assert response.usage.thinking_tokens == 2


def test_client_defaults_missing_cached_token_details_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_LLM_API_KEY", "secret")

    def fake_urlopen(_request: urllib.request.Request, timeout: float) -> FakeHTTPResponse:
        del timeout
        return FakeHTTPResponse(
            {
                "model": "actual-model",
                "choices": [{"message": {"content": "answer"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    usage = OpenAICompatibleClient(profile()).complete(request()).usage

    assert usage.cached_input_tokens == 0
    assert usage.uncached_input_tokens == 10


def test_client_sends_enabled_thinking_when_profile_opts_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_LLM_API_KEY", "secret")
    captured: dict[str, object] = {}

    def fake_urlopen(http_request: urllib.request.Request, timeout: float) -> FakeHTTPResponse:
        del timeout
        raw_data = http_request.data
        assert isinstance(raw_data, bytes)
        captured["payload"] = json.loads(raw_data.decode("utf-8"))
        return FakeHTTPResponse(
            {
                "model": "actual-model",
                "choices": [{"message": {"content": "answer"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    OpenAICompatibleClient(profile(thinking=True)).complete(request())

    payload = cast(dict[str, Any], captured["payload"])
    assert payload["thinking"] == {"type": "enabled"}


def test_client_rejects_missing_environment_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_LLM_API_KEY", raising=False)

    with pytest.raises(ValueError, match="TEST_LLM_API_KEY"):
        OpenAICompatibleClient(profile())


def test_client_classifies_retryable_http_error_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_LLM_API_KEY", "secret-must-not-leak")

    def fake_urlopen(_request: urllib.request.Request, timeout: float) -> FakeHTTPResponse:
        del timeout
        raise urllib.error.HTTPError(
            "https://example.test/v1/chat/completions",
            429,
            "rate limited",
            Message(),
            BytesIO(),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleClient(profile())

    with pytest.raises(LLMConnectionError) as exc_info:
        client.complete(request())
    assert "secret-must-not-leak" not in str(exc_info.value)


def test_client_rejects_invalid_response_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_LLM_API_KEY", "secret")

    def fake_urlopen(
        _request: urllib.request.Request,
        timeout: float,
    ) -> FakeHTTPResponse:
        del timeout
        return FakeHTTPResponse({"choices": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(LLMCallError, match="invalid response schema"):
        OpenAICompatibleClient(profile()).complete(request())


class FakeRawHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> FakeRawHTTPResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_client_classifies_invalid_json_as_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_LLM_API_KEY", "secret-must-not-leak")

    def fake_urlopen(_request: urllib.request.Request, timeout: float) -> FakeRawHTTPResponse:
        del timeout
        # A 2xx body that is empty/truncated/non-JSON (e.g. an unstable upstream
        # channel or gateway hiccup) must be retryable, not a permanent error.
        return FakeRawHTTPResponse(b"<html>502 Bad Gateway</html>")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(LLMConnectionError) as exc_info:
        OpenAICompatibleClient(profile()).complete(request())
    assert "invalid JSON" in str(exc_info.value)
    assert "secret-must-not-leak" not in str(exc_info.value)
