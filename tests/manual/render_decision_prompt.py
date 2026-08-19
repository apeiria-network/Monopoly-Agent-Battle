"""Render decision prompt scenarios for human inspection.

Run from the repository root:
    .venv/Scripts/python.exe tests/manual/render_decision_prompt.py

Prints four scenarios that illustrate different composer behaviours:

  A – first decision in a turn, no conversation history.
  B – window-in replay: 4 prior action turns, window=3.
  C – multi-decision within same turn, situation CHANGED.
  D – multi-decision within same turn, situation UNCHANGED.
"""

from __future__ import annotations

import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.composer import compose_prompt
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.requests import build_decision_request
from monopoly_agent_battle.domain.models import GameEvent, TurnPhase
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.llm.protocol import LLMMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIVIDER = "=" * 60


def _print_header(label: str, title: str) -> None:
    print(f"\n{_DIVIDER}")
    print(f"SCENARIO {label}: {title}")
    print(_DIVIDER)


def _print_messages(messages: list[LLMMessage]) -> None:
    for i, msg in enumerate(messages, 1):
        print(f"\n--- Message {i} [{msg.role}] ---")
        print(msg.content)


def _make_engine(directory: str) -> GameEngine:
    """Return a configured GameEngine with a minimal asset-management state."""
    config = GameConfig(
        game_id="prompt-inspection",
        experiment_id="manual-review",
        seed=1,
        players=(
            PlayerConfig(player_id="a", seat=1),
            PlayerConfig(player_id="b", seat=2),
        ),
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=Path(directory),
    )
    engine = GameEngine(config)
    engine.state.turn_phase = TurnPhase.ASSET_MANAGEMENT
    engine.state.properties[1].owner_id = "a"
    engine.state.players["a"].properties.add(1)
    engine.state.properties[3].owner_id = "b"
    engine.state.players["b"].properties.add(3)
    engine.state.players["a"].chance_cards.append("chance-swap-property")
    return engine


# ---------------------------------------------------------------------------
# Scenario A – first decision, no history
# ---------------------------------------------------------------------------


def scenario_a(directory: str) -> None:
    _print_header("A", "First decision in a turn — no history")
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=1)
    conversation = AgentConversation(agent_id="a", window_turns=3)
    messages = compose_prompt(conversation, request)
    _print_messages(messages)


# ---------------------------------------------------------------------------
# Scenario B – window-in replay (4 prior turns, window=3)
# ---------------------------------------------------------------------------

_PRIOR_CONTENT = (
    "## 当前局面\n[局面快照-回合{turn}]\n\n"
    "## 当前决策\n资产管理阶段\n\n"
    "## 合法候选操作\n[候选列表]\n\n"
    "## 输出要求\n[输出要求]"
)


def scenario_b(directory: str) -> None:
    _print_header("B", "Window-in replay — 4 prior turns, window=3 → boundary=turn 2")
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=5)

    conversation = AgentConversation(agent_id="a", window_turns=3)

    # Round events for rounds 1-4 (all get registered; only round 1 ends up in
    # segment 3 because turns 2-4 are inside the window).
    conversation.add_round_events(
        1,
        [
            GameEvent(event_type="turn_started", payload={"player_id": "a"}),
            GameEvent(event_type="dice_rolled", payload={"player_id": "a", "dice": [3, 4]}),
            GameEvent(event_type="player_moved", payload={"player_id": "a", "to": 7}),
            GameEvent(event_type="turn_ended", payload={"player_id": "a"}),
        ],
    )
    conversation.add_round_events(
        2,
        [
            GameEvent(event_type="turn_started", payload={"player_id": "a"}),
            GameEvent(event_type="dice_rolled", payload={"player_id": "a", "dice": [2, 5]}),
            GameEvent(event_type="player_moved", payload={"player_id": "a", "to": 14}),
            GameEvent(
                event_type="property_purchased",
                payload={"player_id": "a", "position": 14, "price": 140},
            ),
            GameEvent(event_type="turn_ended", payload={"player_id": "a"}),
        ],
    )
    conversation.add_round_events(
        3,
        [
            GameEvent(event_type="turn_started", payload={"player_id": "a"}),
            GameEvent(event_type="dice_rolled", payload={"player_id": "a", "dice": [1, 6]}),
            GameEvent(event_type="player_moved", payload={"player_id": "a", "to": 21}),
            GameEvent(event_type="turn_ended", payload={"player_id": "a"}),
        ],
    )
    conversation.add_round_events(
        4,
        [
            GameEvent(event_type="turn_started", payload={"player_id": "a"}),
            GameEvent(event_type="dice_rolled", payload={"player_id": "a", "dice": [4, 4]}),
            GameEvent(event_type="player_moved", payload={"player_id": "a", "to": 29}),
            GameEvent(event_type="turn_ended", payload={"player_id": "a"}),
        ],
    )

    # Add 4 prior action turns; window_turns=3 so boundary = action_turns[-3] = turn 2.
    # Turn 1 is outside the window (segment 3); turns 2-4 are in the window (segment 4).
    for turn_num in range(1, 5):
        decision_id = f"decision-prompt-inspection-{turn_num:06d}"
        conversation.add_decision_request(
            turn=turn_num,
            round_num=turn_num,
            content=_PRIOR_CONTENT.format(turn=turn_num),
            decision_id=decision_id,
        )
        conversation.add_decision_response(
            decision_id=decision_id,
            reasoning=(
                f'{{"selected_option": "end_turn", "target": null, '
                f'"reason": "第{turn_num}回合结束策略。"}}'
            ),
        )

    messages_b = compose_prompt(conversation, request)
    _print_messages(messages_b)


