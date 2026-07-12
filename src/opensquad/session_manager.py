import asyncio
import contextlib
import copy
import json
import logging
import os
import queue  # thread-safe Queue (replaces asyncio.Queue for sync/async mixed access)
import random
import re
import string
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from opensquad.time_utils import utc_now_iso

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Continuous conversation manager.
    - Automatically saves conversation history
    - Automatically loads the last conversation on startup
    - Singleton pattern: always maintains one continuous session
    """

    def __init__(self, save_dir: str | None = None, history_dir: str | None = None, cache_size: int = 10):
        # -- Phase 2.2: Use workspace path --
        from opensquad.system_config import syscfg

        self.save_dir = save_dir or syscfg.workspace_sessions_dir()
        self.history_dir = history_dir or syscfg.workspace_data_dir("ai_his_talk")
        self.current_session_file = os.path.join(self.save_dir, "current_session.json")
        self._lock = threading.Lock()
        self.session_data = {
            "id": None,
            "title": None,
            "messages": [],
            "events": [],  # Full interaction events: thought, tool_call, tool_result, etc.
            "latest_summary": "",
            "last_updated": None,
            "created_at": None,
        }

        # LRU session cache: OrderedDict maintains access order; most recently accessed at the end
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._cache_max_size = cache_size

        # Deferred write control: add_event() no longer writes on every call;
        # instead it batches writes when conditions are met
        self._dirty_event_count = 0  # dirty event count since last write
        self._last_save_time = 0.0  # monotonic timestamp of last write
        _FLUSH_INTERVAL = 2.0  # write at most once every 2 seconds
        _FLUSH_EVENT_BATCH = 10  # or after accumulating 10 events
        self._FLUSH_INTERVAL = _FLUSH_INTERVAL
        self._FLUSH_EVENT_BATCH = _FLUSH_EVENT_BATCH

        # ---- Async batch writer (P0-1: decouple I/O from main loop) ----
        # All non-critical writes go through this queue; a background task
        # batches them to disk so add_event()/add_message() return immediately.
        self._write_queue: queue.Queue | None = None
        self._writer_task: asyncio.Task | None = None
        self._writer_flush_interval = 0.5  # seconds between background flushes
        self._writer_batch_size = 20  # max items to coalesce per flush
        self._writer_running = False
        self._writer_shutdown_event: asyncio.Event | None = None
        self._writer_idle_event: asyncio.Event | None = None

        # Save sequence number: monotonically increasing counter written to disk
        # with every _save_session().  Prevents the async writer from overwriting
        # a newer session_data with stale data.  Initialized from disk on load.
        self._save_seq: int = 0

        # Ensure directories exist
        os.makedirs(self.save_dir, exist_ok=True)
        os.makedirs(self.history_dir, exist_ok=True)

        # Load existing session
        self._load_session()

    # ---- LRU cache operations ----

    def _get_history_file_mtime(self, sid: str) -> float | None:
        """Get the mtime of a history file; returns None if it does not exist."""
        fp = os.path.join(self.history_dir, f"{sid}.json")
        try:
            return os.path.getmtime(fp) if os.path.exists(fp) else None
        except OSError:
            return None

    def _cache_put(self, sid: str, data: dict):
        """Put session data into the cache (deep copy + record disk mtime for staleness check)."""
        if not sid:
            return
        if sid in self._cache:
            self._cache.move_to_end(sid)  # Move to end (most recently used)
        mtime = self._get_history_file_mtime(sid)
        self._cache[sid] = {
            "data": copy.deepcopy(data),
            "mtime": mtime,
        }
        # Evict the least recently used entry (OrderedDict head) when over capacity
        while len(self._cache) > self._cache_max_size:
            evicted_sid, _ = self._cache.popitem(last=False)
            logger.info(f"[SessionManager] Cache evicted: {evicted_sid}")

    def _cache_get(self, sid: str) -> dict | None:
        """Get session data from cache (validates mtime on hit; returns deep copy)."""
        if sid not in self._cache:
            return None
        entry = self._cache[sid]
        # mtime validation: invalidate cache if disk file was updated by another process
        disk_mtime = self._get_history_file_mtime(sid)
        cached_mtime = entry.get("mtime")
        if disk_mtime is not None and cached_mtime is not None and disk_mtime != cached_mtime:
            logger.info(f"[SessionManager] Cache stale (mtime changed): {sid}")
            del self._cache[sid]
            return None
        self._cache.move_to_end(sid)
        return copy.deepcopy(entry["data"])

    def _cache_remove(self, sid: str):
        """Remove an entry from the cache."""
        self._cache.pop(sid, None)

    # ---- Events and messages ----

    # ---- Async batch writer lifecycle ----

    def start_async_writer(self, loop: asyncio.AbstractEventLoop | None = None):
        """Start the background async writer task.

        Must be called from within an async context (e.g. agents_boot.py main()).
        """
        if self._writer_running:
            return
        if loop is None:
            loop = asyncio.get_running_loop()
        self._write_queue = queue.Queue()
        self._writer_shutdown_event = asyncio.Event()
        self._writer_idle_event = asyncio.Event()
        self._writer_idle_event.set()
        self._writer_task = loop.create_task(self._async_save_loop())
        self._writer_running = True
        logger.info("[SessionManager] Async writer started")

    async def stop_async_writer(self, timeout: float = 5.0):
        """Gracefully stop the background writer, draining pending writes.

        Must be awaited from within an async context.
        """
        if not self._writer_running or self._writer_task is None:
            return
        self._writer_running = False
        # Signal the loop to exit after the current batch
        self._writer_shutdown_event.set()
        try:
            await asyncio.wait_for(self._writer_task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("[SessionManager] Async writer drain timed out, forcing cancel")
            self._writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer_task
        self._writer_task = None
        self._write_queue = None
        self._writer_shutdown_event = None
        logger.info("[SessionManager] Async writer stopped")

    async def _async_save_loop(self):
        """Background consumer: batches queued writes and flushes to disk."""
        while self._writer_running:
            try:
                # Wait for the flush interval or shutdown signal
                await asyncio.wait_for(
                    self._writer_shutdown_event.wait(),
                    timeout=self._writer_flush_interval,
                )
            except asyncio.TimeoutError:
                pass  # Normal: interval elapsed, check queue

            if not self._writer_running:
                break

            # Drain queue up to batch size
            batch = []
            for _ in range(self._writer_batch_size):
                try:
                    batch.append(self._write_queue.get_nowait())
                except queue.Empty:
                    break

            if batch:
                # Clear idle before processing (writer is busy)
                if self._writer_idle_event:
                    self._writer_idle_event.clear()
                # Snapshot the save_seq BEFORE applying mutations, so we can
                # detect whether a concurrent sync save (e.g. add_event for
                # tool_call) has already written a newer version to disk.
                seq_before = self._save_seq
                # Apply all queued mutations (they are lightweight closures)
                for mutation in batch:
                    mutation()
                # Only save if nobody else has saved since we started the batch.
                # If add_event('tool_call', ...) ran between seq_before and now,
                # self._save_seq > seq_before, meaning the file on disk is already
                # newer than what we would write — skip to avoid stale overwrite.
                if self._save_seq > seq_before:
                    logger.debug(
                        "[SessionManager] Async writer skip save: seq %d > %d (concurrent sync save detected)",
                        self._save_seq,
                        seq_before,
                    )
                else:
                    self._save_session()
                # Mark idle after flush is complete
                if self._writer_idle_event:
                    self._writer_idle_event.set()
                logger.debug(
                    f"[SessionManager] Async flush: {len(batch)} mutation(s), seq_before={seq_before}, seq_after={self._save_seq}"
                )

        # Final drain on shutdown
        final_batch = []
        while True:
            try:
                final_batch.append(self._write_queue.get_nowait())
            except queue.Empty:
                break
        if final_batch:
            for mutation in final_batch:
                mutation()
            self._save_session()
            if self._writer_idle_event:
                self._writer_idle_event.set()
            logger.info(f"[SessionManager] Final drain: {len(final_batch)} mutation(s)")

    def _enqueue_mutation(self, mutation: callable):
        """Enqueue a lightweight mutation closure if the async writer is active;
        otherwise fall back to synchronous _save_session().

        Each mutation captures a snapshot of self._save_seq at enqueue time.
        The async writer uses this to detect whether the mutation is stale
        (i.e. a concurrent sync save has already superseded it).
        """
        if self._writer_running and self._write_queue is not None:
            try:
                self._write_queue.put_nowait(mutation)
                return
            except queue.Full:
                pass  # Fallback to sync
        # Synchronous fallback (boot phase or queue overflow)
        mutation()
        self._save_session()

    def add_event(self, event_type: str, event_data: dict, turn_id: int | None = None, round_id: int | None = None):
        """Add an interaction event to history.

        Non-critical events are enqueued for async batch flush.
        tool_call / tool_result events still flush synchronously to guarantee
        crash-recoverable state before tool execution.
        """

        def _mutate():
            event = {
                "type": event_type,
                "data": event_data,
                "timestamp": utc_now_iso(),
            }
            if turn_id is not None:
                event["turn_id"] = turn_id
            if round_id is not None:
                event["round_id"] = round_id
            self.session_data["events"].append(event)
            self.session_data["last_updated"] = utc_now_iso()
            # Limit event history length
            if len(self.session_data["events"]) > 2000:
                self.session_data["events"] = self.session_data["events"][-2000:]

        if event_type in ("tool_call", "tool_result"):
            # Layer 3b: critical events — drain any pending async mutations first
            # to ensure the saved snapshot includes all prior add_message() calls,
            # then append the event and flush synchronously.
            self._drain_pending_mutations_sync()
            _mutate()
            self._save_session()
            self._last_save_time = time.monotonic()
        else:
            self._enqueue_mutation(_mutate)

    def _flush_if_dirty(self):
        """DEPRECATED: kept for backward-compat callers.
        With the async writer, flushes happen automatically in the background.
        """
        if not self._writer_running:
            # Fallback when writer is not running (e.g. during boot before start_async_writer)
            if self._dirty_event_count == 0:
                return
            now = time.monotonic()
            elapsed = now - self._last_save_time
            if elapsed >= self._FLUSH_INTERVAL or self._dirty_event_count >= self._FLUSH_EVENT_BATCH:
                self._save_session()
                self._dirty_event_count = 0
                self._last_save_time = now

    def _load_session(self):
        """Load the current session; if empty, try loading the most recent history session."""
        loaded = False
        if os.path.exists(self.current_session_file):
            try:
                with open(self.current_session_file, encoding="utf-8") as f:
                    self.session_data = json.load(f)
                # Ensure fields exist (backward compatibility)
                if "events" not in self.session_data:
                    self.session_data["events"] = []
                if "latest_summary" not in self.session_data:
                    self.session_data["latest_summary"] = ""
                if "id" not in self.session_data:
                    self.session_data["id"] = self._generate_id()
                if "title" not in self.session_data:
                    self.session_data["title"] = None
                if "archived_messages" not in self.session_data:
                    self.session_data["archived_messages"] = []
                if "archived_events" not in self.session_data:
                    self.session_data["archived_events"] = []
                # Restore save_seq from disk to prevent stale async-writer overwrites
                self._save_seq = self.session_data.get("_save_seq", 0)

                # If the current session has no messages, try loading history
                if not self.session_data.get("messages"):
                    logger.info("[SessionManager] Current session is empty, trying to load latest history")
                else:
                    loaded = True
                    logger.info(
                        f"[SessionManager] Loaded session with {len(self.session_data['messages'])} messages and {len(self.session_data.get('events', []))} events"
                    )
            except Exception as e:
                logger.error(f"[SessionManager] Failed to load session: {e}")

        if not loaded:
            # Try to find the latest history file
            history_dir = self.history_dir
            if os.path.exists(history_dir):
                files = [f for f in os.listdir(history_dir) if f.endswith(".json")]
                if files:
                    files.sort(key=lambda x: os.path.getmtime(os.path.join(history_dir, x)), reverse=True)
                    latest_sid = files[0].replace(".json", "")
                    logger.info(f"[SessionManager] Auto-loading latest history: {latest_sid}")
                    if self.load_history_session(latest_sid):
                        return

            self._init_new_session()

    def _generate_id(self):
        now = datetime.now(timezone.utc)
        rand_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        return f"{now.strftime('%Y%m%d_%H%M%S')}_{rand_suffix}"

    def _init_new_session(self):
        """Initialize a new session."""
        now_iso = utc_now_iso()
        sid = self._generate_id()

        self.session_data = {
            "id": sid,
            "title": None,
            "messages": [],
            "events": [],  # Interaction event history
            "archived_messages": [],  # Messages removed by context compression, kept for UI display
            "archived_events": [],  # Events removed by context compression, kept for UI display
            "latest_summary": "",
            "last_updated": now_iso,
            "created_at": now_iso,
        }
        logger.info(f"[SessionManager] Initialized new session: {sid}")
        self._save_session()

    def get_session_history(self, session_id: str) -> dict | None:
        """Read a history session without modifying the current session."""
        # If it's the current session, return directly
        if session_id == self.session_data.get("id"):
            return self.session_data

        # Check cache
        cached = self._cache_get(session_id)
        if cached is not None:
            logger.info(f"[SessionManager] Cache hit (read-only): {session_id}")
            return cached

        # Cache miss: read from disk
        history_dir = self.history_dir
        file_path = os.path.join(history_dir, f"{session_id}.json")
        if not os.path.exists(file_path):
            logger.error(f"[SessionManager] Session file not found: {file_path}")
            return None

        try:
            with open(file_path, encoding="utf-8") as f:
                content = json.load(f)

            if isinstance(content, list):
                data = {
                    "id": session_id,
                    "messages": content,
                    "events": [],
                    "latest_summary": "",
                    "last_updated": utc_now_iso(),
                    "created_at": utc_now_iso(),
                }
            else:
                data = content
                data["id"] = session_id
                if "events" not in data:
                    data["events"] = []
                if "latest_summary" not in data:
                    data["latest_summary"] = ""

            # Put into cache
            self._cache_put(session_id, data)
            return data
        except Exception as e:
            logger.error(f"[SessionManager] Failed to read history session: {e}")
            return None

    def load_history_session(self, session_id: str) -> bool:
        """Load a specific history session (replaces the current session)."""
        # If already the current session, return True immediately
        if self.session_data.get("id") == session_id:
            return True

        # Archive the current session before switching (if it has content)
        self.archive_current_session()

        # 1. Check cache first
        cached = self._cache_get(session_id)
        if cached is not None:
            logger.info(f"[SessionManager] Cache hit (switch): {session_id}")
            self.session_data = cached
            self._save_session()
            return True

        # 2. Cache miss: read from disk
        history_dir = self.history_dir
        file_path = os.path.join(history_dir, f"{session_id}.json")

        if not os.path.exists(file_path):
            logger.error(f"[SessionManager] Session file not found: {file_path}")
            return False

        try:
            with open(file_path, encoding="utf-8") as f:
                content = json.load(f)

            if isinstance(content, list):
                self.session_data = {
                    "id": session_id,
                    "messages": content,
                    "events": [],
                    "latest_summary": "",
                    "last_updated": utc_now_iso(),
                    "created_at": utc_now_iso(),
                }
            else:
                self.session_data = content
                self.session_data["id"] = session_id
                if "events" not in self.session_data:
                    self.session_data["events"] = []
                if "latest_summary" not in self.session_data:
                    self.session_data["latest_summary"] = ""

            # Put into cache
            self._cache_put(session_id, self.session_data)

            self._save_session()
            logger.info(f"[SessionManager] Loaded history session (disk): {session_id}")
            return True
        except Exception as e:
            logger.error(f"[SessionManager] Failed to load history session: {e}")
            return False

    def archive_current_session(self):
        """Archive the current session to history."""
        if not self.session_data.get("messages"):
            return

        history_dir = self.history_dir
        os.makedirs(history_dir, exist_ok=True)

        sid = self.session_data.get("id")
        if not sid:
            sid = self._generate_id()
            self.session_data["id"] = sid

        # Cache the session before switching away so a switch-back doesn't need to read disk
        self._cache_put(sid, self.session_data)

        file_path = os.path.join(history_dir, f"{sid}.json")
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self.session_data, f, indent=2, ensure_ascii=False)
            logger.info(f"[SessionManager] Archived session: {sid}")
        except Exception as e:
            logger.error(f"[SessionManager] Failed to archive session: {e}")

    def start_new_session(self):
        """Start a new session (automatically archives the old one).

        Critical ordering: the new empty session file is written to disk
        (current_session.json) BEFORE the old session is archived to history/.

        Why this matters: if the agent process crashes between these two
        operations, the new (empty) session is already on disk. On restart,
        the agent loads the empty session instead of the old one, preventing
        "session bounce" (user clicks New Session → old session reappears).

        The old approach did archive-first → init-new-second, which meant a
        crash after archiving but before writing the new file would leave
        the old current_session.json intact, causing the bounce.
        """
        with self._lock:
            self._drain_pending_mutations_sync()
            # Snapshot old session data before we overwrite session_data
            old_data = copy.deepcopy(self.session_data) if self.session_data.get("messages") else None
            # 1. Write new empty session to current_session.json FIRST
            self._init_new_session()
            # 2. Archive old session to history/ SECOND (non-critical for crash recovery)
            if old_data:
                sid = old_data.get("id")
                if sid:
                    self._cache_put(sid, old_data)
                history_dir = self.history_dir
                os.makedirs(history_dir, exist_ok=True)
                file_path = os.path.join(history_dir, f"{sid}.json")
                try:
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(old_data, f, indent=2, ensure_ascii=False)
                    logger.info(f"[SessionManager] Archived session: {sid}")
                except Exception as e:
                    logger.error(f"[SessionManager] Failed to archive session: {e}")

    def _drain_pending_mutations_sync(self):
        """Apply queued async mutations before replacing session_data.

        NOTE: This method must NOT block the event loop with time.sleep().
        Prior versions used a 5-second busy-wait for the async writer to
        become idle, which blocked the entire asyncio event loop and caused
        WebSocket heartbeats to time out (→ disconnect → new session bounce).

        The fix: skip the wait and drain whatever is immediately available.
        Any mutations currently being processed by the async writer have
        already been dequeued and are being applied inline to self.session_data
        — they will be reflected in the archive.  Any mutations still in the
        queue are captured here.
        """
        if not self._writer_running or self._write_queue is None:
            return
        batch = []
        while True:
            try:
                batch.append(self._write_queue.get_nowait())
            except queue.Empty:
                break
        for mutation in batch:
            try:
                mutation()
            except Exception as e:
                logger.warning(f"[SessionManager] Pending mutation failed during drain: {e}")

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimation: 1 token ≈ 1.3 chars for mixed CJK/English."""
        if not text:
            return 0
        return max(1, len(text) // 2)

    # Caps for the archived history kept for UI display only.
    # These messages/events are removed from the LLM context (chat_api.req)
    # but preserved in session_data["archived_*"] so the frontend can still
    # render them inside a collapsible "已归档" section.
    _ARCHIVED_MESSAGES_CAP = 5000
    _ARCHIVED_EVENTS_CAP = 10000

    @staticmethod
    def _item_timestamp_ms(item: dict[str, Any]) -> float:
        """Parse ISO timestamp to epoch-ms; missing/invalid → +inf (sort last)."""
        raw = item.get("timestamp")
        if not raw or not isinstance(raw, str):
            return float("inf")
        try:
            # Support both "...Z" and "+00:00" forms
            normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
            return datetime.fromisoformat(normalized).timestamp() * 1000.0
        except (ValueError, TypeError, OSError):
            return float("inf")

    @staticmethod
    def _tool_pair_id(evt: dict[str, Any]) -> str | None:
        """Stable id linking a tool_call to its tool_result, if present."""
        data = evt.get("data") if isinstance(evt.get("data"), dict) else {}
        if not isinstance(data, dict):
            return None
        tid = data.get("id") or data.get("tool_use_id")
        return str(tid) if tid else None

    def compress_current_session(
        self,
        keep_ratio: float | None = None,
        previous_summary: str = "",
        external_summary: str = "",
        keep_from_timestamp_ms: float | None = None,
    ) -> dict[str, Any]:
        """Compress session context based on token count or a timestamp cut.

        Keeps the newest `keep_ratio` of tokens from messages + events, unless
        `keep_from_timestamp_ms` is set — then every unit whose newest item is
        strictly before that timestamp is archived (aligns disk archive with
        chat_api auto-compression's recent_start boundary).

        Removed items are stored in archived_messages / archived_events.
        """
        if keep_ratio is None:
            try:
                from opensquad.system_config import syscfg

                keep_ratio = float(syscfg.ctx_keep_recent_fraction())
            except Exception:
                keep_ratio = 0.1

        messages = list(self.session_data.get("messages", []))
        events = list(self.session_data.get("events", []))

        # Build a unified chronological list. Previously messages were appended
        # first and events second, so walking from the "end" kept all events
        # before any messages — splitting tool pairs across the archive boundary
        # and scrambling the frontend timeline after hydration.
        items: list[dict[str, Any]] = []
        for i, msg in enumerate(messages):
            content = str(msg.get("content", ""))
            tc = self._estimate_tokens(content)
            items.append(
                {
                    "idx": i,
                    "kind": "message",
                    "item": msg,
                    "tokens": tc,
                    "ts": self._item_timestamp_ms(msg),
                    "order": i,
                }
            )
        for i, evt in enumerate(events):
            data = evt.get("data", evt.get("content", ""))
            text = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data or "")
            tc = self._estimate_tokens(text)
            items.append(
                {
                    "idx": i,
                    "kind": "event",
                    "item": evt,
                    "tokens": tc,
                    "ts": self._item_timestamp_ms(evt),
                    "order": len(messages) + i,
                }
            )

        # Chronological order; same-ts: events before messages (matches UI builder),
        # then original insertion order as tie-break.
        items.sort(
            key=lambda it: (
                it["ts"],
                0 if it["kind"] == "event" else 1,
                it["order"],
            )
        )

        # Group tool_call + matching tool_result into atomic units so a cut
        # never archives the call while leaving the result live (or vice versa).
        units: list[list[dict[str, Any]]] = []
        pending_tool: dict[str, list[dict[str, Any]]] = {}
        for it in items:
            if it["kind"] == "event":
                evt = it["item"]
                etype = evt.get("type")
                pair_id = self._tool_pair_id(evt) if etype in ("tool_call", "tool_result") else None
                if pair_id and etype == "tool_call":
                    group = [it]
                    pending_tool[pair_id] = group
                    units.append(group)
                    continue
                if pair_id and etype == "tool_result" and pair_id in pending_tool:
                    pending_tool[pair_id].append(it)
                    continue
            units.append([it])

        total_tokens = sum(it["tokens"] for it in items)
        keep_tokens = int(total_tokens * keep_ratio)

        kept_messages: list[dict[str, Any]] = []
        kept_events: list[dict[str, Any]] = []
        compressed_messages: list[dict[str, Any]] = []
        compressed_events: list[dict[str, Any]] = []
        budget = keep_tokens
        used_timestamp_cut = keep_from_timestamp_ms is not None and keep_from_timestamp_ms < float("inf")

        # Walk units newest → oldest; each unit is kept or archived as a whole.
        # Prepend the whole unit (in chronological order) so tool_call stays
        # before its tool_result — per-item insert(0) would reverse pairs.
        for unit in reversed(units):
            unit_tokens = sum(it["tokens"] for it in unit)
            if used_timestamp_cut:
                unit_newest_ts = max(it["ts"] for it in unit)
                keep_unit = unit_newest_ts >= float(keep_from_timestamp_ms)
            else:
                keep_unit = budget > 0 and unit_tokens <= budget
                if keep_unit:
                    budget -= unit_tokens
            unit_msgs = [it["item"] for it in unit if it["kind"] == "message"]
            unit_evts = [it["item"] for it in unit if it["kind"] == "event"]
            if keep_unit:
                kept_messages = unit_msgs + kept_messages
                kept_events = unit_evts + kept_events
            else:
                compressed_messages = unit_msgs + compressed_messages
                compressed_events = unit_evts + compressed_events

        if used_timestamp_cut:
            budget = sum(self._estimate_tokens(str(m.get("content", ""))) for m in kept_messages)

        summary_content = external_summary.strip() if external_summary else ""
        if not summary_content:
            summary_content = previous_summary.strip()

        if not summary_content:
            summary_content = (
                "## Current Task\n- (unknown - summarization failed)\n\n"
                "## Original Goal\n- (unknown - summarization failed)\n\n"
                "## Completed\n- Done: (unknown)\n- In progress: (unknown)\n- Todo: (unknown)\n\n"
                "## Current State\n- (unknown - summarization failed)\n\n"
                "## Key Parameters\n- (none)\n\n"
                "## Unresolved Issues\n- (none)"
            )

        # Update session: keep only the newest messages/events. Removed
        # items are appended to the archived_* arrays (capped) so the UI
        # can still display them after refresh.
        def _mutate():
            self.session_data["messages"] = kept_messages
            self.session_data["events"] = kept_events
            # Append, not replace, so repeated compressions preserve the
            # full conversation history. The cap trims the oldest entries
            # if the session is compressed many times.
            existing_msgs = self.session_data.get("archived_messages") or []
            merged_msgs = existing_msgs + compressed_messages
            self.session_data["archived_messages"] = merged_msgs[-self._ARCHIVED_MESSAGES_CAP :]
            existing_evts = self.session_data.get("archived_events") or []
            merged_evts = existing_evts + compressed_events
            self.session_data["archived_events"] = merged_evts[-self._ARCHIVED_EVENTS_CAP :]
            self.session_data["latest_summary"] = summary_content
            self.session_data["last_updated"] = utc_now_iso()

        # Compression is a rare operation; sync flush to guarantee immediate persistence
        _mutate()
        self._save_session()

        return {
            "compressed": True,
            "total_tokens": total_tokens,
            "kept_tokens": total_tokens - budget if not used_timestamp_cut else budget,
            "keep_ratio": keep_ratio,
            "keep_from_timestamp_ms": keep_from_timestamp_ms,
            "compressed_messages": len(compressed_messages),
            "compressed_events": len(compressed_events),
            "kept_messages": len(kept_messages),
            "kept_events": len(kept_events),
            "archived_messages_count": len(self.session_data.get("archived_messages") or []),
            "archived_events_count": len(self.session_data.get("archived_events") or []),
            "summary_content": summary_content,
        }

    def add_message(
        self,
        role: str,
        content: str,
        msg_type: str = "text",
        images: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        output_audio: list[dict[str, Any]] | None = None,
        output_images: list[str] | None = None,
        **extra_fields,
    ):
        logger.info(
            f"[SessionManager] add_message: role={role}, content_len={len(content)}, content_preview={content[:80]}"
        )

        def _mutate():
            message = {
                "role": role,
                "content": content,
                "type": msg_type,
                "timestamp": utc_now_iso(),
            }
            if images:
                message["images"] = images
            if attachments:
                message["attachments"] = attachments
            if output_audio:
                message["output_audio"] = output_audio
            if output_images:
                message["output_images"] = output_images
            if extra_fields:
                message.update(extra_fields)

            self.session_data["messages"].append(message)
            self.session_data["last_updated"] = utc_now_iso()
            # Provisional session title from the first user message until agent names it.
            if role == "user" and not self.session_data.get("title") and not self.session_data.get("title_locked"):
                provisional = self._title_from_user_content(content)
                if provisional:
                    self.session_data["title"] = provisional
            if len(self.session_data["messages"]) > 1000:
                self.session_data["messages"] = self.session_data["messages"][-1000:]

        # P0-1: enqueue mutation for async flush; sync fallback if writer not running
        self._enqueue_mutation(_mutate)

    def _adopt_disk_title_lock(self) -> None:
        """
        If Gateway/UI renamed the current session on disk (title_locked), adopt it
        before we overwrite the file — so user titles survive the live agent.
        """
        if self.session_data.get("title_locked"):
            return
        path = self.current_session_file
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                disk = json.load(f)
            if not isinstance(disk, dict):
                return
            if disk.get("id") != self.session_data.get("id"):
                return
            if disk.get("title_locked") and str(disk.get("title") or "").strip():
                self.session_data["title"] = str(disk["title"]).strip()
                self.session_data["title_locked"] = True
        except Exception:
            pass

    def _save_session(self):
        try:
            self._adopt_disk_title_lock()
            msg_count = len(self.session_data.get("messages", []))
            evt_count = len(self.session_data.get("events", []))
            sid = self.session_data.get("id", "unknown")
            # Increment save_seq and stamp it onto the data so _load_session can
            # detect stale overwrites from the async writer.
            self._save_seq += 1
            self.session_data["_save_seq"] = self._save_seq
            logger.info(
                f"[SessionManager] _save_session: sid={sid}, messages={msg_count}, events={evt_count}, save_seq={self._save_seq}, file={self.current_session_file}"
            )
            tmp_path = self.current_session_file + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.session_data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.current_session_file)
            self._last_save_time = time.monotonic()
            # Synchronize cache with the latest state of the current session
            sid = self.session_data.get("id")
            if sid:
                self._cache_put(sid, self.session_data)
        except Exception as e:
            logger.error(f"[SessionManager] Failed to save session: {e}")

    def update_last_message_elapsed_ms(self, elapsed_ms: int):
        """Write elapsed_ms (total workflow time) to the last assistant message."""

        def _mutate():
            messages = self.session_data.get("messages", [])
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "assistant":
                    messages[i]["elapsed_ms"] = elapsed_ms
                    return

        self._enqueue_mutation(_mutate)

    def mark_last_assistant_end_task(self):
        """Mark the latest assistant message as a complex-task end report (for UI fold)."""

        def _mutate():
            messages = self.session_data.get("messages", [])
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "assistant":
                    messages[i]["end_task"] = True
                    return

        self._enqueue_mutation(_mutate)

    def get_messages(self, limit: int | None = None) -> list[dict]:
        messages = self.session_data["messages"]
        if limit:
            return messages[-limit:]
        return messages

    def get_messages_for_chat_api(self, limit: int = 50) -> list[dict]:
        messages = self.get_messages(limit)
        result = []
        _ui_only_keys = frozenset(
            {
                "type",
                "timestamp",
                "elapsed_ms",
                "images",
                "attachments",
                "output_audio",
                "output_images",
                "preview",
                "msg_type",
                "end_task",
            }
        )
        for m in messages:
            api_msg = {}
            for k, v in m.items():
                if k not in _ui_only_keys:
                    api_msg[k] = v
            result.append(api_msg)
        return result

    def get_events(self, limit: int | None = None) -> list[dict]:
        events = self.session_data.get("events", [])
        if limit:
            return events[-limit:]
        return events

    def flush(self):
        """Drain all pending async mutations and sync the latest state to disk.

        Call this before reading session data from another process/thread
        (e.g. Gateway) to guarantee the file reflects all in-memory state.
        """
        self._drain_pending_mutations_sync()
        self._save_session()

    def get_current_session_id(self) -> str:
        return self.session_data.get("id", "unknown")

    def set_title(self, title: str):
        if not title:
            return

        def _mutate():
            # User-renamed titles stay sticky until unlocked.
            if self.session_data.get("title_locked"):
                return
            self.session_data["title"] = title
            self.session_data["last_updated"] = utc_now_iso()

        self._enqueue_mutation(_mutate)

    def get_title(self) -> str | None:
        return self.session_data.get("title")

    @staticmethod
    def _title_from_user_content(content: str) -> str:
        """Normalize user message text into a short session title."""
        if not content:
            return ""
        text = re.sub(r"<image>.*?</image>", "[image]", content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"\[File:[^\]]*\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:80]

    @classmethod
    def resolve_session_title(cls, messages: list, explicit_title: str | None, fallback: str) -> str:
        """Resolve display title: explicit → <title> tag → first user message → fallback id."""
        if explicit_title and str(explicit_title).strip():
            return str(explicit_title).strip()
        for m in messages or []:
            if m.get("role") == "assistant":
                match = re.search(r"<title>(.*?)</title>", m.get("content", "") or "", re.DOTALL)
                if match:
                    t = match.group(1).strip()
                    if t:
                        return t
        for m in messages or []:
            if m.get("role") == "user":
                t = cls._title_from_user_content(m.get("content", "") or "")
                if t:
                    return t
        return fallback

    def clear(self):
        self._init_new_session()
        # clear() is user-initiated; sync flush to guarantee immediate persistence
        self._save_session()

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_messages": len(self.session_data["messages"]),
            "total_archived_messages": len(self.session_data.get("archived_messages") or []),
            "total_archived_events": len(self.session_data.get("archived_events") or []),
            "created_at": self.session_data["created_at"],
            "last_updated": self.session_data["last_updated"],
        }

    def get_session_list(self) -> list[dict[str, str]]:
        """Get all session list."""
        history_dir = self.history_dir
        sessions = []
        seen_ids = set()

        def _extract_preview(messages: list) -> str:
            """Extract the last user message as preview."""
            for m in reversed(messages):
                if m.get("role") == "user":
                    content = m.get("content", "").strip()
                    if content:
                        # Remove image markers and other special content
                        content = re.sub(r"<image>.*?</image>", "[image]", content)
                        return content[:80]  # Limit to 80 characters
            return ""

        # 1. Current session
        curr_id = self.session_data.get("id")
        if curr_id:
            messages = self.session_data.get("messages", [])
            title = self.resolve_session_title(messages, self.session_data.get("title"), curr_id)
            preview = _extract_preview(messages)
            sessions.append(
                {
                    "id": curr_id,
                    "title": title,
                    "preview": preview,
                    "current": True,
                    "created_at": self.session_data.get("created_at"),
                    "last_updated": self.session_data.get("last_updated"),
                }
            )
            seen_ids.add(curr_id)

        # 2. History files
        if os.path.exists(history_dir):
            try:
                files = [f for f in os.listdir(history_dir) if f.endswith(".json")]
                files.sort(key=lambda x: os.path.getmtime(os.path.join(history_dir, x)), reverse=True)
                for f in files:
                    sid = f.replace(".json", "")
                    if sid in seen_ids:
                        continue
                    title = sid
                    preview = ""
                    created_at = None
                    last_updated = None
                    # Prefer reading title from cache (with mtime validation)
                    cached = self._cache_get(sid)
                    if cached is not None:
                        messages = cached.get("messages", [])
                        title = self.resolve_session_title(messages, cached.get("title"), sid)
                        preview = _extract_preview(messages)
                        created_at = cached.get("created_at")
                        last_updated = cached.get("last_updated")
                    else:
                        try:
                            with open(os.path.join(history_dir, f), encoding="utf-8") as jf:
                                content = jf.read()
                                try:
                                    parsed = json.loads(content)
                                    messages = parsed.get("messages", []) or []
                                    title = self.resolve_session_title(messages, parsed.get("title"), sid)
                                    preview = _extract_preview(messages)
                                    if isinstance(parsed, dict):
                                        created_at = parsed.get("created_at")
                                        last_updated = parsed.get("last_updated")
                                except Exception:
                                    match = re.search(r"<title>(.*?)</title>", content, re.DOTALL)
                                    if match:
                                        title = match.group(1).strip() or sid
                        except Exception:
                            pass
                    sessions.append(
                        {
                            "id": sid,
                            "title": title,
                            "preview": preview,
                            "current": False,
                            "created_at": created_at,
                            "last_updated": last_updated,
                        }
                    )
                    seen_ids.add(sid)
            except Exception as e:
                logger.error(f"Error scanning history: {e}")
        return sessions

    def register_lazy_session(self, temp_id: str, real_id: str):
        """Register a lazy session mapping - will be resolved when first message is sent"""
        logger.info(f"[SessionManager] Registered lazy session: {temp_id} -> {real_id}")
        # Store the real_id to use when creating the actual session
        if not hasattr(self, "_lazy_sessions"):
            self._lazy_sessions = {}
        self._lazy_sessions[temp_id] = real_id

    def delete_session(self, session_id: str) -> bool:
        self._cache_remove(session_id)
        history_dir = self.history_dir
        file_path = os.path.join(history_dir, f"{session_id}.json")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                return True
            except Exception:
                return False
        return False


# Global singleton
session_manager = SessionManager()


def reinit_session_manager(save_dir: str, history_dir: str | None = None):
    """Re-initialize the global singleton pointing to a new storage directory (multi-agent isolation)."""
    global session_manager
    # If history_dir is not specified, create a history directory alongside save_dir
    if history_dir is None:
        history_dir = os.path.join(os.path.dirname(save_dir), "history")
    os.makedirs(history_dir, exist_ok=True)
    session_manager = SessionManager(save_dir=save_dir, history_dir=history_dir)
    logger.info(f"[SessionManager] Reinitialized with save_dir: {save_dir}, history_dir: {history_dir}")
    return session_manager


def get_session_manager() -> SessionManager:
    return session_manager
