"""Unit tests for vendor-specific thinking-mode payload assembly."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import Any, cast

import pytest

from monopoly_agent_battle.config.models import ModelProfile
from monopoly_agent_battle.llm.deepseek_client import DeepSeekClient
from monopoly_agent_battle.llm.glm_client import GlmClient
from monopoly_agent_battle.llm.gpt_client import GptClient
from monopoly_agent_battle.llm.kimi_client import KimiClient
from monopoly_agent_battle.llm.openai_compatible_client import OpenAICompatibleClient
from monopoly_agent_battle.llm.protocol import LLMMessage, LLMRequest
from monopoly_agent_battle.llm.qwen_client import QwenClient


class FakeHTTPResponse:
    def __init__(self, document: dict[str, Any]) -> None:
        self._payload = json.dumps(document).encode("utf-8")

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def profile(provider: str, model: str, **overrides: object) -> ModelProfile:
    data: dict[str, object] = {
        "provider": provider,
        "base_url": "https://vendor.example/v1/",
        "api_key_env": "TEST_VENDOR_API_KEY",
        "model": model,
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
    )


def capture_request(monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]) -> None:
    monkeypatch.setenv("TEST_VENDOR_API_KEY", "vendor-secret")

    def fake_urlopen(http_request: urllib.request.Request, timeout: float) -> FakeHTTPResponse:
        del timeout
        captured["url"] = http_request.full_url
        captured["authorization"] = http_request.headers["Authorization"]
        raw_data = http_request.data
        assert isinstance(raw_data, bytes)
        captured["payload"] = json.loads(raw_data.decode("utf-8"))
        return FakeHTTPResponse(
            {
                "model": "actual-model",
                "choices": [{"message": {"content": "answer"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def payload_of(
    monkeypatch: pytest.MonkeyPatch,
    build_client: Callable[[], OpenAICompatibleClient],
) -> dict[str, Any]:
    captured: dict[str, object] = {}
    capture_request(monkeypatch, captured)
    build_client().complete(request())
    payload = cast(dict[str, Any], captured["payload"])
    return payload


def test_kimi_sends_disabled_thinking_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    capture_request(monkeypatch, captured)
    client = KimiClient(profile("kimi", "kimi-k2.6"))

    response = client.complete(request())

    assert captured["url"] == "https://vendor.example/v1/chat/completions"
    assert captured["authorization"] == "Bearer vendor-secret"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning" not in payload
    assert "reasoning_effort" not in payload
    assert response.content == "answer"
    assert response.usage.thinking_tokens == 2


def test_kimi_sends_enabled_thinking_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = payload_of(
        monkeypatch, lambda: KimiClient(profile("kimi", "kimi-k2.6", thinking=True))
    )

    assert payload["thinking"] == {"type": "enabled"}


def test_glm_omits_thinking_fields_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = payload_of(monkeypatch, lambda: GlmClient(profile("glm", "glm-5.3-flash")))

    assert "thinking" not in payload
    assert "reasoning_effort" not in payload


def test_glm_sends_thinking_and_low_reasoning_effort_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = payload_of(
        monkeypatch, lambda: GlmClient(profile("glm", "glm-5.3-flash", thinking=True))
    )

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "low"


def test_deepseek_omits_thinking_fields_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = payload_of(
        monkeypatch, lambda: DeepSeekClient(profile("deepseek", "DeepSeek-V4-Flash"))
    )

    assert "thinking" not in payload
    assert "reasoning_effort" not in payload


def test_deepseek_sends_thinking_and_low_reasoning_effort_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = payload_of(
        monkeypatch,
        lambda: DeepSeekClient(profile("deepseek", "DeepSeek-V4-Flash", thinking=True)),
    )

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "low"


def test_gpt_omits_reasoning_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = payload_of(monkeypatch, lambda: GptClient(profile("gpt", "gpt-5.6-luna")))

    assert "reasoning" not in payload
    assert "reasoning_effort" not in payload
    assert "thinking" not in payload
    assert payload["max_completion_tokens"] == 123
    assert "max_tokens" not in payload


def test_gpt_sends_reasoning_effort_low_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = payload_of(
        monkeypatch, lambda: GptClient(profile("gpt", "gpt-5.6-luna", thinking=True))
    )

    assert payload["reasoning_effort"] == "low"
    assert "reasoning" not in payload
    assert "thinking" not in payload
    assert payload["max_completion_tokens"] == 123
    assert "max_tokens" not in payload


def test_qwen_sends_disabled_thinking_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = payload_of(monkeypatch, lambda: QwenClient(profile("qwen", "qwen3.8-flash")))

    assert payload["enable_thinking"] is False
    assert "thinking" not in payload
    assert "reasoning" not in payload
    assert "reasoning_effort" not in payload


def test_qwen_sends_enabled_thinking_and_low_effort_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = payload_of(
        monkeypatch, lambda: QwenClient(profile("qwen", "qwen3.8-flash", thinking=True))
    )

    assert payload["enable_thinking"] is True
    assert payload["reasoning_effort"] == "low"
    assert "thinking" not in payload
    assert "reasoning" not in payload


@pytest.mark.parametrize(
    ("client_class", "provider"),
    [
        (KimiClient, "kimi"),
        (GlmClient, "glm"),
        (GptClient, "gpt"),
        (QwenClient, "qwen"),
        (DeepSeekClient, "deepseek"),
    ],
)
def test_vendor_clients_reject_mismatched_provider(
    monkeypatch: pytest.MonkeyPatch,
    client_class: type[OpenAICompatibleClient],
    provider: str,
) -> None:
    monkeypatch.setenv("TEST_VENDOR_API_KEY", "vendor-secret")

    with pytest.raises(ValueError, match=f"requires provider={provider}"):
        client_class(profile("openai_compatible", "some-model"))


@pytest.mark.parametrize(
    ("client_class", "provider", "model"),
    [
        (KimiClient, "kimi", "kimi-k2.6"),
        (GlmClient, "glm", "glm-5.3-flash"),
        (GptClient, "gpt", "gpt-5.6-luna"),
        (QwenClient, "qwen", "qwen3.8-flash"),
        (DeepSeekClient, "deepseek", "DeepSeek-V4-Flash"),
    ],
)
def test_vendor_clients_require_environment_credential(
    monkeypatch: pytest.MonkeyPatch,
    client_class: type[OpenAICompatibleClient],
    provider: str,
    model: str,
) -> None:
    monkeypatch.delenv("TEST_VENDOR_API_KEY", raising=False)

    with pytest.raises(ValueError, match="TEST_VENDOR_API_KEY"):
        client_class(profile(provider, model))
