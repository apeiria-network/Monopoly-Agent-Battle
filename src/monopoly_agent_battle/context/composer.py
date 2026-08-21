"""10-segment prompt composer for Stage 4C.

Given an ``AgentConversation`` and the current ``DecisionRequest``, produce
the list of ``LLMMessage`` objects that will be sent to the LLM. The mapping
follows the Stage 4C-remake specification:

- Segments 1+2 plus the fixed output contract → one ``system`` message.
- Segments 3, 4 and 5-9 all render into ``user`` chunks; only ``assistant``
  entries (this AI's prior JSON replies + in-turn validation-failed replies)
  break the user accumulation. Consecutive user chunks merge into a single
  user message so the LLM never receives two adjacent user messages.
- Segment 3 (compressed inter-turn history events, viewer-scoped) is skipped
  when the conversation has no completed turns. It never carries error
  entries — errors are per-turn only.
- Segment 4 replays the current action turn's entries in time order:
  ``EventEntry`` → user broadcast; ``ErrorEntry`` → assistant(bad_reply) then
  a user(feedback) chunk; ``DecisionEntry`` → user(snapshot) then
  assistant(reply). Errors stay visible for every decision in this turn.
- Segments 5-9 always terminate the prompt as (part of) the final user
  message.
"""

from __future__ import annotations

from monopoly_agent_battle.context.broadcast import render_event
from monopoly_agent_battle.context.conversation import (
    AgentConversation,
    DecisionEntry,
    ErrorEntry,
    EventEntry,
)
from monopoly_agent_battle.context.token_guard import ContextWarning
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.prompts import (
    render_current_user_message,
    render_system_prompt,
)
from monopoly_agent_battle.llm.protocol import LLMMessage


def compose_prompt(
    conversation: AgentConversation,
    request: DecisionRequest,
) -> tuple[tuple[LLMMessage, ...], ContextWarning | None]:
    """Assemble the 10-segment prompt into a message list.

    Returns ``(messages, warning?)``. The warning is the segment-3 overflow
    advisory (if any) attached by ``AgentConversation.start_turn``; callers
    should log it to ``runtime.jsonl`` — never surface it to the Agent.
    """
    messages: list[LLMMessage] = []

    messages.append(LLMMessage(role="system", content=render_system_prompt(request)))

    buffer: list[str] = []

    if conversation.segment3_sentences:
        buffer.append("## 历史事件播报\n" + "\n".join(conversation.segment3_sentences))

    if conversation.current_turn is not None:
        last_flushed_decision_id: str | None = None
        event_lines: list[str] = []

        def flush_event_block() -> None:
            if event_lines:
                buffer.append("\n".join(event_lines))
                event_lines.clear()

        for entry in conversation.current_turn.entries:
            if isinstance(entry, EventEntry):
                sentence = render_event(entry.event, conversation.agent_id)
                if sentence is not None:
                    event_lines.append(f"[第{entry.complete_round}轮] {sentence}")
            elif isinstance(entry, ErrorEntry):
                flush_event_block()
                if entry.decision_id != last_flushed_decision_id:
                    buffer.append(entry.question_summary)
                    last_flushed_decision_id = entry.decision_id
                messages.append(LLMMessage(role="user", content=_join(buffer)))
                buffer.clear()
                messages.append(LLMMessage(role="assistant", content=entry.bad_reply))
                buffer.append(entry.feedback_text)
            else:
                assert isinstance(entry, DecisionEntry)
                flush_event_block()
                if entry.decision_id != last_flushed_decision_id:
                    buffer.append(entry.question_summary)
                    last_flushed_decision_id = entry.decision_id
                messages.append(LLMMessage(role="user", content=_join(buffer)))
                buffer.clear()
                messages.append(LLMMessage(role="assistant", content=entry.assistant_reply))

        flush_event_block()

    buffer.append(render_current_user_message(request))
    messages.append(LLMMessage(role="user", content=_join(buffer)))

    return tuple(messages), conversation.segment3_warning


def _join(pieces: list[str]) -> str:
    return "\n\n".join(piece for piece in pieces if piece)
