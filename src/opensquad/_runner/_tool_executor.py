# -*- coding: utf-8 -*-
"""
Tool executor module -- tool call dispatch, result handling, truncation, and
collaboration board sync.

Extracted from runner.py. Contains:
- Tool call execution with plugin hooks
- Result truncation logic
- Collaboration board auto-sync
- Response parsing helpers (_remove_tags, _filter_native_tokens, etc.)
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Callable

from opensquad.tool import logger

__all__ = ["ToolExecutor"]


class ToolExecutor:
    """
    Handles tool call dispatch, result processing, and truncation.

    Extracted from runner.py to isolate tool execution complexity from the main
    turn loop. All methods are designed to be called from within the Runner's
    async context.
    """

    # ------------------------------------------------------------------
    # Tag parsing helpers (shared with response processing)
    # ------------------------------------------------------------------

    @staticmethod
    def filter_native_tokens(text: str) -> str:
        """Filter leaked native tool call text from various models.

        Handles:
        1. <|...|> format (Qwen3/DeepSeek, etc.)
        2. functions.<name>:<id>{...} format (Kimi/Moonshot, etc.)
        """
        if not text:
            return text

        # Format 1: <|...|> tokens
        if "<|" in text:
            text = re.sub(
                r"<\|tool_calls_section_begin\|>.*?<\|tool_calls_section_end\|>",
                "",
                text,
                flags=re.DOTALL,
            )
            text = re.sub(r"<\|[^|>]*\|>", "", text)

        # Format 2: functions.<name>:<id>{...} tokens
        if "functions." in text:
            text = re.sub(
                r"\bfunctions\.[a-zA-Z0-9_]+:\d+\{(?:[^{}]|\{[^{}]*\})*\}",
                "",
                text,
                flags=re.DOTALL,
            )

        return text

    @staticmethod
    def remove_tags(text: str, tags: list[str]) -> str:
        """Remove specified XML tags from text (supports attributes and self-closing)."""
        if not text:
            return ""
        result = text
        for tag in tags:
            pattern = rf"<{tag}\b[^>]*>.*?</{tag}>"
            result = re.sub(pattern, "", result, flags=re.DOTALL | re.IGNORECASE)
            pattern = rf"<{tag}\b[^>]*/>"
            result = re.sub(pattern, "", result, flags=re.IGNORECASE)
            # Truncate from unclosed tag to end
            pattern = rf"<{tag}\b[^>]*>.*"
            result = re.sub(pattern, "", result, flags=re.DOTALL | re.IGNORECASE)
        return result.strip()

    @staticmethod
    def remove_all_tags(text: str) -> str:
        """Thoroughly remove all XML/HTML tags, preserving Markdown and plain text."""
        if not text:
            return ""

        result = text

        # Filter native tool call tokens
        result = ToolExecutor.filter_native_tokens(result)

        # Remove possibly orphaned tool_call open tags
        result = re.sub(r'tool_call\s+name="[^"]+"\s*>', "", result, flags=re.IGNORECASE)

        # Silent blocks: remove entirely
        silent_blocks = [
            "thought", "plan", "think", "tool_call", "tool_result",
            "to_system", "state", "wake", "sleep", "title", "option",
            "arguments",
        ]
        for tag in silent_blocks:
            result = re.sub(
                rf"<{tag}\b[^>]*>.*?</{tag}>", "", result,
                flags=re.DOTALL | re.IGNORECASE
            )
            result = re.sub(rf"<{tag}\b[^>]*/>", "", result, flags=re.IGNORECASE)

        # Extract to_user content (keep it)
        result = re.sub(
            r"<to_user\b[^>]*>(.*?)</to_user>",
            r"\1",
            result,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Remove remaining tag names
        result = re.sub(r"<[^>]+>", "", result)

        # Clean orphaned closing tags and stray brackets
        result = re.sub(r"</[a-zA-Z0-9_]+>", "", result)
        result = re.sub(r"^\s*[<>]\s*$", "", result, flags=re.MULTILINE)

        # Collapse extra blank lines
        result = re.sub(r"\n{4,}", "\n\n\n", result)

        return result.strip()

    @staticmethod
    def extract_tag(response: str, tag: str) -> str | None:
        """Robustly extract XML tag content, supporting attributes and extra whitespace."""
        pattern = rf"<{tag}\b[^>]*>(.*?)</{tag}>"
        match = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
        if match:
            val = match.group(1).strip()
            logger.info("[ToolExecutor] Found tag <%s>: %s", tag, val)
            return val

        # Fallback: unclosed tag
        pattern_fallback = rf"<{tag}\b[^>]*>(.*)"
        match_fb = re.search(pattern_fallback, response, re.IGNORECASE | re.DOTALL)
        if match_fb:
            val = match_fb.group(1).split("<")[0].strip()
            logger.info("[ToolExecutor] Found unclosed tag <%s>: %s", tag, val)
            return val

        # Lazy pattern for state/wake/sleep
        if tag in ("state", "wake", "sleep"):
            pattern_lazy = rf"{tag}\s*>\s*(.*?)\s*</{tag}>"
            match_lazy = re.search(pattern_lazy, response, re.IGNORECASE)
            if match_lazy:
                val = match_lazy.group(1).strip()
                logger.info("[ToolExecutor] Found lazy tag %s: %s", tag, val)
                return val

        return None

    @staticmethod
    def extract_text_before_tool(text: str) -> str | None:
        """Extract text content before the first tool_call marker."""
        if not text:
            return None

        tool_match = re.search(r"<tool_call", text, re.IGNORECASE)
        if not tool_match:
            return None

        text_before = text[: tool_match.start()]
        text_before = re.sub(r"<(?!tool_call)[^>]+>", "", text_before)
        text_before = text_before.strip()

        if text_before and len(text_before) > 3:
            return text_before
        return None

    @staticmethod
    def is_leaked_tool_params(text: str) -> bool:
        """Detect leaked tool parameters (JSON or XML parameter tags)."""
        s = text.strip()
        if not s:
            return False

        # JSON leak: starts with { and ends with }
        if s.startswith("{") and re.search(r"\}\s*$", s):
            if s == "{}":
                return True
            if re.match(r'^\{\s*"[a-zA-Z_][a-zA-Z0-9_]*"\s*:', s):
                logger.warning(
                    "[ToolExecutor] Detected leaked JSON parameters "
                    "without <tool_call> wrapper"
                )
                return True

        # XML leak detection
        system_tags = {
            "title", "thought", "think", "plan", "to_user", "to_user_reply",
            "to_system", "tool_call", "tool_result", "arguments", "state",
            "wake", "sleep", "option", "forward", "system_reminder", "func",
            "task_start", "task_complete", "task_failed",
        }

        xml_tags = re.findall(
            r"<([a-zA-Z_][a-zA-Z0-9_]*)>.*?</\1>", s, re.DOTALL | re.IGNORECASE
        )

        if xml_tags and "<tool_call" not in text:
            leaked_tags = [
                tag for tag in xml_tags if tag.lower() not in system_tags
            ]
            if leaked_tags:
                logger.warning(
                    "[ToolExecutor] Detected leaked XML parameter tags: %s",
                    leaked_tags,
                )
                return True

        return False

    # ------------------------------------------------------------------
    # Repetition detection
    # ------------------------------------------------------------------

    @staticmethod
    def is_repeated_content(text: str) -> bool:
        """Detect repetitive / stuttering output from the model."""
        if not text or len(text.strip()) < 20:
            return False

        clean = text.strip()
        words = clean.split()
        if len(words) < 5:
            return False

        # Pattern 1: exact repeat of the entire string
        first_half = clean[: len(clean) // 2]
        second_half = clean[len(clean) // 2 :]
        if first_half == second_half:
            logger.warning("[ToolExecutor] Detected exact half-repeat pattern")
            return True

        # Pattern 2: single word or short phrase repeated many times
        word_counts: dict[str, int] = {}
        for w in words:
            word_counts[w] = word_counts.get(w, 0) + 1
        if words:
            max_count = max(word_counts.values())
            if max_count / len(words) > 0.6:
                logger.warning(
                    "[ToolExecutor] Detected word-frequency repetition (max=%.1f%%)",
                    (max_count / len(words)) * 100,
                )
                return True

        # Pattern 3: cross-turn repetition
        return False

    # ------------------------------------------------------------------
    # Result truncation
    # ------------------------------------------------------------------

    @staticmethod
    def truncate_result_text(text: str, max_len: int | None) -> str:
        """Truncate text to max_len chars, preserving head and tail portions."""
        if max_len is None or max_len <= 0 or len(text) <= max_len:
            return text
        if max_len >= 50000:
            return text[:25000] + "...[truncated]..." + text[-10000:]
        return text[:1000] + "...[truncated]..." + text[-500:]

    def summarize_result(
        self,
        name: str,
        result: Any,
        get_max_chars: "Callable[[], int | None] | None" = None,
    ) -> str:
        """
        Summarize a tool result for inclusion in the LLM context.

        Args:
            name:          Tool name.
            result:        Raw tool result (any type).
            get_max_chars: Callable returning max chars, or None for no limit.
        """
        now = datetime.now().strftime("%H:%M:%S")

        # MCP multimodal result (contains screenshots)
        if isinstance(result, dict) and result.get("__mcp_multimodal__"):
            text = result.get("text", "")
            img_count = len(result.get("images", []))
            res_str = f"{text} [+{img_count} screenshot(s) attached]"
        else:
            res_str = str(result)

        tool_name_lower = (name or "").lower()
        is_skill_read = "read_skill" in tool_name_lower
        is_skill_related = is_skill_read or "skill" in tool_name_lower

        if is_skill_read:
            max_len: int | None = None
        elif is_skill_related:
            max_len = 50000
        elif get_max_chars is not None:
            max_len = get_max_chars()
            if max_len is not None and max_len <= 0:
                max_len = None
        else:
            max_len = 50000

        res_str = self.truncate_result_text(res_str, max_len)
        return f"[{now}] Tool '{name}' executed. Result: {res_str}"

    # ------------------------------------------------------------------
    # Collaboration board sync (extracted from tool execution loop)
    # ------------------------------------------------------------------

    @staticmethod
    def sync_collab_board(
        agent_dir: str,
        agent_id: str,
        tool_name: str,
        result: Any,
    ) -> None:
        """
        Update the collaboration board with the latest tool execution result.

        Silently skips on any error -- collaboration board is best-effort.
        """
        try:
            from opensquad.collab_board import (
                list_tasks,
                update_latest_tool,
            )

            tasks = list_tasks()
            active_task_id = ""
            for t in tasks:
                if t.get("status") == "active":
                    active_task_id = str(t.get("task_id") or "")
                    break

            if not active_task_id:
                return

            sensitive_tools = {
                "read_related_files", "glob", "grep", "rg",
                "filesystem__read", "filesystem__write", "filesystem__edit",
                "bash", "subprocess", "delegate_task",
                "system__send_file_to_web", "execute_command",
                "view_source_code", "find_files",
            }
            if tool_name.startswith("collaboration.") or tool_name.startswith(
                "agent_setup."
            ):
                sensitive_tools.add(tool_name)

            should_sync = tool_name not in sensitive_tools
            if should_sync:
                update_latest_tool(
                    collab_id=active_task_id,
                    task_name="",
                    agent_id=agent_id,
                    tool_name=tool_name,
                    tool_result=result,
                )
        except Exception:
            pass
