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


def call(label: str, system: str, question: str, extra: dict[str, Any] | None = None) -> None:
    """Send one chat-completions request and print its cache usage."""
    url = os.environ["GLM_URL"].rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": "glm-5.3-flash",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ],
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
    usage = document["usage"]
    details = usage.get("prompt_tokens_details") or {}
    print(
        f"{label:<24} prompt={usage.get('prompt_tokens')}"
        f" cached={details.get('cached_tokens')}"
        f" ({time.monotonic() - started:.1f}s)",
        flush=True,
    )


def main() -> None:
    load_local_env()
    prefix_p = build_prefix("P")
    prefix_q = build_prefix("Q")
    prefix_r = build_prefix("R")
    question = "State rule 12 in one short sentence."

    print("Phase A: TTL bracket, production params (thinking+effort+seed)", flush=True)
    call("A1 cold", prefix_p, question, _FULL_PARAMS)
    call("A2 repeat +0s", prefix_p, question, _FULL_PARAMS)
    time.sleep(5)
    call("A3 repeat +5s", prefix_p, question, _FULL_PARAMS)
    time.sleep(5)
    call("A4 repeat +10s", prefix_p, question, _FULL_PARAMS)
    time.sleep(10)
    call("A5 repeat +20s", prefix_p, question, _FULL_PARAMS)
    time.sleep(20)
    call("A6 repeat +40s", prefix_p, question, _FULL_PARAMS)

    print("Phase B: eviction by a different prefix (back-to-back)", flush=True)
    call("B0 re-warm P", prefix_p, question, _FULL_PARAMS)
    call("B1 other prefix Q", prefix_q, question, _FULL_PARAMS)
    call("B2 P after Q +0s", prefix_p, question, _FULL_PARAMS)

    print("Phase C: bare params (no seed / no thinking), fresh prefix", flush=True)
    call("C1 bare cold", prefix_r, question)
    call("C2 bare repeat +0s", prefix_r, question)


if __name__ == "__main__":
    main()
