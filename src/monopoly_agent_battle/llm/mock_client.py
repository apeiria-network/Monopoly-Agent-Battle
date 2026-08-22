"""Deterministic, seedable mock LLM client for CI and credential-free runs."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence

from monopoly_agent_battle.decision.prompts import options_from_prompt
from monopoly_agent_battle.llm.protocol import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    UsageMetrics,
)

ResponsePolicy = Callable[[LLMRequest], str]


def estimate_tokens(text: str) -> int:
    """Return a deterministic token-count proxy for text length."""
    return max(1, len(text) // 4)


def _prompt_text(request: LLMRequest) -> str:
    """Return the caller-facing prompt carried by the last user message."""
    return request.messages[-1].content


def _is_great_priest_request(request: LLMRequest) -> bool:
    """Identify the Shang Great Priest role without inspecting prompt content."""
    return request.caller_role.endswith(".great_priest")


def _oracle_response(request: LLMRequest) -> str:
    """Return provisional deterministic oracle prose for the priest role."""
    return "神谕提示：审视眼前局势，谨慎权衡当下行动。"


def first_option_policy(request: LLMRequest) -> str:
    """Emit a valid response for the first rendered candidate.

    ``build_decision_request`` places the engine default first (``end_turn``
    when present, otherwise the first candidate), so this policy drives full
    games deterministically. Candidates that require a target fall through to
    the runner's validation retries and default fallback, which fills targets.
    """
    if _is_great_priest_request(request):
        return _oracle_response(request)
    options = options_from_prompt(_prompt_text(request))
    if not options:
        raise ValueError("prompt contains no legal options")
    option_id = str(options[0]["option_id"])
    return (
        f'{{"selected_option": {{"option": "{option_id}"}}, '
        f'"reason": "选择候选操作 {option_id}。"}}'
    )


def seeded_policy(seed: int) -> ResponsePolicy:
    """Return a deterministic per-call policy for a given seed.

    ``seed == 0`` picks the first candidate (the engine default) so credential-free
    full-game runs stay clean; any other seed advances a local RNG for variety.
    The same seed always reproduces the same response sequence.
    """
    if seed == 0:
        return first_option_policy

    rng = random.Random(seed)

    def policy(request: LLMRequest) -> str:
        if _is_great_priest_request(request):
            return _oracle_response(request)
        options = options_from_prompt(_prompt_text(request))
        if not options:
            raise ValueError("prompt contains no legal options")
        index = rng.randrange(len(options))
        option_id = str(options[index]["option_id"])
        return (
            f'{{"selected_option": {{"option": "{option_id}"}}, '
            f'"reason": "选择候选操作 {option_id}。"}}'
        )

    return policy


def script_policy(responses: Sequence[str]) -> ResponsePolicy:
    """Return a policy that replays a fixed response list in order.

    When the list is exhausted the last response repeats, so a short script can
    still drive a full game deterministically.
    """
    if not responses:
        raise ValueError("mock script must contain at least one response")
    pending = list(responses)

    def policy(_request: LLMRequest) -> str:
        if not pending:
            return responses[-1]
        return pending.pop(0)

    return policy


class MockLLMClient(LLMClient):
    """A deterministic in-process client that answers from a response policy."""

    def __init__(self, policy: ResponsePolicy | None = None, *, seed: int = 0) -> None:
        self._policy = policy if policy is not None else seeded_policy(seed)

    def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        content = self._policy(request)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return LLMResponse(
            content=content,
            usage=UsageMetrics(
                input_tokens=estimate_tokens(_prompt_text(request)),
                output_tokens=estimate_tokens(content),
                duration_ms=duration_ms,
            ),
            model=request.model,
        )
