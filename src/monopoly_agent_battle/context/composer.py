"""Assemble 10-segment prompts with conversation history (Stage 4C).

Segment structure:
1. System instruction (role & goal)
2. Game rules
3. Broadcast history (window-out, compressed)
4. Conversation history (window-in, full replay)
5. Your state
6. Other players state
7. Board state
8. Current decision
9. Legal options
10. Output requirements
"""

from __future__ import annotations

from typing import Any

from monopoly_agent_battle.context.broadcast import render_event
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.context.rules import load_game_rules
from monopoly_agent_battle.context.token_guard import apply_token_limit
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.prompts import (
    _OUTPUT_GUIDE,  # pyright: ignore[reportPrivateUsage]
    PLAYER_INSTRUCTION,
    _json,  # pyright: ignore[reportPrivateUsage]
    _render_decision,  # pyright: ignore[reportPrivateUsage]
    _render_response_format,  # pyright: ignore[reportPrivateUsage]
    _render_situation,  # pyright: ignore[reportPrivateUsage]
)
from monopoly_agent_battle.llm.protocol import LLMMessage


def compose_prompt(
    conversation: AgentConversation,
    decision_request: DecisionRequest,
    validation_feedback: str | None = None,
    token_cap: int | None = None,
) -> list[LLMMessage]:
    """Compose the complete 10-segment prompt with conversation history.

    Args:
        conversation: The AI's conversation history
        decision_request: Current decision request
        validation_feedback: Temporary feedback for validation retry (if any)
        token_cap: Maximum token budget (optional)

    Returns:
        List of LLMMessage in OpenAI format (system + user/assistant alternating)
    """
    # Segment 1 & 2: System prompt + Game rules
    visible = decision_request.visible_state
    your_state: dict[str, Any] = visible["your_state"]  # type: ignore[assignment]
    system_content = _compose_system_segment(
        decision_request.player_id,
        your_state["seat"],
    )

    # Check if we have history
    has_history = len(conversation.messages) > 0

    # Segment 3 & 4: History segments (only if has_history)
    broadcast_history = ""
    conversation_history_messages: list[LLMMessage] = []

    if has_history:
        # Segment 3: Window-out broadcast history
        broadcast_history = _compose_broadcast_history(conversation)

        # Segment 4: Window-in conversation replay
        conversation_history_messages = _compose_conversation_history(conversation)

    # Segment 5-10: Current state and decision
    current_content = _compose_current_segments(decision_request, validation_feedback)

    # Assemble segments dictionary for token protection
    segments = {
        "system": system_content,
        "current_state": current_content,
    }

    if broadcast_history:
        segments["broadcast_history"] = broadcast_history

    # Convert conversation history messages to a single string for token guard
    if conversation_history_messages:
        conversation_history_text = "\n\n".join(
            msg.content for msg in conversation_history_messages
        )
        segments["conversation_history"] = conversation_history_text

    # Apply token limit if specified
    if token_cap is not None:
        segments = apply_token_limit(
            segments,
            token_cap,
            protected_segments=frozenset({"system", "current_state"}),
        )

    # Build final message list
    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=segments["system"]),
    ]

    # Add broadcast history as first user message if present
    if "broadcast_history" in segments and segments["broadcast_history"]:
        messages.append(
            LLMMessage(
                role="user",
                content=f"## 历史事件播报\n\n{segments['broadcast_history']}",
            )
        )
        # Add a brief assistant acknowledgment
        messages.append(LLMMessage(role="assistant", content="我已了解历史事件。"))

    # Add conversation history messages (window-in)
    # Rebuild from trimmed segment if it was modified by token guard
    if "conversation_history" in segments and segments["conversation_history"]:
        # Split back into turns and rebuild messages
        trimmed_text = segments["conversation_history"]
        lines = trimmed_text.split("\n")
        current_role = "user"
        current_content_lines: list[str] = []

        for line in lines:
            if line.startswith("### 回合"):
                # Start of a user message
                if current_content_lines:
                    # Flush previous message
                    messages.append(
                        LLMMessage(role=current_role, content="\n".join(current_content_lines))
                    )
                    current_content_lines = []
                current_role = "user"
                current_content_lines.append(line)
            elif line.strip() and not line.startswith("### 回合"):
                current_content_lines.append(line)
                # After user message comes assistant message
                # Heuristic: if we've accumulated content and hit an empty line, switch role
                if current_role == "user" and len(current_content_lines) > 3:
                    # Look ahead for role switch indicator
                    pass  # Simple approach: alternate user/assistant based on turn markers

        # Flush last message
        if current_content_lines:
            messages.append(LLMMessage(role=current_role, content="\n".join(current_content_lines)))
    elif conversation_history_messages:
        # No trimming occurred, use original messages
        messages.extend(conversation_history_messages)

    # Add current decision as final user message
    messages.append(LLMMessage(role="user", content=segments["current_state"]))

    return messages


