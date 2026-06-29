# -*- coding: utf-8 -*-
"""
Result formatting module — functions for formatting tool execution results.

Extracted from runner.py to reduce its size.
"""
from __future__ import annotations

import os
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


def truncate_result_text(text: str, max_len: int | None) -> str:
    """Truncate text to max_len chars, preserving head and tail portions.
    If max_len is None or <= 0, no truncation is applied.
    """
    if max_len is None or max_len <= 0 or len(text) <= max_len:
        return text
    if max_len >= 50000:
        return text[:25000] + "...[truncated]..." + text[-10000:]
    else:
        return text[:1000] + "...[truncated]..." + text[-500:]


def get_tool_output_max_chars(config_path: str) -> int:
    """Read tool_output_max_chars from agent config.json.
    Returns 0 for no limit; defaults to 50000 chars if not configured.
    """
    from opensquad.json_cache import load_json_cached
    try:
        if config_path and os.path.isfile(config_path):
            cfg = load_json_cached(config_path, default={})
            val = cfg.get("model", {}).get("tool_output_max_chars")
            if val is not None:
                v = int(val)
                if v < 0:
                    return 0
                return v
    except (ValueError, TypeError, AttributeError):
        pass
    return 50000


def summarize_result(name: str, result: Any, config_path: str = "") -> str:
    """Format a tool execution result into a string suitable for LLM context.

    Args:
        name: Tool name.
        result: Tool result (string, dict, or any).
        config_path: Path to agent config.json (used to read tool_output_max_chars).

    Returns:
        Formatted result string with timestamp.
    """
    now = datetime.now().strftime("%H:%M:%S")

    # MCP multimodal result (contains screenshots): keep only the text portion
    if isinstance(result, dict) and result.get("__mcp_multimodal__"):
        text = result.get("text", "")
        img_count = len(result.get("images", []))
        res_str = f"{text} [+{img_count} screenshot(s) attached]"
    else:
        res_str = str(result)

    # Skill-related reads should preserve content to avoid cutting SKILL.md
    tool_name = (name or "").lower()
    is_skill_read = "read_skill" in tool_name or "activate_skill" in tool_name
    is_skill_related = is_skill_read or "skill" in tool_name

    if is_skill_read:
        max_len = None
    elif is_skill_related:
        max_len = 50000
    else:
        max_len = get_tool_output_max_chars(config_path)
        max_len = max_len if max_len > 0 else None

    res_str = truncate_result_text(res_str, max_len)
    return f"[{now}] Tool '{name}' executed. Result: {res_str}"
