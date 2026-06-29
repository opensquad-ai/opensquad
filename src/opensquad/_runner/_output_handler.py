# -*- coding: utf-8 -*-
"""
Output handler module -- stream parsing setup, event emission, and token stats.

Extracted from runner.py to reduce its size.  Handles:
- stream_parser handler registration (_setup_event_dispatch)
- Token stat computation and broadcast (_broadcast_token_stats)
- Streamed text accumulation (to_user_stream / to_user_final lifecycle)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Callable

from opensquad.tool import logger

__all__ = ["OutputHandler"]


class OutputHandler:
    """
    Manages output-related concerns: stream parsing, event emission, and token stats.

    This class is instantiated by ``AgentRunner`` and delegates actual work to
    runner methods via callbacks, keeping the Runner itself as the source of truth.
    """

    def setup_stream_handlers(
        self,
        stream_parser: Any,
        turn_sid: str,
        emit: Callable[[str, Any], Any],
        filter_native_tokens: Callable[[str], str],
    ) -> list[str]:
        """
        Wire up the stream_parser with handlers for each tag type.

        This method is idempotent -- safe to call multiple times per turn.

        Args:
            stream_parser:  The stream parser from the ChatAPI.
            turn_sid:      Current session ID for event routing.
            emit:          ``runner._emit``.
            filter_native_tokens: ``runner._filter_native_tokens``.

        Returns:
            The ``_streamed_user_text`` accumulator list.
        """
        sid = turn_sid

        def emit_with_sid(etype: str, data: Any) -> None:
            """Inject session_id into event data before emitting."""
            bus_emit = emit  # alias for clarity
            from opensquad.events import bus

            # emit is already runner._emit which wraps bus.emit_async with sid injection.
            # But emit_with_sid is called from sync context, so use bus.emit directly.
            bus.emit(etype, {"sid": sid, "data": data})

        streamed_user_text: list[str] = []
        streamed_user_tag: str | None = None

        def emit_user_stream(text: str) -> None:
            """Emit to_user_stream and accumulate for later persistence."""
            text = filter_native_tokens(text)
            if not text:
                return
            streamed_user_text.append(text)
            emit_with_sid("to_user_stream", text)

        def emit_to_user(text: str) -> None:
            nonlocal streamed_user_tag
            streamed_user_tag = "to_user"
            emit_user_stream(text)

        def emit_to_user_reply(text: str) -> None:
            nonlocal streamed_user_tag
            streamed_user_tag = "to_user_reply"
            emit_user_stream(text)

        if not stream_parser:
            return streamed_user_text

        stream_parser._default_handler = emit_user_stream

        stream_parser._handlers.update(
            {
                "thought": lambda x: emit_with_sid("thought", x),
                "think": lambda x: emit_with_sid("thought", x),
                "to_user": emit_to_user,
                "to_user_reply": emit_to_user_reply,
                # Intercept these tags to prevent them from appearing as plain text
                "title": lambda x: None,
                "plan": lambda x: None,
                "tool_call": lambda x: None,
                "arguments": lambda x: None,
                "func": lambda x: None,
                "state": lambda x: None,
                "wake": lambda x: None,
                "sleep": lambda x: None,
                "to_system": lambda x: None,
                "option": lambda x: None,
            }
        )

        return streamed_user_text

    # ------------------------------------------------------------------
    # Token stats
    # ------------------------------------------------------------------

    async def broadcast_token_stats(
        self,
        runner: Any,
        emit: Callable[[str, Any], Any],
    ) -> None:
        """
        Compute and broadcast token usage stats via WebSocket event and file.

        Args:
            runner: AgentRunner instance.
            emit:    runner._emit.
        """
        try:
            chat_api = runner.chat_api
            tools = getattr(chat_api, "_last_tools", None)
            total = chat_api._count_tokens(chat_api.req, tools)
            stats: dict[str, int] = {
                "system": 0,
                "user": 0,
                "thought": 0,
                "tool": 0,
                "response": 0,
            }

            encoding = getattr(chat_api, "encoding", None)

            def _count_str(text: str) -> int:
                if text is None:
                    return 0
                if encoding:
                    try:
                        return len(encoding.encode(text))
                    except Exception:
                        pass
                return len(text) // 4

            # ---- Tool definitions (schemas) count as tool overhead ----
            if tools:
                for tool in tools:
                    fn = tool.get("function", {}) if isinstance(tool, dict) else getattr(tool, "function", {})
                    stats["tool"] += 6
                    if fn.get("name"):
                        stats["tool"] += _count_str(fn["name"])
                    if fn.get("description"):
                        stats["tool"] += _count_str(fn["description"])
                    if fn.get("parameters"):
                        stats["tool"] += _count_str(json.dumps(fn["parameters"], ensure_ascii=False))

            _THOUGHT_RE = re.compile(r'<(thought|think)>(.*?)</\1>', re.DOTALL | re.IGNORECASE)
            _TOOL_CALL_RE = re.compile(r'<tool_call[^>]*>(.*?)</tool_call>', re.DOTALL | re.IGNORECASE)
            _TOOL_RESULT_RE = re.compile(r'<tool_result[^>]*>(.*?)</tool_result>', re.DOTALL | re.IGNORECASE)

            def _count_content_list(items: list, target: str) -> None:
                has_tool_result = any(isinstance(item, dict) and item.get("type") == "tool_result" for item in items)
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    t = item.get("type")
                    if t == "text":
                        text = item.get("text", "")
                        if has_tool_result:
                            stats["tool"] += _count_str(text)
                        elif target == "user":
                            stats["user"] += _count_str(text)
                        elif target == "assistant":
                            thought_sum = sum(_count_str(m.group(2)) for m in _THOUGHT_RE.finditer(text))
                            if thought_sum:
                                stats["thought"] += thought_sum
                                text = _THOUGHT_RE.sub('', text).strip()
                            stats["response"] += _count_str(text)
                    elif t == "tool_result":
                        for c in (item.get("content") or []):
                            if isinstance(c, dict) and c.get("type") == "text":
                                stats["tool"] += _count_str(c["text"])
                    elif t == "tool_use":
                        inp = item.get("input", {})
                        stats["tool"] += _count_str(item.get("name", "") + str(inp))

            for msg in chat_api.req:
                role = msg.get("role", "")
                content = msg.get("content", "")

                if role == "tool":
                    if isinstance(content, str):
                        stats["tool"] += _count_str(content)
                    elif isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                stats["tool"] += _count_str(item["text"])
                    continue

                if role == "assistant":
                    reasoning = msg.get("reasoning_content")
                    if reasoning:
                        stats["thought"] += _count_str(reasoning)

                    if msg.get("tool_calls"):
                        for tc in msg["tool_calls"]:
                            fn = tc.get("function", {}) if isinstance(tc, dict) else getattr(tc, "function", {})
                            stats["tool"] += _count_str(fn.get("name", "") + fn.get("arguments", ""))

                if isinstance(content, list):
                    target = "user" if role == "user" else "assistant" if role == "assistant" else role
                    _count_content_list(content, target)
                elif isinstance(content, str):
                    if role == "system":
                        stats["system"] += _count_str(content)
                    elif role == "user":
                        if "<tool_result>" in content or "<tool_result " in content:
                            tool_sum = sum(_count_str(m.group(1)) for m in _TOOL_RESULT_RE.finditer(content))
                            stats["tool"] += tool_sum
                            stats["user"] += _count_str(_TOOL_RESULT_RE.sub('', content).strip())
                        elif re.match(r'^\[\d{2}:\d{2}:\d{2}\] Tool \'', content):
                            stats["tool"] += _count_str(content)
                        else:
                            stats["user"] += _count_str(content)
                    elif role == "assistant":
                        thought_sum = sum(_count_str(m.group(2)) for m in _THOUGHT_RE.finditer(content))
                        stats["thought"] += thought_sum
                        text_no_thought = _THOUGHT_RE.sub('', content).strip()

                        tool_sum = sum(_count_str(m.group(1)) for m in _TOOL_CALL_RE.finditer(text_no_thought))
                        stats["tool"] += tool_sum
                        text_response = _TOOL_CALL_RE.sub('', text_no_thought).strip()

                        stats["response"] += _count_str(text_response)

            breakdown_total = sum(stats.values())
            stats["overhead"] = max(0, total - breakdown_total)

            # Cumulative totals (history + current session)
            hist_in = runner._hist_input_tokens
            hist_out = runner._hist_output_tokens
            hist_req = runner._hist_requests
            hist_cache = runner._hist_cache_read_tokens

            cumul_input = hist_in + getattr(chat_api, "total_input_tokens", 0)
            cumul_output = hist_out + getattr(chat_api, "total_output_tokens", 0)
            cumul_total = cumul_input + cumul_output
            cumul_requests = hist_req + getattr(chat_api, "total_requests", 0)
            cumul_cache = hist_cache + getattr(
                chat_api, "total_cache_read_tokens", 0
            )

            token_data = {
                "used": total,
                "max": chat_api.token_max,
                "model": getattr(chat_api, "model", ""),
                "hist_input": hist_in,
                "hist_output": hist_out,
                "hist_requests": hist_req,
                "cumulative": {
                    "total_input_tokens": cumul_input,
                    "total_output_tokens": cumul_output,
                    "total_tokens": cumul_total,
                    "total_requests": cumul_requests,
                    "cache_read_tokens": cumul_cache,
                },
                "session": {
                    "input_tokens": getattr(chat_api, "total_input_tokens", 0),
                    "output_tokens": getattr(chat_api, "total_output_tokens", 0),
                    "requests": getattr(chat_api, "total_requests", 0),
                    "cache_read_tokens": getattr(
                        chat_api, "total_cache_read_tokens", 0
                    ),
                },
                "breakdown": stats,
            }

            await emit("token_stats", token_data)

            # Persist to file
            data_dir = getattr(chat_api, "history_dir", None)
            if data_dir:
                stats_file = f"{data_dir}/token_stats.json"
                try:
                    with open(stats_file, "w", encoding="utf-8") as f:
                        json.dump(token_data, f, ensure_ascii=False)
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"[OutputHandler] _broadcast_token_stats: {e}")
