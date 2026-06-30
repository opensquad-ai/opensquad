"""
Event Pipeline v1.0 — Unified external event buffer

All external active inputs flow through this pipeline:
  - Web user messages (from input_hub)
  - Group chat / DM messages (from message_queue)
  - Task supervisor alerts
  - Timer / scheduled events
  - Any other external push

Tools automatically drain pipeline contents before returning,
so the LLM sees accumulated events in the same inner-loop turn.

This enables the "never stop" architecture:
  LLM → tool call → drain pipeline → LLM sees events → continues inner loop
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PipelineEvent:
    """A single event in the pipeline."""

    source: str  # "web" | "group" | "dm" | "timer" | "task_watch" | "custom"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def format_for_llm(self) -> str:
        """Format this event for LLM consumption."""
        ts = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        if self.source == "web":
            return f"[Web User @ {ts}] {self.content}"
        elif self.source == "group":
            group = self.metadata.get("group_name", "") or self.metadata.get("source_name", "?")
            sender = self.metadata.get("sender_name", "?")
            return f"[Group: {group} | {sender} @ {ts}] {self.content}"
        elif self.source == "dm":
            sender = self.metadata.get("sender_name", "") or self.metadata.get("source_name", "?")
            return f"[DM: {sender} @ {ts}] {self.content}"
        elif self.source == "task_watch":
            return f"[Task Supervisor @ {ts}] {self.content}"
        elif self.source == "timer":
            return f"[Timer @ {ts}] {self.content}"
        else:
            return f"[{self.source} @ {ts}] {self.content}"


class EventPipeline:
    """
    Thread-safe event buffer. All external inputs push here.
    Tools drain contents before returning to LLM.
    """

    def __init__(self, max_size: int = 200):
        self._events: deque = deque(maxlen=max_size)
        # BUGFIX: single threading.Lock for all access paths
        # (was asyncio.Lock + threading.Lock — two different locks on the same deque → races)
        self._lock = threading.Lock()
        self._stats = {"pushed": 0, "drained": 0}

    def push_nowait(self, source: str, content: str, metadata: dict[str, Any] | None = None):
        """Sync push (non-async). Safe to call from sync code like input_hub.push()."""
        evt = PipelineEvent(
            source=source,
            content=content,
            metadata=metadata or {},
        )
        with self._lock:
            self._events.append(evt)
        self._stats["pushed"] += 1
        logger.debug(f"[EventPipeline] Pushed: {source} - {content[:80]}")

    def drain_sync(self) -> list[PipelineEvent]:
        """Sync drain. Thread-safe, for use from sync code paths."""
        with self._lock:
            events = list(self._events)
            self._events.clear()
        if events:
            self._stats["drained"] += len(events)
        return events

    def drain_formatted_sync(self) -> str:
        """Sync drain + format as LLM-readable string."""
        events = self.drain_sync()
        if not events:
            return ""
        lines = ["", "--- External Events (arrived during processing) ---"]
        for evt in events:
            lines.append(evt.format_for_llm())
        lines.append("--- End External Events ---")
        return "\n".join(lines)

    async def push(self, source: str, content: str, metadata: dict[str, Any] | None = None):
        """Push an event into the pipeline."""
        evt = PipelineEvent(
            source=source,
            content=content,
            metadata=metadata or {},
        )
        with self._lock:
            self._events.append(evt)
        self._stats["pushed"] += 1
        self._has_events.set()
        logger.debug(f"[EventPipeline] Pushed: {source} — {content[:80]}")

    async def drain(self) -> list[PipelineEvent]:
        """
        Drain all accumulated events. Called by tool execution path
        before returning results to LLM.
        """
        with self._lock:
            events = list(self._events)
            self._events.clear()
        if events:
            self._stats["drained"] += len(events)
            self._has_events.clear()
            logger.debug(f"[EventPipeline] Drained {len(events)} event(s)")
        return events

    async def drain_formatted(self) -> str:
        """Drain and return as formatted string for LLM attachment."""
        events = await self.drain()
        if not events:
            return ""
        lines = ["", "--- External Events (arrived during processing) ---"]
        for evt in events:
            lines.append(evt.format_for_llm())
        lines.append("--- End External Events ---")
        return "\n".join(lines)

    async def has_pending(self) -> bool:
        """Check if there are pending events (non-blocking)."""
        return len(self._events) > 0

    @property
    def size(self) -> int:
        return len(self._events)

    @property
    def stats(self) -> dict:
        return dict(self._stats)


# Global singleton
event_pipeline = EventPipeline()


# ── AgentContext-aware getter (Phase 1a) ──
def get_event_pipeline(ctx=None):
    """Return event_pipeline from AgentContext if available, else global singleton."""
    if ctx is not None:
        return ctx.event_pipeline
    from opensquad._context import get_current_context

    ctx = get_current_context()
    return ctx.event_pipeline if ctx is not None else event_pipeline
