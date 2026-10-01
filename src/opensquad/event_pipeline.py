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

v1.1: Events are bucketed by session_id so parallel panes do not cross-drain.
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
    session_id: str = ""

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

    def ui_steer_text(self) -> str:
        """The text the UI should show for this event when it lands mid-turn.

        Mid-turn chat messages are recorded as steers, and the UI recognises a
        machine-delivered message by its wire marker (`[DM] ss: hi`,
        `[Messages]\\n[<group> | group_id=…] ss: hi`). Without it a private message
        renders as a bare "插话", indistinguishable from the user interrupting.

        Web/gateway events are the user's own words, so they are returned as-is.
        """
        if self.source == "dm":
            sender = str(self.metadata.get("sender_name") or self.metadata.get("source_name") or "")
            return f"[DM] {sender}: {self.content}" if sender else self.content
        if self.source == "group":
            group = str(self.metadata.get("group_name") or self.metadata.get("source_name") or "")
            sender = str(self.metadata.get("sender_name") or "")
            group_id = str(self.metadata.get("group_id") or self.metadata.get("source_id") or "")
            if not (group and sender):
                return self.content
            tail = f" | group_id={group_id}" if group_id else ""
            return f"[Messages]\n[{group}{tail}] {sender}: {self.content}"
        return self.content


def resolve_pipeline_session_id(session_id: str | None = None) -> str:
    """Best-effort session id for routing pipeline push/drain."""
    sid = (session_id or "").strip()
    if sid:
        return sid
    try:
        from opensquad.session_parallel import get_turn_local

        tl = get_turn_local()
        if tl and tl.sid:
            return str(tl.sid).strip()
    except Exception:
        pass
    try:
        from opensquad.session_manager import get_session_manager
        from opensquad.session_parallel import resolve_primary_session_id

        sm = get_session_manager()
        return str(resolve_primary_session_id(sm) or sm.get_primary_session_id() or "").strip()
    except Exception:
        pass
    return ""


class EventPipeline:
    """
    Thread-safe per-session event buffer. All external inputs push here.
    Tools drain contents for their session before returning to LLM.
    """

    def __init__(self, max_size: int = 200):
        self._max_size = max_size
        self._events_by_sid: dict[str, deque] = {}
        # sid -> identities already delivered (bounded). One DM can reach the
        # runner through more than one path (message_queue and the input_hub
        # supplement drain), and without this the model reads the same message
        # twice and answers twice — the duplicate-DM the user sees.
        self._seen_by_sid: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._stats = {"pushed": 0, "drained": 0, "deduped": 0}

    @staticmethod
    def _identity(source: str, metadata: dict[str, Any] | None) -> str:
        """Stable id for one inbound message, or '' when it has none.

        Only *ids* are deduped: two identical-looking messages a user really
        did send twice are two different messages and must both arrive.
        """
        md = metadata or {}
        raw = md.get("raw_data")
        raw_id = raw.get("id") if isinstance(raw, dict) else ""
        return str(md.get("client_id") or raw_id or "").strip()

    def _bucket(self, sid: str) -> deque:
        key = sid or ""
        bucket = self._events_by_sid.get(key)
        if bucket is None:
            bucket = deque(maxlen=self._max_size)
            self._events_by_sid[key] = bucket
        return bucket

    def push_nowait(
        self,
        source: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ):
        """Sync push (non-async). Safe to call from sync code like input_hub.push()."""
        sid = resolve_pipeline_session_id(session_id)
        identity = self._identity(source, metadata)
        evt = PipelineEvent(
            source=source,
            content=content,
            metadata=metadata or {},
            session_id=sid,
        )
        with self._lock:
            if identity:
                seen = self._seen_by_sid.get(sid or "")
                pending = self._events_by_sid.get(sid or "")
                in_flight = False
                if pending:
                    in_flight = any(self._identity(e.source, e.metadata) == identity for e in pending)
                if in_flight or (seen and identity in seen):
                    self._stats["deduped"] += 1
                    logger.info(
                        "[EventPipeline] Dropped duplicate event sid=%s source=%s id=%s",
                        sid or "-",
                        source,
                        identity,
                    )
                    return
                if seen is None:
                    seen = deque(maxlen=self._max_size)
                    self._seen_by_sid[sid or ""] = seen
                seen.append(identity)
            self._bucket(sid).append(evt)
        self._stats["pushed"] += 1
        logger.debug("[EventPipeline] Pushed sid=%s source=%s content=%s", sid or "-", source, content[:80])

    def drain_sync(self, session_id: str | None = None) -> list[PipelineEvent]:
        """Sync drain for one session bucket (empty key when sid unknown)."""
        sid = resolve_pipeline_session_id(session_id)
        with self._lock:
            bucket = self._events_by_sid.get(sid or "")
            if not bucket:
                return []
            events = list(bucket)
            bucket.clear()
            if not bucket:
                self._events_by_sid.pop(sid or "", None)
        if events:
            self._stats["drained"] += len(events)
        return events

    def cancel_user_event(self, session_id: str | None, client_id: str) -> bool:
        """Remove one pending user event (by client_id) from a session bucket.

        Used by the steer（引导注入）撤回：消息已发出但模型尚未消费时，把它从
        注入队列里撤掉。只撤 web/gateway 来源的用户事件，不影响 vision 注入
        等内部事件。返回是否找到并移除。
        """
        cid = (client_id or "").strip()
        if not cid:
            return False
        sid = resolve_pipeline_session_id(session_id)
        with self._lock:
            bucket = self._events_by_sid.get(sid or "")
            if not bucket:
                return False
            kept = deque(maxlen=self._max_size)
            removed = False
            for evt in bucket:
                if (
                    not removed
                    and evt.source in ("web", "gateway", "dm")
                    and str(evt.metadata.get("client_id") or "") == cid
                ):
                    removed = True
                    continue
                kept.append(evt)
            if not removed:
                return False
            if kept:
                self._events_by_sid[sid or ""] = kept
            else:
                self._events_by_sid.pop(sid or "", None)
            # 撤回后同一条消息可能被重新发送：把它从已投递集合里去掉，
            # 否则那次重发会被判成重复而被丢掉。
            seen = self._seen_by_sid.get(sid or "")
            if seen:
                try:
                    seen.remove(cid)
                except ValueError:
                    pass
        if removed:
            logger.info("[EventPipeline] Cancelled user event sid=%s client_id=%s", sid or "-", cid)
        return removed

    def drain_formatted_sync(self, session_id: str | None = None) -> str:
        """Sync drain + format as LLM-readable string for one session."""
        events = self.drain_sync(session_id=session_id)
        if not events:
            return ""
        lines = ["", "--- External Events (arrived during processing) ---"]
        for evt in events:
            lines.append(evt.format_for_llm())
        lines.append("--- End External Events ---")
        return "\n".join(lines)

    def size_for(self, session_id: str | None = None) -> int:
        sid = resolve_pipeline_session_id(session_id)
        with self._lock:
            return len(self._events_by_sid.get(sid or "", ()))

    @property
    def size(self) -> int:
        with self._lock:
            return sum(len(bucket) for bucket in self._events_by_sid.values())

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
