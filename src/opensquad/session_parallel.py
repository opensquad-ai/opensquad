"""Parallel multi-session support: turn-local state, concurrency caps, tool write mutex."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Default max concurrent LLM/tool turns per agent process (plan: MAX_PARALLEL_TURNS=4).
MAX_PARALLEL_TURNS = max(1, int(os.environ.get("OPENSQUAD_MAX_PARALLEL_TURNS", "4")))

# Channels that always route to the agent's primary session (external ingress).
EXTERNAL_CHANNELS = frozenset(
    {
        "telegram",
        "telegram_group",
        "telegram_private",
        "feishu",
        "feishu_group",
        "feishu_private",
        "wecom",
        "dingtalk",
        "qq",
        "whatsapp",
        "discord",
        "slack",
        "api",
        "external",
        "group",
        "chatpro",
    }
)

# Agent-level mutex for filesystem / cwd mutating tools (shared workspace).
_tool_write_lock: asyncio.Lock | None = None


def get_tool_write_lock() -> asyncio.Lock:
    global _tool_write_lock
    if _tool_write_lock is None:
        _tool_write_lock = asyncio.Lock()
    return _tool_write_lock


def is_external_channel(channel: str | None) -> bool:
    ch = (channel or "").strip().lower()
    if not ch or ch in ("web", "cli", "gateway"):
        return False
    if ch in EXTERNAL_CHANNELS:
        return True
    # Prefix match: telegram_*, feishu_*, etc.
    return any(ch.startswith(prefix) for prefix in ("telegram", "feishu", "wecom", "dingtalk"))


@dataclass
class TurnLocal:
    """Per-asyncio-Task turn state so concurrent sessions do not clobber each other."""

    sid: str = ""
    chat_api: Any = None
    agent_mode: str = ""
    images: list = field(default_factory=list)
    attachments: list = field(default_factory=list)
    channel: str = ""
    source_chat_id: str = ""
    group_id: str = ""
    user_id: str = ""
    input_source: str = "unknown"
    last_user_input: str = ""
    tool_result_images: list = field(default_factory=list)
    tool_result_image_paths: list = field(default_factory=list)
    turn: int = 0
    round: int = 0
    turn_started_ms: float = 0.0
    workflow_started_ms: float = 0.0
    in_task: bool = False
    awaiting_user_reply: bool = False
    last_user_msg_from_to_user: bool = False
    auto_continue_retries: int = 0
    streamed_user_tag: Any = None
    dynamic_context_prefix: str = ""
    current_tools: Any = None
    current_tool_choice: str = "auto"


_cv_turn: contextvars.ContextVar[TurnLocal | None] = contextvars.ContextVar("opensquad_turn_local", default=None)


def get_turn_local() -> TurnLocal | None:
    return _cv_turn.get()


def set_turn_local(tl: TurnLocal | None) -> contextvars.Token:
    return _cv_turn.set(tl)


def reset_turn_local(token: contextvars.Token) -> None:
    _cv_turn.reset(token)


class ParallelTurnScheduler:
    """Tracks in-flight session turns and enforces MAX_PARALLEL_TURNS."""

    def __init__(self, max_parallel: int = MAX_PARALLEL_TURNS):
        self.max_parallel = max_parallel
        self._tasks: dict[str, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(max_parallel)
        self._busy_sessions: set[str] = set()

    @property
    def busy_sessions(self) -> set[str]:
        return set(self._busy_sessions)

    def is_session_busy(self, sid: str) -> bool:
        t = self._tasks.get(sid)
        return t is not None and not t.done()

    def reap(self) -> list[str]:
        """Remove finished tasks; return finished session ids."""
        done: list[str] = []
        for sid, task in list(self._tasks.items()):
            if task.done():
                done.append(sid)
                self._tasks.pop(sid, None)
                self._busy_sessions.discard(sid)
                self._sem.release()
                exc = task.exception() if not task.cancelled() else None
                if exc:
                    logger.error("[ParallelTurnScheduler] turn failed sid=%s: %s", sid, exc, exc_info=exc)
        return done

    async def acquire_slot(self, sid: str, timeout: float | None = 2.0) -> bool:
        """Wait until a parallel slot is free and this sid is not already running.

        Returns False if *sid* is already busy, or if *timeout* elapses while
        waiting for a free slot (so the dispatcher is never permanently stalled).
        """
        while True:
            self.reap()
            if self.is_session_busy(sid):
                return False
            try:
                if timeout is None:
                    await self._sem.acquire()
                else:
                    await asyncio.wait_for(self._sem.acquire(), timeout=timeout)
            except asyncio.TimeoutError:
                return False
            self.reap()
            if self.is_session_busy(sid):
                self._sem.release()
                return False
            return True

    def start(self, sid: str, coro) -> asyncio.Task:
        self._busy_sessions.add(sid)

        async def _wrapped():
            try:
                return await coro
            finally:
                self._busy_sessions.discard(sid)

        task = asyncio.create_task(_wrapped(), name=f"session-turn:{sid}")
        self._tasks[sid] = task
        return task

    def request_stop_session(self, sid: str) -> None:
        task = self._tasks.get(sid)
        if task and not task.done():
            task.cancel()
