from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger_name = "opensquad.run_command_dispatcher"
logger = logging.getLogger(logger_name)

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_session_id(sid: str) -> bool:
    """Session ID must match a safe character whitelist to prevent path traversal."""
    if not sid or len(sid) > 128:
        return False
    return bool(_SESSION_ID_RE.match(sid))


@dataclass
class CommandDispatchResult:
    handled: bool
    next_query: str | None
    should_continue: bool = False


class RunCommandDispatcher:
    """Extracts system command dispatch from `AgentRunner.run()`."""

    def __init__(self, runner: Any):
        self.runner = runner

    async def dispatch(self, initial_query: str | None) -> CommandDispatchResult:
        if initial_query == "__STOP__":
            self.runner._input_hub.clear_stop_request()
            return CommandDispatchResult(handled=True, next_query=None, should_continue=True)

        if initial_query == "__REQUEST_TOKEN_STATS__" or (
            isinstance(initial_query, str) and initial_query.startswith("__REQUEST_TOKEN_STATS__:")
        ):
            forced_sid = ""
            if isinstance(initial_query, str) and initial_query.startswith("__REQUEST_TOKEN_STATS__:"):
                forced_sid = initial_query.split(":", 1)[1].strip()
            try:
                if not (forced_sid and forced_sid != "unknown"):
                    from opensquad.session_manager import get_session_manager

                    sm = get_session_manager()
                    forced_sid = (sm.get_focused_session_id() or sm.get_current_session_id() or "").strip()
            except Exception:
                pass
            await self.runner._broadcast_token_stats(forced_sid or None)
            return CommandDispatchResult(handled=True, next_query=None, should_continue=True)

        if initial_query == "__RESUME_WORKFLOW__":
            self.runner._current_input_source = "wake"
            return CommandDispatchResult(
                handled=False, next_query="Continue the previous task from where you left off."
            )

        if initial_query == "__NEW_SESSION__":
            await self._handle_new_session()
            return CommandDispatchResult(handled=True, next_query=None, should_continue=True)

        if initial_query and initial_query.startswith("__WITHDRAW_TURN__:"):
            payload = initial_query.split(":", 1)[1].strip()
            ts, mid = payload, ""
            if "|" in payload:
                ts, mid = payload.split("|", 1)
                ts, mid = ts.strip(), mid.strip()
            await self._handle_withdraw_turn(ts, message_id=mid or None)
            return CommandDispatchResult(handled=True, next_query=None, should_continue=True)

        if initial_query and initial_query.startswith("__LOAD_SESSION__:"):
            sid = initial_query.split(":", 1)[1]
            if not _validate_session_id(sid):
                logger.warning(f"[Security] Blocked path-traversal attempt in session_id: {sid!r}")
                return CommandDispatchResult(handled=True, next_query=None, should_continue=True)
            await self._handle_load_session(sid)
            return CommandDispatchResult(handled=True, next_query=None, should_continue=True)

        return CommandDispatchResult(handled=False, next_query=initial_query)

    async def _handle_new_session(self) -> None:
        self.runner._reset_session_stats()
        drained = self.runner._input_hub.get_all_pending()
        if drained:
            self.runner._pending_buffer.extend(
                [
                    {
                        "content": item["content"],
                        "source": item.get("source", "web"),
                        "images": item.get("images"),
                        "attachments": item.get("attachments"),
                        "channel": item.get("channel", ""),
                    }
                    for item in drained
                ]
            )
        self.runner._session_manager.start_new_session()
        # Drop short-lived session file-change checkpoints (Cursor-style safety net)
        try:
            from opensquad.utils.path_utils import get_workspace_root
            from opensquad.utils.session_changeset import clear_for_new_session

            clear_for_new_session(get_workspace_root())
        except Exception:
            logger.debug("[RunCommandDispatcher] session changeset clear skipped", exc_info=True)
        new_sid = self.runner._session_manager.get_current_session_id()
        logger.warning(
            "[RunCommandDispatcher] New session started: sid=%s file=%s",
            new_sid,
            getattr(self.runner._session_manager, "current_session_file", "?"),
        )
        self.runner._turn_sid = new_sid
        self.runner._load_history()
        await self.runner._emit("turn_start", 0)
        await self.runner._bus.emit_async("session_list", self.runner._session_manager.get_session_list())
        await self.runner._bus.emit_async(
            "current_session",
            {"id": self.runner._session_manager.get_current_session_id(), "title": "Current Session"},
        )
        await self.runner._broadcast_token_stats()
        await self.runner._emit("info", "New session started")
        now_ms = int(datetime.now().timestamp() * 1000)
        await self.runner._emit("turn_elapsed", {"started_ms": now_ms, "ended_ms": now_ms})

    async def _handle_withdraw_turn(self, timestamp: str, message_id: str | None = None) -> None:
        """Truncate session messages/events from *timestamp* and reload LLM context."""
        # Prefer AgentRunner implementation (wired into main + urgent loops).
        withdraw = getattr(self.runner, "_withdraw_turn", None)
        if callable(withdraw):
            await withdraw(timestamp, message_id=message_id or None)
            return
        self.runner._input_hub.clear_stop_request()
        result = self.runner._session_manager.truncate_from_timestamp(
            timestamp,
            inclusive=True,
            message_id=message_id,
        )
        logger.warning(
            "[RunCommandDispatcher] withdraw_turn ts=%s mid=%s ok=%s messages=%s events=%s",
            timestamp,
            message_id,
            result.get("ok"),
            result.get("messages"),
            result.get("events"),
        )
        self.runner._load_history()
        sid = self.runner._session_manager.get_current_session_id()
        history_data = {
            "messages": self.runner._session_manager.get_messages(),
            "events": self.runner._session_manager.get_events(),
            "session_id": sid,
            "is_working_session": True,
            "reason": "withdraw",
        }
        await self.runner._bus.emit_async("history_sync", history_data)
        await self.runner._broadcast_token_stats()
        await self.runner._emit("info", "Turn withdrawn")
        # Force idle so the UI can send again (stop_task may have left flags set)
        await self.runner._emit("state", "idle")
        now_ms = int(datetime.now().timestamp() * 1000)
        await self.runner._emit("turn_elapsed", {"started_ms": now_ms, "ended_ms": now_ms})

    async def _handle_load_session(self, sid: str) -> None:
        if self.runner._session_manager.load_history_session(sid):
            self.runner._turn_sid = sid
            self.runner._load_history()
            await self.runner._emit("turn_start", 0)
            history_data = {
                "messages": self.runner._session_manager.get_messages(),
                "events": self.runner._session_manager.get_events(),
                "session_id": sid,
                "is_working_session": True,
            }
            await self.runner._bus.emit_async("history_sync", history_data)
            await self.runner._bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
            await self.runner._bus.emit_async("session_list", self.runner._session_manager.get_session_list())
            await self.runner._broadcast_token_stats()
            await self.runner._emit("info", f"Session loaded: {sid}")
            now_ms = int(datetime.now().timestamp() * 1000)
            await self.runner._emit("turn_elapsed", {"started_ms": now_ms, "ended_ms": now_ms})
