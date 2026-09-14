"""One-off probe: measure GLM implicit context-cache TTL and eviction behavior.

Motivated by pre-test14: glm-5.3-flash recorded cached_tokens=0 on 17 of 18
calls; the single hit (call 32 -> 33, a validation retry) was a back-to-back
resend, while same-role gaps of ~10s (calls 40 -> 42), ~25s (47 -> 51) and
~60s (55 -> 59) all missed. Official docs (docs.bigmodel.cn context cache)
confirm an implicit similarity-based cache with an undocumented validity
period ("缓存有合理的时效性，过期后会重新计算").

This probe brackets the effective TTL under production parameters
(thinking + reasoning_effort + seed) and checks whether a different-prefix
request evicts a warm entry.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

from monopoly_agent_battle.config.local_env import load_local_env

_FULL_PARAMS: dict[str, Any] = {
    "seed": 42,
    "thinking": {"type": "enabled"},
    "reasoning_effort": "low",
}


def build_prefix(tag: str) -> str:
    """Build a ~4.5k-token stable system prompt, unique per tag."""
    body = " ".join(
        f"Rule {tag}{i}: the player must follow regulation {tag}{i} in every phase."
        for i in range(400)
    )
    return f"You are a strict game master. Rules: {body}"


def call(
    label: str,
    system: str,
    question: str,
    extra: dict[str, Any] | None = None,
    extra_messages: list[dict[str, str]] | None = None,
) -> None:
    """Send one chat-completions request and print its cache usage."""
    url = os.environ["GLM_URL"].rstrip("/") + "/chat/completions"
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    if extra_messages:
        messages.extend(extra_messages)
    if question:
        messages.append({"role": "user", "content": question})
    payload: dict[str, Any] = {
        "model": "glm-5.3-flash",
        "messages": messages,
        "max_tokens": 512,
    }
    if extra:
        payload.update(extra)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['GLM_API_KEY']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=120) as response:
        document = json.loads(response.read().decode("utf-8"))
    usage: dict[str, Any] = document["usage"]
    details: dict[str, Any] = usage.get("prompt_tokens_details") or {}
    print(
        f"{label:<24} prompt={usage.get('prompt_tokens')}"
        f" cached={details.get('cached_tokens')}"
        f" ({time.monotonic() - started:.1f}s)",
        flush=True,
    )


def build_chinese_system() -> str:
    """Build a ~4k-token Chinese system prompt (production-like density)."""
    body = " ".join(
        f"规则第{i}条：玩家必须在每个阶段严格遵守第{i}号条例，不得违反。" for i in range(300)
    )
    return f"你是严格的大富翁裁判。规则如下：{body}"


def build_history() -> list[dict[str, str]]:
    """Build a small multi-turn history (production-like shape)."""
    prior_reply = '{"reason": "现金充裕，结束回合。", "selected_option": {"option": "end_turn"}}'
    return [
        {"role": "user", "content": "当前局面：玩家甲持有现金1400，位于第6格。请给出决策。"},
        {"role": "assistant", "content": prior_reply},
        {"role": "user", "content": "当前局面：玩家甲掷骰后移动到第14格，现金1254。请给出决策。"},
    ]


def main() -> None:
    load_local_env()
    system = build_chinese_system()
    history = build_history()

    print("Phase F: TTL upper bound, identical multi-turn payload", flush=True)
    call("F1 cold", system, "", _FULL_PARAMS, extra_messages=history)
    call("F2 identical +0s", system, "", _FULL_PARAMS, extra_messages=history)
    time.sleep(60)
    call("F3 identical +60s", system, "", _FULL_PARAMS, extra_messages=history)
    time.sleep(120)
    call("F4 identical +180s", system, "", _FULL_PARAMS, extra_messages=history)
    time.sleep(120)
    call("F5 identical +300s", system, "", _FULL_PARAMS, extra_messages=history)

    print("Phase G: single system+user pairs, replication of E anomaly", flush=True)
    for pair in range(3):
        tagged = build_prefix(f"G{pair}")
        call(f"G{pair}a cold", tagged, f"Question {pair}: summarize rule 5.", _FULL_PARAMS)
        call(f"G{pair}b identical +0s", tagged, f"Question {pair}: summarize rule 5.", _FULL_PARAMS)

    print("Phase H: segment-3-style front truncation breaks prefix?", flush=True)
    lines = [f"[Round {i}] Player moved to square {i} and paid rent {i}." for i in range(20)]
    base = [{"role": "user", "content": "\n".join(lines)}]
    call("H1 base cold", system, "Decide now.", _FULL_PARAMS, extra_messages=base)
    call("H2 base +0s", system, "Decide now.", _FULL_PARAMS, extra_messages=base)
    shifted = [
        {
            "role": "user",
            "content": "\n".join([*lines[1:], "[Round 20] New event happened."]),
        }
    ]
    call("H3 front-truncated", system, "Decide again.", _FULL_PARAMS, extra_messages=shifted)


if __name__ == "__main__":
    main()
