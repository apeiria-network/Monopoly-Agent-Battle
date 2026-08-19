"""10-segment prompt composer for Stage 4C.

Given an ``AgentConversation`` and the current ``DecisionRequest``, produce
the list of ``LLMMessage`` objects that will be sent to the LLM. The mapping
follows the Stage 4C-remake specification:

- Segments 1+2 (role + game rules) → one ``system`` message.
- Segment 3 (compressed history events, viewer-scoped) → one ``user`` message
  if the conversation has any completed turns; otherwise skipped.
- Segment 4 (current action turn: prior decisions + between-decision events)
  → interleaved messages. ``assistant`` breaks user accumulation; consecutive
  user pieces merge into a single message.
- Optional validation-failure feedback: appended to segment 4 as
  ``assistant(bad_reply)`` then ``user(feedback)`` before the final segment 5-10.
- Segments 5-10 (latest state + current decision + candidates + output guide)
  → merged into the last ``user`` message (concatenates onto any pending user
  buffer from segment 4).

Segments 3 and 4 are omitted for the very first decision (no completed turns
and no prior entries in the current turn).
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

    # Segment 3 → one user message if there is compressed history.
    if conversation.segment3_sentences:
        messages.append(
            LLMMessage(
                role="user",
                content="## 历史事件播报\n" + "\n".join(conversation.segment3_sentences),
            )
        )

    # Segment 4 → stream from the current turn's entries; consecutive user
    # chunks merge; assistant chunks break the merge.
    buffer: list[str] = []
    if conversation.current_turn is not None:
        for entry in conversation.current_turn.entries:
            if isinstance(entry, EventEntry):
                sentence = render_event(entry.event, conversation.agent_id)
                if sentence is not None:
                    buffer.append(sentence)
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
