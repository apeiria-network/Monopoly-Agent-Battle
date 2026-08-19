"""Conversation history manager for multi-turn Agent context (Stage 4C)."""

from __future__ import annotations

from dataclasses import dataclass, field

from monopoly_agent_battle.domain.models import GameEvent


@dataclass
class ConversationMessage:
    """One message in an Agent's conversation history."""

    role: str  # "user" | "assistant"
    content: str
    turn: int  # Agent action turn number
    round_num: int  # Global round number
    decision_id: str | None = None


@dataclass
class AgentConversation:
    """Maintains conversation history and window boundaries for one Agent.

    In Stage 4, agent_id = player_id (one BaselineAgent per player).
    In Stage 5, agent_id = player_id (one CourtAgent per player, with multiple internal AIs).
    The window tracks the Agent's (player's) recent action turns, not individual AI turns.
    """

    agent_id: str  # player_id in both Stage 4 and Stage 5
    window_turns: int = 3
    broadcast_history_turns: int = 10
    messages: list[ConversationMessage] = field(default_factory=lambda: [])
    action_turns: list[int] = field(default_factory=lambda: [])
    round_events: dict[int, list[GameEvent]] = field(default_factory=lambda: {})

    def get_window_boundary(self) -> int:
        """Return the starting turn number of the window.

        Returns 0 if all history is within the window.
        """
        if len(self.action_turns) <= self.window_turns:
            return 0
        return self.action_turns[-self.window_turns]

    def is_within_window(self, turn: int) -> bool:
        """Check if a turn is within the current window."""
        return turn >= self.get_window_boundary()

    def get_messages_in_window(self) -> list[ConversationMessage]:
        """Return messages within the current window."""
        boundary = self.get_window_boundary()
        return [msg for msg in self.messages if msg.turn >= boundary]

    def get_messages_outside_window(self) -> list[ConversationMessage]:
        """Return messages outside the current window."""
        boundary = self.get_window_boundary()
        return [msg for msg in self.messages if msg.turn < boundary]

    def get_broadcast_rounds(self) -> list[int]:
        """Return the list of global rounds to broadcast (most recent N rounds)."""
        all_rounds = sorted(self.round_events.keys())
        return all_rounds[-self.broadcast_history_turns :]

    def add_decision_request(
        self,
        turn: int,
        round_num: int,
        content: str,
        decision_id: str,
    ) -> None:
        """Add a user message for a decision request."""
        self.messages.append(
            ConversationMessage(
                role="user",
                content=content,
                turn=turn,
                round_num=round_num,
                decision_id=decision_id,
            )
        )
        if turn not in self.action_turns:
            self.action_turns.append(turn)

    def add_decision_response(self, decision_id: str, reasoning: str) -> None:
        """Add an assistant message for a decision response."""
        # Find the corresponding request to get turn and round_num
        request_msg = next(
            (msg for msg in reversed(self.messages) if msg.decision_id == decision_id),
            None,
        )
        if request_msg is None:
            msg = f"Cannot find request for decision_id {decision_id}"
            raise ValueError(msg)

        self.messages.append(
            ConversationMessage(
                role="assistant",
                content=reasoning,
                turn=request_msg.turn,
                round_num=request_msg.round_num,
                decision_id=decision_id,
            )
        )

    def add_round_events(self, round_num: int, events: list[GameEvent]) -> None:
        """Record events for a global round."""
        if round_num not in self.round_events:
            self.round_events[round_num] = []
        self.round_events[round_num].extend(events)
