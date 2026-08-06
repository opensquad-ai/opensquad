"""
Output handler module -- stream parsing setup, event emission, and token stats.

Extracted from runner.py to reduce its size.  Handles:
- stream_parser handler registration (_setup_event_dispatch)
- Token stat computation and broadcast (_broadcast_token_stats)
- Streamed text accumulation (to_user_stream / to_user_final lifecycle)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from opensquad.tool import logger

__all__ = ["OutputHandler"]

# PERF-4: markers that identify tool-result payloads when they are emitted as
# plain text (i.e. without a proper <tool_result> tag).  These must never
# surface in the chat pane.
_TOOL_RESULT_MARKERS = (
    '"status"',
    "'status'",
    "read_range",
    "total_lines",
    "executed. Result:",
)


def _looks_like_tool_result(text: str) -> bool:
    """Heuristic: does ``text`` look like a leaked tool result payload?"""
    if len(text) > 4000:
        return False
    lower = text.lower()
    hits = sum(1 for m in _TOOL_RESULT_MARKERS if m.lower() in lower)
    # A single weak marker is not enough (e.g. user text may mention "status");
    # require the JSON-object shape OR two markers.
    if hits >= 2:
        return True
    if hits == 1:
        stripped = text.strip()
        return stripped.startswith(("{", "[{")) and any(
            k in stripped for k in ('"status"', "'status'", "read_range", "total_lines")
        )
    return False


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
            # PERF-4: guard against tool-result JSON leaking into the chat pane.
            # When the LLM emits raw tool output (instead of a proper
            # <tool_result> tag), the default handler would otherwise surface it
            # as if it were a user-facing message.
            stripped = text.strip()
            if _looks_like_tool_result(stripped):
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

        def emit_to_user_end_task(text: str) -> None:
            nonlocal streamed_user_tag
            streamed_user_tag = "to_user_end_task"
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
                "to_user_end_task": emit_to_user_end_task,
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
                # PERF-4: tool_result is a machine-readable tag; never surface it
                # to the chat pane.  It was previously missing from _handlers, so
                # raw tool JSON leaked via the default handler.
                "tool_result": lambda x: None,
                "result": lambda x: None,
                "tool_response": lambda x: None,
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
            from opensquad.token_breakdown import compute_token_breakdown

            chat_api = runner.chat_api
            tools = None
            if hasattr(runner, "_tools_for_token_stats"):
                tools = runner._tools_for_token_stats()
            else:
                tools = getattr(chat_api, "_last_tools", None) or getattr(runner, "_current_tools", None)
            total = chat_api._count_tokens(chat_api.req, tools)
            encoding = getattr(chat_api, "encoding", None)
            stats = compute_token_breakdown(
                chat_api.req,
                tools,
                encoding=encoding,
                total=total,
            )

            # Cumulative totals (history + current session)
            hist_in = runner._hist_input_tokens
            hist_out = runner._hist_output_tokens
            hist_req = runner._hist_requests
            hist_cache = runner._hist_cache_read_tokens

            cumul_input = hist_in + getattr(chat_api, "total_input_tokens", 0)
            cumul_output = hist_out + getattr(chat_api, "total_output_tokens", 0)
            cumul_total = cumul_input + cumul_output
            cumul_requests = hist_req + getattr(chat_api, "total_requests", 0)
            cumul_cache = hist_cache + getattr(chat_api, "total_cache_read_tokens", 0)

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
                    "cache_read_tokens": getattr(chat_api, "total_cache_read_tokens", 0),
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
