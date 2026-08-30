"""Local fake LLM client for prompt-aware, protocol-valid simulation."""

from __future__ import annotations

import json
import random
import time
from typing import Any, cast

from monopoly_agent_battle.decision.protocol import option_json
from monopoly_agent_battle.llm.mock_client import estimate_tokens
from monopoly_agent_battle.llm.protocol import LLMClient, LLMRequest, LLMResponse, UsageMetrics


class FakeLLMClient(LLMClient):
    """Generate deterministic random replies without making network requests."""

    def __init__(self, *, seed: int = 42) -> None:
        self._rng = random.Random(seed)

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Read the supplied context and return a local simulated model reply."""
        started = time.perf_counter()
        content = self._reply(request)
        return LLMResponse(
            content=content,
            usage=UsageMetrics(
                input_tokens=sum(estimate_tokens(message.content) for message in request.messages),
                output_tokens=estimate_tokens(content),
                duration_ms=int((time.perf_counter() - started) * 1000),
            ),
            model=request.model,
        )

    def _reply(self, request: LLMRequest) -> str:
        caller = request.caller_role
        if caller.endswith(".great_priest"):
            return "神谕提示：审视当前局势，谨慎权衡可行行动。"
        if caller.endswith(".imperial_counsellor"):
            return json.dumps(
                {
                    "reason": "根据两位官员的建议进行模拟评估。",
                    "assessments": [
                        {"officer_id": "chancellor", "judgement": self._judgement()},
                        {"officer_id": "grand_marshal", "judgement": self._judgement()},
                    ],
                },
                ensure_ascii=False,
            )
        if caller.endswith(".menxia"):
            return json.dumps(
                {
                    "reason": "根据中书省草案进行模拟审核。",
                    "selected_option": {"option": self._rng.choice(("agree", "disagree"))},
                },
                ensure_ascii=False,
            )
        decision = request.decision_request
        if decision is None or not decision.options:
            return json.dumps(
                {"selected_option": {"option": "end_turn"}, "reason": "模拟完成。"},
                ensure_ascii=False,
            )
        required = self._required_selected_option(request)
        if required is not None:
            return json.dumps(
                {"selected_option": required, "reason": "模拟模型采用内阁确定结果。"},
                ensure_ascii=False,
            )
        option = self._rng.choice(decision.options)
        target = self._rng.choice(option.target.legal_values) if option.target is not None else None
        return json.dumps(
            {
                "selected_option": option_json(option, target),
                "reason": f"模拟模型根据上下文随机选择候选操作 {option.option_id}。",
            },
            ensure_ascii=False,
        )

    def _judgement(self) -> str:
        return self._rng.choice(("agree", "disagree", "neutral"))

    def _required_selected_option(self, request: LLMRequest) -> dict[str, object] | None:
        if not request.caller_role.endswith(".chief_grand_secretary"):
            return None
        for message in reversed(request.messages):
            marker = "请你汇总3位官员的草拟决策，并为决策"
            if marker not in message.content:
                continue
            raw = message.content.split(marker, 1)[1].split("撰写对应决策理由", 1)[0].strip()
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if isinstance(value, dict):
                document = cast(dict[str, Any], value)
                if isinstance(document.get("option"), str):
                    return document
        return None


__all__ = ["FakeLLMClient"]
