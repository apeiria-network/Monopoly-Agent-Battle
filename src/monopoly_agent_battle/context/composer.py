"""10-segment prompt composer for Stage 4C.

Given an ``AgentConversation`` and the current ``DecisionRequest``, produce
the list of ``LLMMessage`` objects that will be sent to the LLM. The mapping
follows the Stage 4C-remake specification:

- Segments 1+2 (role + game rules) → one ``system`` message.
- Segments 3, 4 and 5-10 all render into ``user`` chunks; only ``assistant``
  entries (this AI's prior JSON replies + optional pending validation-failure
  reply) break the user accumulation. Consecutive user chunks are merged into
  a single user message so the LLM never receives two adjacent user messages.
- Segment 3 (compressed history events, viewer-scoped) is skipped when the
  conversation has no completed turns.
- Segment 4 replays the current action turn's entries in time order.
- Optional validation feedback is inserted as ``assistant(bad_reply)`` followed
  by the ``user(feedback)`` piece just before the segment 5-10 message.
- Segments 5-10 always terminate the prompt as (part of) the final user
  message.
"""

from __future__ import annotations

from monopoly_agent_battle.context.broadcast import render_event
from monopoly_agent_battle.context.conversation import (
    AgentConversation,
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

    # Segment 1 + 2 → single system message.
    messages.append(LLMMessage(role="system", content=render_system_prompt(request)))

    # User buffer collects segment 3 + segment 4 user pieces + segment 5-10;
    # only assistant chunks flush and break the merge, guaranteeing no two
    # adjacent user messages ever reach the LLM.
    buffer: list[str] = []

    # Segment 3 → one user chunk (merges with anything that follows).
    if conversation.segment3_sentences:
        buffer.append("## 历史事件播报\n" + "\n".join(conversation.segment3_sentences))

    # Segment 4 → stream from the current turn's entries in time order.
    if conversation.current_turn is not None:
        for entry in conversation.current_turn.entries:
            if isinstance(entry, EventEntry):
                sentence = render_event(entry.event, conversation.agent_id)
                if sentence is not None:
                    buffer.append(f"[第{entry.complete_round}轮] {sentence}")
            else:  # DecisionEntry
                buffer.append(entry.user_snapshot)
                messages.append(LLMMessage(role="user", content=_join(buffer)))
                buffer.clear()
                messages.append(LLMMessage(role="assistant", content=entry.assistant_reply))

    # Optional validation feedback: last decision's bad reply + feedback prompt.
    if conversation.pending_bad_reply is not None:
        assert conversation.pending_feedback is not None
        if buffer:
            messages.append(LLMMessage(role="user", content=_join(buffer)))
            buffer.clear()
        messages.append(LLMMessage(role="assistant", content=conversation.pending_bad_reply))
        buffer.append(conversation.pending_feedback)

    # Segments 5-10 (current request) merge into the trailing user message.
    buffer.append(render_current_user_message(request))
    messages.append(LLMMessage(role="user", content=_join(buffer)))

    return tuple(messages), conversation.segment3_warning


def _join(pieces: list[str]) -> str:
    return "\n\n".join(piece for piece in pieces if piece)