# ---------------------------------------------------------------------------
# Scenario C – multi-decision, situation CHANGED
# ---------------------------------------------------------------------------


def scenario_c(directory: str) -> None:
    _print_header(
        "C",
        "Multi-decision within same turn — situation CHANGED (full state repeated)",
    )
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=1)
    conversation = AgentConversation(agent_id="a", window_turns=3)

    # Snapshot of visible state BEFORE the first decision is made.
    old_visible: dict[str, Any] = copy.deepcopy(dict(request.visible_state))

    print("\n[First call — full state, no previous_visible_state]")
    messages_first = compose_prompt(conversation, request)
    _print_messages(messages_first)

    # Simulate that a mortgage occurred between the two decisions.
    # The "old" state had 30 less cash; the current request already has the updated
    # (post-mortgage) state, so we mutate old_visible to look like the pre-mortgage snapshot.
    old_your_state: dict[str, Any] = dict(old_visible["your_state"])
    old_your_state["cash"] = int(old_your_state["cash"]) - 30
    old_visible["your_state"] = old_your_state

    between_events = [
        GameEvent(
            event_type="property_mortgaged",
            payload={"player_id": "a", "position": 1, "amount": 30},
        ),
    ]

    print(
        "\n[Second call — previous_visible_state differs → include_state=True, "
        "event broadcast prepended]"
    )
    messages_second = compose_prompt(
        conversation,
        request,
        previous_visible_state=old_visible,
        between_events=between_events,
    )
    _print_messages(messages_second)


# ---------------------------------------------------------------------------
# Scenario D – multi-decision, situation UNCHANGED
# ---------------------------------------------------------------------------


_PRIOR_CONTENT_D = (
    "## 当前局面\n[局面快照-D回合{turn}]\n\n"
    "## 当前决策\n资产管理阶段\n\n"
    "## 合法候选操作\n[候选列表]\n\n"
    "## 输出要求\n[输出要求]"
)


def scenario_d(directory: str) -> None:
    _print_header(
        "D",
        "Multi-decision within same turn — situation UNCHANGED (state omitted)",
    )
    engine = _make_engine(directory)
    request = build_decision_request(engine, sequence=2)
    conversation = AgentConversation(agent_id="a", window_turns=3)

    # Add one prior action turn (the first decision of this same turn) so that
    # segment 4 carries a full user message with state.  The current call is the
    # SECOND decision of the turn; because the visible state is unchanged,
    # segments 5-7 are omitted from the final user message.
    prior_decision_id = "decision-prompt-inspection-d-000001"
    conversation.add_decision_request(
        turn=1,
        round_num=1,
        content=_PRIOR_CONTENT_D.format(turn=1),
        decision_id=prior_decision_id,
    )
    conversation.add_decision_response(
        decision_id=prior_decision_id,
        reasoning='{"reasoning": "继续资产管理。", "option_id": "end_turn", "response": {}}',
    )

    # Identical deep copy → comparison passes → include_state=False
    same_visible: dict[str, Any] = copy.deepcopy(dict(request.visible_state))

    print(
        "\n[Call — previous_visible_state identical → include_state=False, "
        "no state section in output; segment 4 has prior turn with full state]"
    )
    messages_d = compose_prompt(
        conversation,
        request,
        previous_visible_state=same_visible,
        between_events=[],
    )
    _print_messages(messages_d)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run all four scenarios sequentially."""
    with TemporaryDirectory() as directory:
        scenario_a(directory)
        scenario_b(directory)
        scenario_c(directory)
        scenario_d(directory)


if __name__ == "__main__":
    main()
