"""Token estimation and budget protection for context assembly (Stage 4C)."""

from __future__ import annotations


class TokenLimitExceededError(Exception):
    """Raised when protected segments exceed the token budget."""


def estimate_tokens(text: str) -> int:
    """Estimate token count using a simplified heuristic.

    Rules:
    - Chinese characters (CJK Unified Ideographs): ~1.5 tokens/char
    - Other characters (English, digits, punctuation): ~0.3 tokens/char

    This is a deterministic approximation that doesn't require external
    tokenizer libraries like tiktoken. Accuracy within ±20% is acceptable
    since trimming is gradual.

    Args:
        text: The text to estimate

    Returns:
        Estimated token count
    """
    chinese_chars = sum(1 for c in text if "一" <= c <= "鿿")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.3)


def apply_token_limit(
    segments: dict[str, str],
    token_cap: int,
    protected_segments: frozenset[str] = frozenset(
        {"system", "rules", "current_state", "current_decision"}
    ),
) -> dict[str, str]:
    """Trim segments to fit within token budget while protecting critical content.

    Trimming strategy:
    1. Estimate total tokens across all segments
    2. If over budget, trim "broadcast_history" first (by removing earliest rounds)
    3. If still over, trim "conversation_history" (by removing earliest turns)
    4. If protected segments alone exceed budget, raise TokenLimitExceededError

    Args:
        segments: Segment dictionary with keys like "system", "rules",
                  "broadcast_history", "conversation_history", "current_state",
                  "current_decision"
        token_cap: Maximum allowed tokens
        protected_segments: Segments that must never be trimmed

    Returns:
        Trimmed segments dictionary

    Raises:
        TokenLimitExceededError: If protected segments exceed token_cap
    """
    # Estimate current usage
    total_tokens = sum(estimate_tokens(content) for content in segments.values())

    if total_tokens <= token_cap:
        return segments

    # Check if protected segments alone exceed budget
    protected_tokens = sum(
        estimate_tokens(segments.get(key, "")) for key in protected_segments if key in segments
    )

    if protected_tokens > token_cap:
        raise TokenLimitExceededError(
            f"Protected segments require {protected_tokens} tokens, "
            f"but token_cap is {token_cap}. "
            f"Consider increasing context_token_cap or simplifying rules."
        )

    # Create a mutable copy
    trimmed = dict(segments)

    # Phase 1: Trim broadcast_history (window-out history)
    if "broadcast_history" in trimmed:
        trimmed = _trim_broadcast_history(trimmed, token_cap, protected_segments)
        total_tokens = sum(estimate_tokens(content) for content in trimmed.values())

    # Phase 2: Trim conversation_history (window-in history)
    if total_tokens > token_cap and "conversation_history" in trimmed:
        trimmed = _trim_conversation_history(trimmed, token_cap, protected_segments)

    return trimmed


def _trim_broadcast_history(
    segments: dict[str, str],
    token_cap: int,
    protected_segments: frozenset[str],
) -> dict[str, str]:
    """Trim broadcast_history by removing earliest rounds until under budget."""
    content = segments.get("broadcast_history", "")
    if not content:
        return segments

    # Split by round markers like "[第1轮]"
    lines = content.strip().split("\n")
    round_groups: list[tuple[int, list[str]]] = []
    current_round = -1
    current_lines: list[str] = []

    for line in lines:
        # Detect round marker
        if line.startswith("[第") and "轮]" in line:
            if current_lines:
                round_groups.append((current_round, current_lines))
            # Extract round number
            try:
                round_num = int(line.split("第")[1].split("轮")[0])
                current_round = round_num
                current_lines = [line]
            except (IndexError, ValueError):
                current_lines.append(line)
        else:
            current_lines.append(line)

    # Add last group
    if current_lines:
        round_groups.append((current_round, current_lines))

    # Try progressively fewer rounds
    for num_rounds_to_keep in range(len(round_groups), 0, -1):
        kept_groups = round_groups[-num_rounds_to_keep:]
        new_content = "\n".join("\n".join(lines) for _, lines in kept_groups).strip()

        test_segments = dict(segments)
        test_segments["broadcast_history"] = new_content

        total = sum(estimate_tokens(c) for c in test_segments.values())
        if total <= token_cap:
            return test_segments

    # Remove broadcast_history entirely if nothing fits
    result = dict(segments)
    del result["broadcast_history"]
    return result


def _trim_conversation_history(
    segments: dict[str, str],
    token_cap: int,
    protected_segments: frozenset[str],
) -> dict[str, str]:
    """Trim conversation_history by removing earliest turns until under budget."""
    content = segments.get("conversation_history", "")
    if not content:
        return segments

    # Split by turn markers like "### 回合 1"
    lines = content.strip().split("\n")
    turn_groups: list[tuple[int, list[str]]] = []
    current_turn = -1
    current_lines: list[str] = []

    for line in lines:
        # Detect turn marker
        if line.startswith("### 回合"):
            if current_lines:
                turn_groups.append((current_turn, current_lines))
            # Extract turn number
            try:
                turn_num = int(line.split("回合")[1].strip())
                current_turn = turn_num
                current_lines = [line]
            except (IndexError, ValueError):
                current_lines.append(line)
        else:
            current_lines.append(line)

    # Add last group
    if current_lines:
        turn_groups.append((current_turn, current_lines))

    # Try progressively fewer turns
    for num_turns_to_keep in range(len(turn_groups), 0, -1):
        kept_groups = turn_groups[-num_turns_to_keep:]
        new_content = "\n".join("\n".join(lines) for _, lines in kept_groups).strip()

        test_segments = dict(segments)
        test_segments["conversation_history"] = new_content

        total = sum(estimate_tokens(c) for c in test_segments.values())
        if total <= token_cap:
            return test_segments

    # Remove conversation_history entirely if nothing fits
    result = dict(segments)
    del result["conversation_history"]
    return result
