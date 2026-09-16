"""
Manual context compression -- ONE session-scoped implementation.

Extracted from the inline ``__COMPRESS_CONTEXT__`` block in ``runner.py`` so the
parallel session dispatcher can reuse it instead of dropping the command.

Why this module exists
----------------------
The Agent Web UI runs ``run_parallel_dispatcher`` (multi-pane). Its
``_handle_agent_level_command`` used to answer ``__COMPRESS_CONTEXT__`` with

    "[Runner] __COMPRESS_CONTEXT__ ignored on parallel dispatcher"

so clicking "compress context" produced **no summary, no error and no terminal
event**: the optimistic "Generating context summary..." block stayed pending
forever (the frontend only clears it on ``summary_stream done`` /
``compression_progress is_final``). The serial loop owned the only working copy.

Design constraints
------------------
- **Session-scoped, never touches global focus.** Everything runs against an
  explicit ``sid``. The parallel dispatcher keeps several panes' state alive in
  ``SessionManager._live_sessions``; ``load_history_session()`` (which *changes*
  focus) is deliberately never called here.
- **Always terminates.** Every exit path -- success, empty summary, exception --
  emits ``summary_stream {done: True}`` plus a final ``compression_progress``.
  A silent failure is the bug being fixed.
- **History rewrite is the only durable side effect**, plus the summary
  message/event so a refresh can still render it.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

from opensquad.protocol_version import (
    FIELD_IS_FINAL,
    FIELD_TEXT,
    FIELD_TRACE_ID,
)
from opensquad.tool import logger

from ._compression import (
    build_summary_payload,
    extract_file_operations,
    run_external_summarizer,
)

__all__ = ["compress_session_context"]


def _resolve_target_api(runner: Any, sid: str) -> Any:
    """ChatAPI (or AgentDefaults) that knows base_url / api_key / model for *sid*.

    Parallel panes each own a ChatAPI in ``_session_chat_apis[sid]``; the serial
    loop just uses the runner-level one. Falling back keeps a missing entry from
    aborting compression.
    """
    api = getattr(runner, "chat_api", None)
    if sid:
        try:
            from opensquad.session_model import session_api_map

            api = session_api_map(runner).get(sid) or api
        except Exception:
            logger.debug("[ManualCompress] session_api_map lookup failed", exc_info=True)
    return api


async def compress_session_context(
    runner: Any,
    *,
    sid: str | None = None,
    emit: Callable[..., Any],
) -> dict[str, Any]:
    """Compress ONE session's context and stream the summary to every client.

    ``sid=None``/"" falls back to the focused session (serial-loop behaviour).
    ``emit`` is ``runner._emit`` (``await emit(type, data, sid=...)``).

    Returns the ``SessionManager.compress_current_session`` result, or
    ``{"compressed": False, "error": ...}`` when the summarizer failed. It never
    raises: the parallel dispatcher must not die on a user gesture.
    """
    # Prefer the manager this runner was constructed with (an embedded caller can
    # inject one), otherwise the agent-wide singleton the rest of the process uses.
    from opensquad.session_manager import get_session_manager

    sm = getattr(runner, "_injected_session_manager", None) or get_session_manager()
    target_sid = str(sid or "").strip()
    if not target_sid:
        target_sid = ((sm.get_focused_session_id() or "") or (sm.get_current_session_id() or "")).strip()

    started_ms = int(datetime.now().timestamp() * 1000)
    round_id = int(getattr(runner, "_current_round", 0) or 0)
    trace_id = f"cmp_{started_ms}_{round_id}"
    stream_id = f"compress_{trace_id}"
    round_id_for_event = round_id

    async def _progress(text: str, *, final: bool = False) -> None:
        # Field names come from the shared WS contract: this payload is read by
        # the browser as `content.is_final` (snake_case) and a rename here is
        # invisible to every Python-side check.
        await emit(
            "compression_progress",
            {FIELD_TEXT: text, FIELD_IS_FINAL: final, FIELD_TRACE_ID: trace_id},
            sid=target_sid,
        )

    async def _finish_stream(text: str = "", *, error: str = "") -> None:
        payload: dict[str, Any] = {
            "id": stream_id,
            "done": True,
            "trace_id": trace_id,
        }
        if text:
            payload["text"] = text
        if error:
            payload["error"] = error
        await emit("summary_stream", payload, sid=target_sid)

    logger.info(
        "[ManualCompress] ENTER sid=%s (focused=%s)",
        target_sid or "-",
        sm.get_current_session_id() or "-",
    )

    chunks: list[str] = []

    try:
        # A concurrent turn on the same pane keeps appending to the same live
        # session dict; rewriting ``messages`` underneath it would drop whatever
        # it appended between our read and our mutate. Refuse instead of losing
        # data — and still answer the UI so the spinner clears.
        sched = getattr(runner, "_parallel_scheduler", None)
        if sched is not None and target_sid:
            try:
                if sched.is_session_busy(target_sid):
                    logger.info(
                        "[ManualCompress] sid=%s is busy — refusing to compress mid-turn",
                        target_sid,
                    )
                    await _finish_stream()
                    await emit(
                        "info",
                        {
                            "event": "context_compress_skipped",
                            "text": "该会话正在执行任务，请等本轮结束后再压缩上下文。",
                            "trace_id": trace_id,
                        },
                        sid=target_sid,
                    )
                    await _progress("Session is busy — compression skipped", final=True)
                    return {"compressed": False, "sid": target_sid, "reason": "busy"}
            except Exception:
                logger.debug("[ManualCompress] busy check failed", exc_info=True)

        await _progress("Collecting conversation history…")

        target_data = sm.ensure_session_loaded(target_sid) if target_sid else None
        prev_summary = ""
        if isinstance(target_data, dict):
            prev_summary = str(target_data.get("latest_summary") or "")
        if not prev_summary:
            prev_summary = str(getattr(getattr(runner, "chat_api", None), "_latest_summary", "") or "")

        msgs_for_summary = sm.get_messages(limit=500, sid=target_sid or None)
        all_events_for_summary = sm.get_events(limit=2000, sid=target_sid or None)

        payload_events = all_events_for_summary
        if not msgs_for_summary and not all_events_for_summary:
            logger.info("[ManualCompress] sid=%s has no history — nothing to compress", target_sid or "-")
            await _finish_stream()
            await _progress("Context is already empty", final=True)
            return {"compressed": False, "sid": target_sid, "reason": "empty"}

        # File-op tracking: tell the summarizer which files were read / modified.
        # The session changeset (when present) is authoritative for modified files.
        file_ops: dict[str, list[str]] | None = None
        try:
            from opensquad.utils.session_changeset import summary as _changeset_summary

            root = getattr(runner, "_workspace_dir", None) or getattr(sm, "save_dir", None)
            if root and os.path.isdir(root):
                cs = _changeset_summary(root)
                file_ops = extract_file_operations(
                    msgs_for_summary,
                    payload_events,
                    changeset_files=cs.get("files"),
                )
        except Exception:
            file_ops = None
        if file_ops is None:
            file_ops = extract_file_operations(msgs_for_summary, payload_events)

        summary_payload = build_summary_payload(
            prev_summary,
            msgs_for_summary,
            payload_events,
            keep_last=None,  # token-based; no message-count threshold
            file_ops=file_ops,
        )

        async def _on_summary_chunk(delta: str) -> None:
            chunks.append(delta)
            await emit(
                "summary_stream",
                {"id": stream_id, "delta": delta, "trace_id": trace_id},
                sid=target_sid,
            )

        api = _resolve_target_api(runner, target_sid)
        await _progress("Summarizing…")
        summary_text = await run_external_summarizer(
            summary_payload,
            base_url=getattr(api, "base_url", "") or "",
            api_key=getattr(api, "api_key", "") or "",
            model=getattr(api, "model", "") or "",
            on_chunk=_on_summary_chunk,
        )
        if not summary_text and chunks:
            summary_text = "".join(chunks).strip()

        # Terminal frame BEFORE the (sync, potentially slow) disk rewrite so the
        # UI stops spinning even if persistence fails afterwards.
        await _finish_stream()

        result = sm.compress_current_session(
            previous_summary=prev_summary,
            external_summary=summary_text,
            sid=target_sid or None,
        )

        if not summary_text:
            logger.warning("[ManualCompress] summarizer returned empty text sid=%s", target_sid or "-")

        # Keep the in-memory ChatAPI of that pane consistent so the NEXT turn
        # starts from the trimmed history + new summary.
        try:
            live_api = _resolve_target_api(runner, target_sid)
            if live_api is not None and result.get("compressed"):
                live_api._latest_summary = result.get("summary_content", "")
        except Exception:
            logger.debug("[ManualCompress] live api summary sync skipped", exc_info=True)

        if summary_text:
            summary_evt = {
                "event": "context_summary_generated",
                "text": "Context summary generated",
                "summary": summary_text,
                "trace_id": trace_id,
            }
            # Persist so refresh / history replay can still show the summary.
            sm.add_event("info", summary_evt, turn_id=0, round_id=round_id_for_event, sid=target_sid)
            sm.add_message("system", summary_text, msg_type="context_summary", sid=target_sid)
            await emit("info", summary_evt, sid=target_sid)
            try:
                from plugins.self_learn.archive import archive_compression_summary

                title = ""
                if isinstance(target_data, dict):
                    title = str(target_data.get("title") or "")
                archive_compression_summary(
                    summary_text,
                    session_id=target_sid or "",
                    session_title=title,
                    source="manual_compress",
                    agent_dir=getattr(runner, "_agent_dir", None) or None,
                    agent_id=getattr(runner, "_agent_id", "") or "",
                )
            except Exception:
                logger.debug("[ManualCompress] self_learn archive skipped", exc_info=True)

        # Rewrite the pane's live history so the UI drops the archived tail.
        refreshed = sm.get_messages(sid=target_sid or None)
        refreshed_events = sm.get_events(sid=target_sid or None)
        await emit(
            "history_sync",
            {
                "messages": refreshed,
                "events": refreshed_events,
                "session_id": target_sid,
                "is_working_session": True,
                "reason": "compression",
            },
            sid=target_sid,
        )
        try:
            await runner._broadcast_token_stats(target_sid or None)
        except Exception:
            logger.debug("[ManualCompress] token stats broadcast skipped", exc_info=True)

        kept = int(result.get("kept_messages") or 0) + int(result.get("kept_events") or 0)
        await _progress(f"Compressed — {kept} item(s) kept", final=True)
        logger.info(
            "[ManualCompress] DONE sid=%s compressed_msgs=%s compressed_events=%s kept=%s",
            target_sid or "-",
            result.get("compressed_messages"),
            result.get("compressed_events"),
            kept,
        )
        return result

    except Exception as exc:  # noqa: BLE001 — a user gesture must never kill the dispatcher
        logger.warning(
            "[ManualCompress] FAILED sid=%s: %s",
            target_sid or "-",
            exc,
            exc_info=True,
        )
        # Guarantee the spinner clears, even if the failure was before the stream.
        try:
            await _finish_stream(error=str(exc))
        except Exception:
            logger.debug("[ManualCompress] terminal summary_stream emit failed", exc_info=True)
        try:
            await emit(
                "info",
                {
                    "event": "context_compress_skipped",
                    "text": f"Context compression failed: {exc}",
                    "trace_id": trace_id,
                },
                sid=target_sid,
            )
            await _progress(f"Compression failed: {exc}", final=True)
        except Exception:
            logger.debug("[ManualCompress] failure notice emit failed", exc_info=True)
        return {"compressed": False, "sid": target_sid, "error": str(exc)}