def _compose_system_segment(player_id: str, seat: int) -> str:
    """Compose segment 1 (system instruction) and segment 2 (game rules)."""
    instruction = PLAYER_INSTRUCTION.format(player_id=player_id, seat=seat)
    rules = load_game_rules()

    return f"{instruction}\n\n## 游戏规则\n\n{rules}"


def _compose_broadcast_history(conversation: AgentConversation) -> str:
    """Compose segment 3: window-out broadcast history (compressed sentences)."""
    window_boundary = conversation.get_window_boundary()
    broadcast_rounds = conversation.get_broadcast_rounds()

    lines: list[str] = []

    for round_num in broadcast_rounds:
        # Only include rounds outside the window
        round_events = conversation.round_events.get(round_num, [])
        if not round_events:
            continue

        # Check if this round has any action turns within window
        round_turns = [
            turn
            for turn in conversation.action_turns
            if any(msg.round_num == round_num and msg.turn == turn for msg in conversation.messages)
        ]

        # If any turn in this round is within window, skip this round entirely
        # (it will be shown in full in segment 4)
        if any(turn >= window_boundary for turn in round_turns):
            continue

        # Render events from this round using broadcast renderer
        round_lines: list[str] = []
        for event in round_events:
            sentence = render_event(event, conversation.agent_id)
            if sentence:
                round_lines.append(sentence)

        if round_lines:
            lines.append(f"[第{round_num}轮]")
            lines.extend(round_lines)

    return "\n".join(lines) if lines else ""


def _compose_conversation_history(
    conversation: AgentConversation,
) -> list[LLMMessage]:
    """Compose segment 4: window-in conversation history (full replay)."""
    window_messages = conversation.get_messages_in_window()

    # Group messages by turn for structured replay
    messages_by_turn: dict[int, list[Any]] = {}
    for msg in window_messages:
        if msg.turn not in messages_by_turn:
            messages_by_turn[msg.turn] = []
        messages_by_turn[msg.turn].append(msg)

    llm_messages: list[LLMMessage] = []

    for turn in sorted(messages_by_turn.keys()):
        turn_msgs = messages_by_turn[turn]
        for msg in turn_msgs:
            # Add turn marker to user messages
            if msg.role == "user":
                content = f"### 回合 {turn}\n\n{msg.content}"
            else:
                content = msg.content

            llm_messages.append(LLMMessage(role=msg.role, content=content))

    return llm_messages


def _compose_current_segments(
    request: DecisionRequest,
    validation_feedback: str | None,
) -> str:
    """Compose segments 5-10: current state, decision, options, output guide."""
    visible = request.visible_state

    # Segment 5-7: Current situation (your state, other players, board)
    situation = _render_situation(visible)

    # Segment 8: Current decision
    decision = _render_decision(request, visible)

    # Segment 9: Legal options
    options = [
        {
            "option_id": option.option_id,
            "title": option.title,
            "preview": option.preview,
            "response_format": _render_response_format(option.response_format, option.option_id),
        }
        for option in request.options
    ]
    options_text = _json(options)

    # Segment 10: Output requirements
    output_guide = _OUTPUT_GUIDE

    # Assemble
    parts = [
        f"## 当前局面\n{situation}",
        f"## 当前决策\n{decision}",
        f"## 合法候选操作\n{options_text}",
        f"## 输出要求\n{output_guide}",
    ]

    # Add validation feedback if present
    if validation_feedback:
        parts.append(f"\n\n## 上次输出反馈\n\n{validation_feedback}")

    return "\n\n".join(parts)
