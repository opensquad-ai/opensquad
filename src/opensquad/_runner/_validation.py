"""
Validation module — content leak and repetition detection functions.

Extracted from runner.py (AgentRunner validation methods) to reduce its size.
These functions detect problematic model output patterns.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def is_leaked_tool_params(text: str) -> bool:
    """Detect leaked tool parameters (JSON or XML parameter tags).

    Detects two leak scenarios:
    1. JSON format leak: starts with { and ends with }, first key is an ASCII identifier
    2. XML parameter tag leak: tool parameter tags appear without an outer <tool_call>
    """
    s = text.strip()
    if not s:
        return False

    # Detect JSON leak
    if s.startswith("{") and re.search(r"\}\s*$", s):
        if s == "{}":
            return True
        if re.match(r'^\{\s*"[a-zA-Z_][a-zA-Z0-9_]*"\s*:', s):
            logger.warning("[Validation] Detected leaked JSON parameters without <tool_call> wrapper")
            return True

    # Detect XML parameter tag leak
    system_tags = {
        "title",
        "thought",
        "think",
        "plan",
        "to_user",
        "to_user_reply",
        "to_system",
        "tool_call",
        "tool_result",
        "arguments",
        "state",
        "wake",
        "sleep",
        "option",
        "forward",
        "system_reminder",
        "func",
        "task_start",
        "task_complete",
        "task_failed",
    }

    xml_tags = re.findall(r"<([a-zA-Z_][a-zA-Z0-9_]*)>.*?</\1>", s, re.DOTALL | re.IGNORECASE)

    if xml_tags and "<tool_call" not in text:
        leaked_tags = [tag for tag in xml_tags if tag.lower() not in system_tags]
        if leaked_tags:
            logger.warning(
                "[Validation] Detected leaked XML parameter tags without <tool_call> wrapper: %s", leaked_tags
            )
            return True

    return False


def is_repeated_content(text: str, get_messages: Callable[[], list[dict[str, Any]]] | None = None) -> bool:
    """Detect repetitive output (stuttering) from lower-quality models.

    Args:
        text: The text to check for repetition.
        get_messages: Optional callable that returns session messages list
                      (used for cross-turn detection). If None, cross-turn
                      check is skipped.

    Returns:
        True if repetitive content is detected.
    """
    if not text or len(text) < 15:
        return False

    # Pattern 1: Adjacent string repetition
    match2 = re.search(r"(.{6,})\s*\1+", text, re.DOTALL)
    if match2:
        pattern = match2.group(1).strip()
        if len(pattern) >= 6 and any(c.isalnum() for c in pattern):
            logger.warning("[Validation] Detected repetitive output (2x): %s...", pattern[:50])
            return True

    match3 = re.search(r"(.{4,})\s*\1{2,}", text, re.DOTALL)
    if match3:
        pattern = match3.group(1).strip()
        if len(pattern) >= 4 and any(c.isalnum() for c in pattern):
            logger.warning("[Validation] Detected repetitive output (3x short): %s...", pattern[:50])
            return True

    # Pattern 2: High density of identical lines
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) > 2:
        counts = Counter(lines)
        most_common, count = counts.most_common(1)[0]
        if (count >= 2 and count > len(lines) * 0.6) or (count >= 3 and count > len(lines) * 0.4):
            if len(most_common) > 4:
                logger.warning("[Validation] Detected repetitive lines: %s", most_common[:50])
                return True

    # Pattern 3: Cross-turn repetition
    if get_messages is not None:
        current_clean = text.strip()
        history = get_messages()
        last_asst = None
        for msg in reversed(history):
            if msg.get("role") == "assistant":
                last_asst = msg.get("content", "").strip()
                break

        if last_asst and current_clean == last_asst:
            logger.warning("[Validation] Detected exact cross-turn repetition: %s...", current_clean[:50])
            return True

        # Pattern 4: Meta-repetition (looping apologies)
        loop_phrases = [
            "stuck in a repetition loop",
            "stuck in a loop",
            "apologize for the repetition",
            "breaking out of the loop",
        ]
        for phrase in loop_phrases:
            if phrase in current_clean.lower() and last_asst and phrase in last_asst.lower():
                logger.warning("[Validation] Detected meta-repetition (looping apologies): %s", phrase)
                return True

    return False
