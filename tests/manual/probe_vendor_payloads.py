"""One-off probe: validate exact game payload shapes against all four endpoints."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from monopoly_agent_battle.config.local_env import load_local_env


def probe(label: str, base_url_env: str, key_env: str, payload: dict[str, object]) -> None:
    url = os.environ[base_url_env].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {os.environ[key_env]}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            document = json.loads(response.read().decode("utf-8"))
            usage = document.get("usage", {})
            out = usage.get("completion_tokens")
            print(f"{label} -> 200 OK (out={out})")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        print(f"{label} -> HTTP {exc.code} : {body}")
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"{label} -> CONNECTION ERROR : {type(exc).__name__} {exc}")


def main() -> None:
    load_local_env()
    message = [{"role": "user", "content": "回复OK"}]

    probe(
        "kimi no-temp           ",
        "KIMI_URL",
        "KIMI_API_KEY",
        {
            "model": "kimi-k2.6",
            "messages": message,
            "max_tokens": 2048,
            "seed": 42,
            "thinking": {"type": "enabled"},
        },
    )
    probe(
        "glm  full(temp0.4)     ",
        "GLM_URL",
        "GLM_API_KEY",
        {
            "model": "glm-5.3-flash",
            "messages": message,
            "max_tokens": 2048,
            "seed": 42,
            "temperature": 0.4,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
        },
    )
    probe(
        "gpt  full(temp0.4)     ",
        "GPT_URL",
        "GPT_API_KEY",
        {
            "model": "gpt-5.6-luna",
            "messages": message,
            "max_tokens": 2048,
            "seed": 42,
            "temperature": 0.4,
            "reasoning": {"effort": "low"},
        },
    )
    probe(
        "gpt  retry             ",
        "GPT_URL",
        "GPT_API_KEY",
        {
            "model": "gpt-5.6-luna",
            "messages": message,
            "max_tokens": 2048,
            "seed": 42,
            "temperature": 0.4,
            "reasoning": {"effort": "low"},
        },
    )
    probe(
        "dsk  full(temp0.4)     ",
        "DEEPSEEK_URL",
        "DEEPSEEK_API_KEY",
        {
            "model": "DeepSeek-V4-Pro",
            "messages": message,
            "max_tokens": 2048,
            "seed": 42,
            "temperature": 0.4,
            "thinking": {"type": "enabled"},
        },
    )


if __name__ == "__main__":
    main()
