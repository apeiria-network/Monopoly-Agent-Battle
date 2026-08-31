"""Per-player performance windows for court decision evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.game.rules.classic_level0 import net_worth
from monopoly_agent_battle.performance.scoring import (
    DecisionEvidence,
    PerformanceWindow,
    PerformanceWindowResult,
    score_window,
)


@dataclass(slots=True)
class _PlayerHistory:
    turn: int = 0
    snapshots: dict[int, int] = field(default_factory=lambda: {})
    decisions: dict[int, list[DecisionEvidence]] = field(default_factory=lambda: {})
    results: list[PerformanceWindowResult] = field(default_factory=lambda: [])


class PerformanceTracker:
    """Track court-player windows without touching engine randomness or state."""

    def __init__(self, engine: Any, court_types: dict[str, str]) -> None:
        self._engine = engine
        self._court_types = dict(court_types)
        self._history = {player: _PlayerHistory() for player in court_types}

    @property
    def game_id(self) -> str:
        return str(self._engine.config.game_id)

    def start_turn(self, player_id: str) -> list[PerformanceWindowResult]:
        history = self._history.get(player_id)
        if history is None:
            return []
        current_turn = history.turn + 1
        current_worth = net_worth(self._engine.state.players[player_id], self._engine.state)
        results: list[PerformanceWindowResult] = []
        if history.turn >= 1:
            results.append(
                self._score(player_id, PerformanceWindow.BASIC, history.turn, current_turn)
            )
        if history.turn >= 3:
            results.append(
                self._score(player_id, PerformanceWindow.LONG_TERM, history.turn - 2, current_turn)
            )
        history.turn = current_turn
        history.snapshots[current_turn] = current_worth
        history.decisions.setdefault(current_turn, [])
        history.results.extend(results)
        return results

    def record_decision(self, player_id: str, evidence: DecisionEvidence) -> None:
        history = self._history.get(player_id)
        if history is not None:
            history.decisions.setdefault(history.turn, []).append(evidence)

    def all_results(self) -> list[PerformanceWindowResult]:
        return [result for history in self._history.values() for result in history.results]

    def current_text(self, player_id: str) -> str | None:
        history = self._history.get(player_id)
        if history is None or not history.results:
            return None
        lines = ["## 官员绩效"]
        for result in history.results[-2:]:
            lines.append(
                f"{result.window.value}（第{result.start_turn}至第{result.end_turn}个行动回合，"
                f"净资产变化 {result.delta}）："
            )
            for officer, assessment in result.assessments.items():
                lines.append(
                    f"{officer}：C={assessment['consistent_count']}/N={assessment['decision_count']}，"
                    f"{'记为差评' if assessment['bad_review'] else '不记差评'}。"
                )
            if result.no_scorable_officers:
                lines.append("无可评分官员。")
        return "\n".join(lines)

    def _score(
        self, player_id: str, window: PerformanceWindow, start: int, end: int
    ) -> PerformanceWindowResult:
        history = self._history[player_id]
        officers = _officers(self._court_types[player_id])
        decisions = [
            evidence for turn in range(start, end) for evidence in history.decisions.get(turn, [])
        ]
        start_worth = history.snapshots[start]
        end_worth = net_worth(self._engine.state.players[player_id], self._engine.state)
        return score_window(
            game_id=self.game_id,
            player_id=player_id,
            court=self._court_types[player_id],
            window=window,
            start_turn=start,
            end_turn=end,
            start_net_worth=start_worth,
            end_net_worth=end_worth,
            decisions=decisions,
            officers=officers,
        )


def _officers(court: str) -> tuple[str, ...]:
    return {
        "shang": (),
        "qin": ("chancellor", "grand_marshal"),
        "tang": ("zhongshu", "menxia"),
        "ming": ("chief_grand_secretary", "grand_secretary_1", "grand_secretary_2"),
        "shang_court": (),
        "qin_court": ("chancellor", "grand_marshal"),
        "tang_court": ("zhongshu", "menxia"),
        "ming_court": ("chief_grand_secretary", "grand_secretary_1", "grand_secretary_2"),
    }.get(court, ())


def evidence_from_trace(
    request: DecisionRequest,
    trace: dict[str, object],
    emperor_option: str,
    emperor_target: dict[str, object] | None,
) -> DecisionEvidence | None:
    import json

    from monopoly_agent_battle.performance.evidence import signature_from_reply
    from monopoly_agent_battle.performance.scoring import DecisionSignature

    emperor = DecisionSignature.from_parts(emperor_option, emperor_target)
    court = str(trace.get("court"))
    raw_calls = trace.get("calls", [])
    if not isinstance(raw_calls, list):
        return None
    calls = cast(list[object], raw_calls)
    typed_calls = [
        cast(Mapping[str, object], item)
        for item in calls
        if isinstance(item, dict)
    ]
    signatures: dict[str, DecisionSignature | None] = {}
    special: dict[str, bool] = {}
    roles = _officers(court)
    for role in roles:
        candidates: list[Mapping[str, object]] = [
            item
            for item in typed_calls
            if item.get("role") == role
            and item.get("outcome") in {"success", "advice_normalized"}
            and isinstance(item.get("content"), str)
        ]
        if court == "tang" and role == "zhongshu":
            candidates = [item for item in candidates if item.get("content_type") == "draft"]
        if court == "ming" and role == "chief_grand_secretary":
            candidates = [item for item in candidates if item.get("content_type") == "draft"]
        if not candidates:
            signatures[role] = None
            continue
        raw = candidates[-1]["content"]
        assert isinstance(raw, str)
        if court == "tang" and role == "menxia":
            try:
                value = json.loads(raw)
                verdict = value["selected_option"]["option"]
            except (TypeError, KeyError, json.JSONDecodeError):
                signatures[role] = None
            else:
                special[role] = (verdict == "agree") == (signatures.get("zhongshu") == emperor)
            continue
        signatures[role] = signature_from_reply(request, raw)
    return DecisionEvidence(str(request.decision_id), emperor, signatures, special or None)
