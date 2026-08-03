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
    - Maintains a focused session (current_session.json) plus optional live
      in-memory sessions for parallel turns; primary_session_id routes external ingress.
    """

    def __init__(self, save_dir: str | None = None, history_dir: str | None = None, cache_size: int = 10):
        # -- Phase 2.2: Use workspace path --
        from opensquad.system_config import syscfg

        self.save_dir = save_dir or syscfg.workspace_sessions_dir()
        self.history_dir = history_dir or syscfg.workspace_data_dir("ai_his_talk")
        self.current_session_file = os.path.join(self.save_dir, "current_session.json")
        self.primary_session_file = os.path.join(self.save_dir, "primary_session.json")
        self._lock = threading.Lock()
        # Bumped on truncate / new_session so in-flight async mutations are skipped.
        self._mutation_gen = 0
        # Per-sid generation for parallel non-focused writes
        self._mutation_gens: dict[str, int] = {}
        # Live in-memory sessions (sid -> data). Focused session is always included.
        self._live_sessions: dict[str, dict] = {}
        self._primary_session_id: str | None = None
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
        # Parallel (non-focused) sessions whose live dicts were mutated by the
        # async writer but have no snapshot coverage — flushed to disk after
        # each batch (P0-5 fix: parallel-session messages never persisted).
        self._dirty_parallel_sids: set[str] = set()

        # Save sequence number: monotonically increasing counter written to disk
        # with every _save_session().  Prevents the async writer from overwriting
        # a newer session_data with stale data.  Initialized from disk on load.
        self._save_seq: int = 0

        # ---- Incremental log + throttled snapshot (write-amplification fix) ----
        # Mutations append O(1) seq-tagged records to history/{sid}.json.log;
        # a full snapshot (current_session.json + history/{sid}.json) is only
        # written every _snapshot_interval_sec or _snapshot_max_records records
        # (~60x fewer full rewrites on large sessions). Crash recovery =
        # snapshot + replay of records with seq > snapshot seq. Both live in
        # this process only; the Gateway has its own mirrored reader.
        self._snapshot_interval_sec = 30.0
        self._snapshot_max_records = 200
        self._last_snapshot_mono = 0.0
        self._log_records_since_snapshot = 0
        # Each start_async_writer() era starts with one fresh snapshot so
        # cross-process readers see recent state immediately, then throttles.
        self._writer_era_snapshotted = True

        # Ensure directories exist
        os.makedirs(self.save_dir, exist_ok=True)
        os.makedirs(self.history_dir, exist_ok=True)

        # Load existing session
        self._load_session()
        self._register_live(self.session_data)
        self._load_or_init_primary()

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

    # ---- Multi-session live store + primary ----

    def _register_live(self, data: dict | None) -> None:
        if not isinstance(data, dict):
            return
        sid = data.get("id")
        if sid:
            self._live_sessions[sid] = data

    def _load_or_init_primary(self) -> None:
        """Load primary_session_id from disk, or default to earliest created session."""
        primary = None
        if os.path.isfile(self.primary_session_file):
            try:
                with open(self.primary_session_file, encoding="utf-8") as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    primary = str(meta.get("primary_session_id") or "").strip() or None
            except Exception as e:
                logger.warning("[SessionManager] Failed to read primary_session.json: %s", e)

        if primary and (primary in self._live_sessions or self._history_exists(primary)):
            self._primary_session_id = primary
            return

        # Default: earliest created_at among focused + history
        earliest_sid = self._find_earliest_session_id()
        focused = self.session_data.get("id")
        self._primary_session_id = earliest_sid or focused
        if self._primary_session_id:
            self._persist_primary()

    def _history_exists(self, sid: str) -> bool:
        return os.path.isfile(os.path.join(self.history_dir, f"{sid}.json"))

    def _find_earliest_session_id(self) -> str | None:
        candidates: list[tuple[str, str]] = []
        focused = self.session_data
        fid = focused.get("id")
        if fid:
            candidates.append((str(focused.get("created_at") or ""), fid))
        if os.path.isdir(self.history_dir):
            try:
                for name in os.listdir(self.history_dir):
                    if not name.endswith(".json"):
                        continue
                    sid = name[:-5]
                    created = ""
                    try:
                        with open(os.path.join(self.history_dir, name), encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, dict):
                            created = str(data.get("created_at") or "")
                            sid = str(data.get("id") or sid)
                    except Exception:
                        pass
                    candidates.append((created, sid))
            except OSError:
                pass
        if not candidates:
            return None
        # Empty created_at sorts first; prefer real timestamps then id
        candidates.sort(key=lambda x: (x[0] or "9999", x[1]))
        # Prefer entries with a real created_at when any exist
        with_ts = [c for c in candidates if c[0]]
        pool = with_ts if with_ts else candidates
        pool.sort(key=lambda x: (x[0], x[1]))
        return pool[0][1]

    def _persist_primary(self) -> None:
        try:
            os.makedirs(self.save_dir, exist_ok=True)
            tmp = self.primary_session_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"primary_session_id": self._primary_session_id}, f, indent=2)
            os.replace(tmp, self.primary_session_file)
        except Exception as e:
            logger.warning("[SessionManager] Failed to persist primary: %s", e)

    def get_primary_session_id(self) -> str:
        sid = (self._primary_session_id or "").strip()
        # Fast path: cached primary is still loadable (current / live / history).
        if sid and (sid == self.session_data.get("id") or sid in self._live_sessions or self._history_exists(sid)):
            return sid
        # Cached primary is a phantom (deleted/archived session) — re-resolve to a
        # real loadable session so external ingress (scheduled tasks, reminders,
        # chatpro, …) doesn't bind to a session the history reader can't find.
        self._load_primary()
        return self._primary_session_id or ""

    def set_primary_session_id(self, sid: str) -> bool:
        sid = (sid or "").strip()
        if not sid:
            return False
        if sid != self.session_data.get("id") and not self._history_exists(sid) and sid not in self._live_sessions:
            # Allow setting to a known live or history session only
            hist = self.get_session_history(sid)
            if hist is None:
                logger.warning("[SessionManager] set_primary: unknown sid=%s", sid)
                return False
        self._primary_session_id = sid
        self._persist_primary()
        logger.info("[SessionManager] Primary session set to %s", sid)
        return True

    def get_focused_session_id(self) -> str:
        return self.get_current_session_id()

    def ensure_session_loaded(self, sid: str) -> dict | None:
        """Ensure sid is in the live map (load from disk if needed). Does not change focus."""
        if not sid:
            return None
        if sid == self.session_data.get("id"):
            self._register_live(self.session_data)
            return self.session_data
        if sid in self._live_sessions:
            return self._live_sessions[sid]
        data = self.get_session_history(sid)
        if data is None:
            return None
        # Keep a mutable live copy
        live = copy.deepcopy(data)
        self._live_sessions[sid] = live
        return live

    def _resolve_session_data(self, sid: str | None = None) -> dict:
        if not sid:
            try:
                from opensquad.session_parallel import get_turn_local

                tl = get_turn_local()
                if tl and tl.sid:
                    sid = tl.sid
            except Exception:
                pass
        if not sid or sid == self.session_data.get("id"):
            return self.session_data
        loaded = self.ensure_session_loaded(sid)
        if loaded is not None:
            return loaded
        logger.warning("[SessionManager] _resolve_session_data fallback to focused for sid=%s", sid)
        return self.session_data

    def _save_session_data(self, data: dict, *, as_focused: bool | None = None) -> None:
        """Persist a session dict. Focused → current_session.json; always mirror to history/{sid}.json when id set."""
        sid = data.get("id")
        is_focused = as_focused if as_focused is not None else (sid == self.session_data.get("id"))
        try:
            # A full snapshot supersedes every incremental log record — never
            # regress below a seq already recorded in data (e.g. data replayed
            # from a log written by an earlier process run).
            data_seq = data.get("_save_seq") or 0
            if isinstance(data_seq, (int, float)):
                self._save_seq = max(self._save_seq, int(data_seq))
            self._save_seq += 1
            data["_save_seq"] = self._save_seq
            if is_focused:
                # Adopt disk title lock only for focused file
                if data is self.session_data:
                    self._adopt_disk_title_lock()
                tmp_path = self.current_session_file + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, self.current_session_file)
            if sid:
                os.makedirs(self.history_dir, exist_ok=True)
                hist_path = os.path.join(self.history_dir, f"{sid}.json")
                tmp_h = hist_path + ".tmp"
                with open(tmp_h, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_h, hist_path)
                self._cache_put(sid, data)
                self._live_sessions[sid] = data
                # Snapshot supersedes the log — reset it so replay starts clean.
                self._truncate_log(sid)
            self._last_save_time = time.monotonic()
            self._last_snapshot_mono = time.monotonic()
        except Exception as e:
            logger.error("[SessionManager] Failed to save session data: %s", e)

    # ---- Incremental log (append-only) + replay ----

    def _log_path_for(self, sid: str) -> str:
        return os.path.join(self.history_dir, f"{sid}.json.log")

    def _append_log_record(self, sid: str, record: dict) -> None:
        """Append one incremental record to ``history/{sid}.json.log`` (O(1)).

        Called from inside mutation closures, which always run under
        ``self._lock`` (async writer batch / sync fallback / drain), so the
        seq counter stays in sync with snapshot writes. Each record takes the
        next ``_save_seq``; a later snapshot (seq S) supersedes every record
        with seq <= S, so a crash between snapshot write and log truncate
        replays nothing twice. A corrupted tail line (partial append) is
        skipped on replay.
        """
        if not sid:
            return
        self._save_seq += 1
        self._log_records_since_snapshot += 1
        # The cached copy is stale the moment a log record lands (log mtime is
        # not part of the cache staleness check) — pop it so the next read
        # merges from disk.
        try:
            self._cache_remove(sid)
        except Exception:
            pass
        record = {"seq": self._save_seq, "sid": sid, **record}
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            os.makedirs(self.history_dir, exist_ok=True)
            with open(self._log_path_for(sid), "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.warning("[SessionManager] Log append failed (sid=%s): %s", sid, e)

    def _truncate_log(self, sid: str) -> None:
        """Reset ``{sid}.json.log`` — the just-written snapshot supersedes it."""
        if not sid:
            return
        self._log_records_since_snapshot = 0
        try:
            path = self._log_path_for(sid)
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logger.debug("[SessionManager] Log truncate failed (sid=%s): %s", sid, e)

    def _replay_log_into(self, data: dict, sid: str, base_seq: int) -> tuple[int, int]:
        """Replay incremental log records with ``seq > base_seq`` into ``data``.

        Mutates ``data`` in place (must be a working copy, not a shared
        reference) and applies the same semantics as the live mutation
        closures (append + cap trimming + draft promotion + provisional
        title). Returns (max replayed seq, applied count). Corrupt lines
        (partial appends from a crash) are dropped with a warning; the
        remaining records still apply.
        """
        if not sid:
            return 0, 0
        path = self._log_path_for(sid)
        if not os.path.exists(path):
            return 0, 0
        max_seq = 0
        count = 0
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        logger.warning("[SessionManager] Dropping corrupt log line (sid=%s): %s", sid, line[:120])
                        continue
                    seq = rec.get("seq")
                    if not isinstance(seq, int) or seq <= base_seq:
                        continue
                    if seq > max_seq:
                        max_seq = seq
                    op = rec.get("op")
                    if op == "msg_append":
                        msg = rec.get("msg")
                        if isinstance(msg, dict):
                            data.setdefault("messages", []).append(msg)
                            if len(data["messages"]) > 1000:
                                data["messages"] = data["messages"][-1000:]
                            if rec.get("draft") is not None:
                                data["draft"] = rec["draft"]
                            if rec.get("title") and not data.get("title_locked"):
                                data["title"] = rec["title"]
                            data["last_updated"] = rec.get("ts") or utc_now_iso()
                            count += 1
                    elif op == "evt_append":
                        evt = rec.get("evt")
                        if isinstance(evt, dict):
                            data.setdefault("events", []).append(evt)
                            if len(data["events"]) > 2000:
                                data["events"] = data["events"][-2000:]
                            data["last_updated"] = rec.get("ts") or utc_now_iso()
                            count += 1
                    elif op == "tail_patch":
                        patches = rec.get("patches")
                        if isinstance(patches, dict):
                            messages = data.get("messages") or []
                            for i in range(len(messages) - 1, -1, -1):
                                if messages[i].get("role") == "assistant":
                                    messages[i].update(patches)
                                    count += 1
                                    break
                    elif op == "meta":
                        fields = rec.get("fields")
                        if isinstance(fields, dict):
                            if "title" in fields and not data.get("title_locked"):
                                data["title"] = fields["title"]
                            if "last_updated" in fields:
                                data["last_updated"] = fields["last_updated"]
                            count += 1
                    else:
                        logger.debug("[SessionManager] Unknown log op skipped (sid=%s): %s", sid, op)
            # Reconstructed state supersedes the replayed records — a later
            # snapshot (via max-insurance in _save_session_data) cannot
            # regress below them.
            data["_save_seq"] = max(int(data.get("_save_seq") or 0), max_seq)
        except Exception as e:
            logger.warning("[SessionManager] Log replay failed (sid=%s): %s", sid, e)
        return max_seq, count

    def _maybe_snapshot(self) -> None:
        """Throttled full-snapshot decision; must be called under ``self._lock``.

        A snapshot runs when the current writer era has not yet written one,
        or when the interval / record-count budget is exhausted. Between
        snapshots the incremental log carries the durable state.
        """
        now = time.monotonic()
        if (
            not self._writer_era_snapshotted
            or self._log_records_since_snapshot >= self._snapshot_max_records
            or (now - self._last_snapshot_mono) >= self._snapshot_interval_sec
        ):
            self._writer_era_snapshotted = True
            self._save_session()

    def _archive_snapshot(self, data: dict) -> None:
        """Write a full history snapshot of ``data`` (archive paths).

        Archive writes are rare user-driven events — they always supersede
        the incremental log so the archived file is a complete, standalone
        snapshot (must be called under ``self._lock``).
        """
        sid = data.get("id")
        if not sid:
            return
        data_seq = data.get("_save_seq") or 0
        if isinstance(data_seq, (int, float)):
            self._save_seq = max(self._save_seq, int(data_seq))
        self._save_seq += 1
        data["_save_seq"] = self._save_seq
        try:
            os.makedirs(self.history_dir, exist_ok=True)
            file_path = os.path.join(self.history_dir, f"{sid}.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._cache_put(sid, data)
            self._truncate_log(sid)
            logger.info(f"[SessionManager] Archived session: {sid}")
        except Exception as e:
            logger.error(f"[SessionManager] Failed to archive session: {e}")

    def _enqueue_mutation_for(self, mutation: callable, sid: str | None = None):
        """Enqueue mutation targeting focused or a specific live session."""
        target_sid = sid or self.session_data.get("id")
        focused_id = self.session_data.get("id")
        is_focused = not target_sid or target_sid == focused_id

        if is_focused:
            gen = self._mutation_gen

            def _guarded():
                if self._mutation_gen != gen:
                    logger.debug(
                        "[SessionManager] Skipping stale focused mutation (gen %s -> %s)",
                        gen,
                        self._mutation_gen,
                    )
                    return
                mutation()
        else:
            # Per-sid generation; default 0 so first writes are not skipped after focus changes
            if target_sid not in self._mutation_gens:
                self._mutation_gens[target_sid] = 0
            gen = self._mutation_gens[target_sid]

            def _guarded():
                if self._mutation_gens.get(target_sid, 0) != gen:
                    logger.debug(
                        "[SessionManager] Skipping stale sid mutation sid=%s (gen %s)",
                        target_sid,
                        gen,
                    )
                    return
                mutation()
                # P0-5: the writer snapshot only covers the focused session;
                # mark parallel sessions dirty so the flush persists them too.
                self._dirty_parallel_sids.add(target_sid)

        if self._writer_running and self._write_queue is not None:
            _wt = self._writer_task
            if _wt is not None and _wt.done():
                # Writer task died before the self-heal could restart it
                # (e.g. cancelled mid-storm) — degrade to synchronous writes
                # so mutations still land on disk instead of queueing forever.
                logger.warning("[SessionManager] Async writer task dead — falling back to sync writes")
                self._writer_running = False
                self._writer_task = None
            else:
                try:
                    self._write_queue.put_nowait(_guarded)
                    return
                except queue.Full:
                    pass
        with self._lock:
            _guarded()
            data = self._resolve_session_data(sid)
            self._save_session_data(data)

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
        # Fresh writer era: force one snapshot on the first flush so readers
        # see recent state, then throttle to _maybe_snapshot()'s budget.
        self._writer_era_snapshotted = False
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
        """Background consumer: batches queued writes and flushes to disk.

        Resilience: boot-time anyio/MCP cancel storms (Python 3.12) leak
        CancelledError into sibling tasks. A dead writer would silently queue
        mutations forever (no disk writes, no errors, and no sync fallback
        while ``_writer_running`` stays True), so the loop task re-creates
        itself on a stray cancellation instead of dying. This mirrors the
        SDK/runner "die + restart" pattern: the OLD task ends (so a caller
        that cancels-and-awaits it — e.g. pytest-asyncio loop teardown —
        completes), while the fresh task continues draining the queue.
        """
        while True:
            try:
                await self._async_save_loop_inner()
                return  # inner loop exited on shutdown (final drain done)
            except asyncio.CancelledError:
                if not self._writer_running:
                    raise
                logger.warning("[SessionManager] Async writer cancelled (boot storm) — restarting")
                self._writer_task = asyncio.get_running_loop().create_task(self._async_save_loop())
                return
            except Exception as e:
                if not self._writer_running:
                    raise
                logger.error("[SessionManager] Async writer crashed (%s) — restarting", e)
                self._writer_task = asyncio.get_running_loop().create_task(self._async_save_loop())
                return

    async def _async_save_loop_inner(self):
        """The actual flush loop (wrapped by _async_save_loop for resilience)."""
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

                def _flush_batch_to_disk():
                    # Apply + save under the same lock as truncate/new_session so a
                    # withdraw cannot cut the session while this batch still holds
                    # dequeued (but unapplied) appends that would resurrect messages.
                    with self._lock:
                        for mutation in batch:
                            try:
                                mutation()
                            except Exception as e:
                                logger.warning("[SessionManager] Async mutation failed: %s", e)
                        # Throttled snapshot: mutations already appended O(1)
                        # incremental records, so a full rewrite only runs every
                        # _snapshot_interval_sec / _snapshot_max_records records.
                        self._maybe_snapshot()
                        # P0-5: persist parallel (non-focused) sessions touched by
                        # this batch — they are not covered by _maybe_snapshot().
                        if self._dirty_parallel_sids:
                            for _dsid in list(self._dirty_parallel_sids):
                                try:
                                    _ddata = self._live_sessions.get(_dsid)
                                    if _ddata is None:
                                        _ddata = self._resolve_session_data(_dsid)
                                    if _ddata is not None and _ddata is not self.session_data:
                                        self._save_session_data(_ddata)
                                except Exception as _de:
                                    logger.warning(
                                        f"[SessionManager] parallel session persist failed sid={_dsid}: {_de}"
                                    )
                            self._dirty_parallel_sids.clear()

                # JSON serialization + disk writes are synchronous and can take
                # seconds on huge sessions. Run them on a worker thread so the
                # event loop (and thus the gateway WS keepalive / heartbeats)
                # never stalls — otherwise agent shows "重连中" mid-turn.
                await asyncio.to_thread(_flush_batch_to_disk)

                # Mark idle after flush is complete
                if self._writer_idle_event:
                    self._writer_idle_event.set()
                logger.debug(
                    f"[SessionManager] Async flush: {len(batch)} mutation(s), "
                    f"log_records_since_snapshot={self._log_records_since_snapshot}"
                )

        # Final drain on shutdown
        final_batch = []
        while True:
            try:
                final_batch.append(self._write_queue.get_nowait())
            except queue.Empty:
                break
        if final_batch or self._log_records_since_snapshot > 0:

            def _final_drain_to_disk():
                with self._lock:
                    for mutation in final_batch:
                        try:
                            mutation()
                        except Exception as e:
                            logger.warning("[SessionManager] Final mutation failed: %s", e)
                    # Guarantee the last state lands as a snapshot on shutdown
                    # (only needed when the log has records beyond the last one).
                    if self._log_records_since_snapshot > 0:
                        self._save_session()

            await asyncio.to_thread(_final_drain_to_disk)
            if self._writer_idle_event:
                self._writer_idle_event.set()
            logger.info(f"[SessionManager] Final drain: {len(final_batch)} mutation(s)")

    def _enqueue_mutation(self, mutation: callable):
        """Enqueue a lightweight mutation closure if the async writer is active;
        otherwise fall back to synchronous _save_session().

        Each mutation captures ``_mutation_gen`` at enqueue time. After
        truncate / new_session bumps the generation, stale closures no-op so
        they cannot re-append withdrawn messages.
        """
        gen = self._mutation_gen

        def _guarded():
            if self._mutation_gen != gen:
                logger.debug(
                    "[SessionManager] Skipping stale mutation (gen %s -> %s)",
                    gen,
                    self._mutation_gen,
                )
                return
            mutation()

        if self._writer_running and self._write_queue is not None:
            _wt = self._writer_task
            if _wt is not None and _wt.done():
                # Writer task died before the self-heal could restart it
                # (e.g. cancelled mid-storm) — degrade to synchronous writes
                # so mutations still land on disk instead of queueing forever.
                logger.warning("[SessionManager] Async writer task dead — falling back to sync writes")
                self._writer_running = False
                self._writer_task = None
            else:
                try:
                    self._write_queue.put_nowait(_guarded)
                    return
                except queue.Full:
                    pass  # Fallback to sync
        # Synchronous fallback (boot phase or queue overflow)
        with self._lock:
            _guarded()
            self._save_session()

    def add_event(
        self,
        event_type: str,
        event_data: dict,
        turn_id: int | None = None,
        round_id: int | None = None,
        *,
        sid: str | None = None,
    ):
        """Add an interaction event to history.

        Non-critical events are enqueued for async batch flush.
        tool_call / tool_result events still flush synchronously to guarantee
        crash-recoverable state before tool execution.
        Optional sid= writes to a live (possibly non-focused) session for parallel turns.
        """
        target = self._resolve_session_data(sid)

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
            target.setdefault("events", []).append(event)
            target["last_updated"] = utc_now_iso()
            if len(target["events"]) > 2000:
                target["events"] = target["events"][-2000:]
            self._append_log_record(
                target.get("id") or sid or self.session_data.get("id"),
                {"op": "evt_append", "evt": event, "ts": event["timestamp"]},
            )

        # tool_call / tool_result previously flushed synchronously to guarantee
        # crash-recoverable state before tool execution. But that JSON+disk
        # write blocks the event loop for seconds on huge sessions → agent WS
        # keepalive times out → gateway flips offline → UI 重连中. The async
        # writer flushes every 0.5s (crash window is tiny), so route everything
        # through it; the sync fallback below only triggers pre-writer boot.
        self._enqueue_mutation_for(_mutate, sid=sid or target.get("id"))
        self._last_save_time = time.monotonic()

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
                # Replay incremental log records written after the last snapshot
                # so a crash loses at most the in-flight writer batch.
                _max_seq, _replayed = self._replay_log_into(
                    self.session_data,
                    self.session_data.get("id"),
                    self._save_seq,
                )
                self._save_seq = max(self._save_seq, _max_seq)
                self._log_records_since_snapshot = _replayed

                if self._is_reusable_draft(self.session_data):
                    # Keep the empty draft as the New Session cache — do not
                    # bounce to latest history (that undoes New Session).
                    self.session_data["draft"] = True
                    loaded = True
                    logger.info(
                        "[SessionManager] Loaded empty draft session: %s",
                        self.session_data.get("id"),
                    )
                    self._sync_goal_state()
                elif self.session_data.get("messages"):
                    loaded = True
                    logger.info(
                        f"[SessionManager] Loaded session with {len(self.session_data['messages'])} messages and {len(self.session_data.get('events', []))} events"
                    )
                    self._sync_goal_state()
                else:
                    logger.info("[SessionManager] Current session is empty, trying to load latest history")
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
            # Empty shells are drafts: hidden from the sidebar and reused on
            # subsequent New Session clicks until the user sends a message.
            "draft": True,
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
        self._sync_goal_state()

    def _sync_goal_state(self) -> None:
        """Keep goal_mode in-memory state aligned with session_data['goal']."""
        try:
            from opensquad.goal_mode import clear_goal_memory, load_goal_from_session

            if self.session_data.get("goal"):
                load_goal_from_session(self.session_data)
            else:
                clear_goal_memory()
        except Exception as e:
            logger.debug("[SessionManager] goal sync skipped: %s", e)

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

            # Merge incremental log records newer than the snapshot. The log
            # (not the json mtime) invalidates the LRU entry via cache-pop in
            # _append_log_record, so a read-only disk hit stays correct.
            _max_seq, _replayed = self._replay_log_into(data, session_id, int(data.get("_save_seq") or 0))
            if _replayed and _max_seq > self._save_seq:
                self._save_seq = max(self._save_seq, _max_seq)

            # Put into cache
            self._cache_put(session_id, data)
            return data
        except Exception as e:
            logger.error(f"[SessionManager] Failed to read history session: {e}")
            return None

    def load_history_session(self, session_id: str) -> bool:
        """Load a specific history session as the focused session.

        Other live in-memory sessions are retained so parallel turns on other
        sids are not discarded when focus changes.
        """
        # If already the current session, return True immediately
        if self.session_data.get("id") == session_id:
            return True

        # Archive the current session before switching (if it has content)
        self.archive_current_session()

        # Prefer live in-memory copy (may have newer parallel-turn writes)
        if session_id in self._live_sessions:
            logger.info(f"[SessionManager] Live hit (switch): {session_id}")
            self.session_data = self._live_sessions[session_id]
            self._save_session()
            self._sync_goal_state()
            return True

        # 1. Check cache first
        cached = self._cache_get(session_id)
        if cached is not None:
            logger.info(f"[SessionManager] Cache hit (switch): {session_id}")
            self.session_data = cached
            self._register_live(self.session_data)
            self._save_session()
            self._sync_goal_state()
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

            # Merge incremental log records newer than the snapshot; the
            # subsequent _save_session() supersedes them with a fresh snapshot.
            _max_seq, _replayed = self._replay_log_into(
                self.session_data, session_id, int(self.session_data.get("_save_seq") or 0)
            )
            if _replayed and _max_seq > self._save_seq:
                self._save_seq = max(self._save_seq, _max_seq)

            # Put into cache
            self._cache_put(session_id, self.session_data)
            self._register_live(self.session_data)

            self._save_session()
            logger.info(f"[SessionManager] Loaded history session (disk): {session_id}")
            self._sync_goal_state()
            return True
        except Exception as e:
            logger.error(f"[SessionManager] Failed to load history session: {e}")
            return False

    def archive_current_session(self):
        """Archive the current session to history."""
        if not self.session_data.get("messages"):
            return

        sid = self.session_data.get("id")
        if not sid:
            sid = self._generate_id()
            self.session_data["id"] = sid

        # Archive = rare user-driven event: full snapshot that supersedes the
        # incremental log (seq bump + truncate inside _archive_snapshot).
        with self._lock:
            self._archive_snapshot(self.session_data)

    def start_new_session(self) -> bool:
        """Start a new session (automatically archives the old one).

        Returns True when a new sid was created, False when the existing empty
        draft was reused (New Session click with no user input yet).

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
            # Reuse the empty draft — do not mint another sid or archive a shell.
            if self._is_reusable_draft(self.session_data):
                if not self.session_data.get("draft"):
                    self.session_data["draft"] = True
                    try:
                        self._save_session()
                    except Exception:
                        logger.debug("[SessionManager] draft flag persist skipped", exc_info=True)
                logger.info(
                    "[SessionManager] Reusing empty draft session: %s",
                    self.session_data.get("id"),
                )
                return False

            self._mutation_gen += 1
            # Snapshot old session before overwrite — including empty sessions so
            # they enter history/ and remain deletable after the user switches away.
            old_data = copy.deepcopy(self.session_data) if self.session_data.get("id") else None
            # 1. Write new empty session to current_session.json FIRST
            self._init_new_session()
            self._register_live(self.session_data)
            # New sessions are not automatically primary; keep existing primary pointer.
            # 2. Archive old session to history/ SECOND (non-critical for crash recovery)
            if old_data:
                sid = old_data.get("id")
                if sid and self._is_reusable_draft(old_data):
                    # Never keep never-sent shells in history/ or the sidebar.
                    self._live_sessions.pop(sid, None)
                    try:
                        self._cache_remove(sid)
                    except Exception:
                        pass
                    history_path = os.path.join(self.history_dir, f"{sid}.json")
                    log_path = self._log_path_for(sid)
                    try:
                        if os.path.isfile(history_path):
                            os.remove(history_path)
                        if os.path.isfile(log_path):
                            os.remove(log_path)
                    except Exception as e:
                        logger.debug("[SessionManager] Failed to drop empty draft %s: %s", sid, e)
                elif sid:
                    self._live_sessions[sid] = old_data
                    self._archive_snapshot(old_data)
            return True

    def create_parallel_session(self, title: str | None = None, origin: str | None = None) -> str:
        """Create a brand-new empty session WITHOUT changing focused or primary.

        Used by scheduled-task / delegation fires so each execution gets its own
        parallel pane (true multi-session) while the user's interactive web chat
        keeps its focused session. The new session is registered in
        ``_live_sessions`` and mirrored to ``history/{sid}.json`` so the frontend
        can load it by id immediately.

        ``origin`` (e.g. ``"scheduled_task"``) is persisted so session lists can
        hide non-interactive sessions while they remain loadable by id.
        """
        with self._lock:
            now_iso = utc_now_iso()
            sid = self._generate_id()
            data = {
                "id": sid,
                "title": (title or "").strip() or None,
                "messages": [],
                "events": [],
                "archived_messages": [],
                "archived_events": [],
                "latest_summary": "",
                "last_updated": now_iso,
                "created_at": now_iso,
            }
            origin_s = (origin or "").strip()
            if origin_s:
                data["origin"] = origin_s
            self._live_sessions[sid] = data
            self._save_session_data(data, as_focused=False)
            logger.info(
                "[SessionManager] Created parallel session: %s title=%s origin=%s (focused unchanged=%s)",
                sid,
                data.get("title") or "-",
                origin_s or "-",
                self.session_data.get("id") or "-",
            )
            return sid

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
        *,
        sid: str | None = None,
        **extra_fields,
    ):
        # Allow legacy callers that passed sid via kwargs
        if sid is None and "sid" in extra_fields:
            sid = extra_fields.pop("sid", None)
        logger.info(
            f"[SessionManager] add_message: role={role}, content_len={len(content)}, "
            f"content_preview={content[:80]}, sid={sid or 'focused'}"
        )
        target = self._resolve_session_data(sid)

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

            target.setdefault("messages", []).append(message)
            target["last_updated"] = utc_now_iso()
            draft_promoted = False
            provisional_title = None
            if role == "user":
                # First real user input promotes the draft into the normal session list.
                if target.get("draft"):
                    target["draft"] = False
                    draft_promoted = True
                if not target.get("title") and not target.get("title_locked"):
                    provisional = self._title_from_user_content(content)
                    if provisional:
                        target["title"] = provisional
                        provisional_title = provisional
            if len(target["messages"]) > 1000:
                target["messages"] = target["messages"][-1000:]
            record: dict = {
                "op": "msg_append",
                "msg": message,
                "ts": message["timestamp"],
            }
            if draft_promoted:
                record["draft"] = False
            if provisional_title:
                record["title"] = provisional_title
            self._append_log_record(
                target.get("id") or sid or self.session_data.get("id"),
                record,
            )

        self._enqueue_mutation_for(_mutate, sid=sid or target.get("id"))

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
            self._register_live(self.session_data)
            self._save_session_data(self.session_data, as_focused=True)
            msg_count = len(self.session_data.get("messages", []))
            evt_count = len(self.session_data.get("events", []))
            sid = self.session_data.get("id", "unknown")
            logger.info(
                f"[SessionManager] _save_session: sid={sid}, messages={msg_count}, events={evt_count}, "
                f"save_seq={self._save_seq}, file={self.current_session_file}"
            )
        except Exception as e:
            logger.error(f"[SessionManager] Failed to save session: {e}")

    def update_last_message_elapsed_ms(self, elapsed_ms: int):
        """Write elapsed_ms (total workflow time) to the last assistant message."""

        def _mutate():
            messages = self.session_data.get("messages", [])
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "assistant":
                    messages[i]["elapsed_ms"] = elapsed_ms
                    self._append_log_record(
                        self.session_data.get("id"),
                        {"op": "tail_patch", "patches": {"elapsed_ms": elapsed_ms}},
                    )
                    return

        self._enqueue_mutation(_mutate)

    def mark_last_assistant_end_task(self):
        """Mark the latest assistant message as a complex-task end report (for UI fold)."""

        def _mutate():
            messages = self.session_data.get("messages", [])
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "assistant":
                    messages[i]["end_task"] = True
                    self._append_log_record(
                        self.session_data.get("id"),
                        {"op": "tail_patch", "patches": {"end_task": True}},
                    )
                    return

        self._enqueue_mutation(_mutate)

    def sync_tool_call_message(
        self,
        tool_calls: list[dict],
        content: str | None = None,
        reasoning_content: str | None = None,
        *,
        sid: str | None = None,
    ):
        """Persist an assistant message carrying ``tool_calls`` (tool-loop fix).

        Patches the most recent assistant message that has no ``tool_calls``
        yet (the LLM reply that produced these calls); if none exists, appends
        a new assistant message. Without this, tool-call history lived only in
        the in-memory ChatAPI ``req`` and was lost on every ``_load_history``
        (turn bind) — the root cause of long agent tool loops where the LLM
        re-issued identical read-only calls for hundreds of rounds.
        """
        target = self._resolve_session_data(sid)
        target_id = target.get("id") or sid or self.session_data.get("id")

        def _mutate():
            messages = target.setdefault("messages", [])
            for i in range(len(messages) - 1, -1, -1):
                m = messages[i]
                if m.get("role") == "assistant" and not m.get("tool_calls"):
                    m["tool_calls"] = tool_calls
                    if content is not None:
                        m["content"] = content
                    elif not m.get("content"):
                        m["content"] = None
                    if reasoning_content is not None:
                        m["reasoning_content"] = reasoning_content
                    target["last_updated"] = utc_now_iso()
                    self._append_log_record(
                        target_id,
                        {"op": "tail_patch", "patches": {"tool_calls": tool_calls, "content": m.get("content")}},
                    )
                    return
            # No patchable assistant — append a fresh assistant(tool_calls) msg.
            message = {
                "role": "assistant",
                "content": content or "",
                "type": "api_sync",
                "timestamp": utc_now_iso(),
                "tool_calls": tool_calls,
            }
            if reasoning_content is not None:
                message["reasoning_content"] = reasoning_content
            messages.append(message)
            target["last_updated"] = utc_now_iso()
            self._append_log_record(target_id, {"op": "msg_append", "msg": message, "ts": message["timestamp"]})

        self._enqueue_mutation_for(_mutate, sid=target_id)

    def get_messages(self, limit: int | None = None, *, sid: str | None = None) -> list[dict]:
        data = self._resolve_session_data(sid)
        messages = data.get("messages") or []
        if limit:
            return messages[-limit:]
        return messages

    def get_messages_for_chat_api(self, limit: int = 50, *, sid: str | None = None) -> list[dict]:
        messages = self.get_messages(limit, sid=sid)
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

    def get_events(self, limit: int | None = None, *, sid: str | None = None) -> list[dict]:
        data = self._resolve_session_data(sid)
        events = data.get("events", [])
        if limit:
            return events[-limit:]
        return events

    def flush(self):
        """Drain all pending async mutations and sync the latest state to disk.

        Call this before reading session data from another process/thread
        (e.g. Gateway) to guarantee the file reflects all in-memory state.
        """
        with self._lock:
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
            self._append_log_record(
                self.session_data.get("id"),
                {
                    "op": "meta",
                    "fields": {"title": title, "last_updated": self.session_data["last_updated"]},
                },
            )

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
        with self._lock:
            self._drain_pending_mutations_sync()
            self._mutation_gen += 1
            self._init_new_session()
            # clear() is user-initiated; sync flush to guarantee immediate persistence
            self._save_session()

    def truncate_from_timestamp(
        self,
        timestamp: str,
        *,
        inclusive: bool = True,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """Remove messages/events at or after a cut point. Used by withdraw.

        Applies synchronously (drain + mutate + save) so subsequent
        ``get_messages()`` / ``history_sync`` see the truncated session — not
        the pre-withdraw snapshot.

        Prefer *message_id* (client/server id) to locate the cut **index** —
        many messages can share the same second-precision timestamp.
        """
        ts = (timestamp or "").strip()
        mid = (message_id or "").strip()
        if not ts and not mid:
            return {"ok": False, "error": "timestamp or message_id required"}

        with self._lock:
            # Ensure prior async mutations are applied before we cut the session
            self._drain_pending_mutations_sync()

            messages = list(self.session_data.get("messages") or [])
            events = list(self.session_data.get("events") or [])

            cut_idx: int | None = None
            cut_raw = ts

            if mid:
                for i, m in enumerate(messages):
                    if not isinstance(m, dict):
                        continue
                    ids = {
                        str(m.get("message_id") or "").strip(),
                        str(m.get("client_id") or "").strip(),
                        str(m.get("id") or "").strip(),
                    }
                    if mid in ids and mid:
                        cut_idx = i
                        cut_raw = str(m.get("timestamp") or "").strip() or cut_raw
                        break

            if cut_idx is not None:
                kept_m = messages[:cut_idx] if inclusive else messages[: cut_idx + 1]
            else:
                if not cut_raw:
                    return {"ok": False, "error": "could not resolve cut timestamp"}

                from opensquad.time_utils import utc_from_iso

                def _parse(raw: str):
                    try:
                        return utc_from_iso(raw)
                    except Exception:
                        return None

                cut_dt = _parse(cut_raw)

                # First message at/after cut time (avoids wiping a whole same-second burst
                # when we cannot resolve message_id).
                found_idx: int | None = None
                for i, m in enumerate(messages):
                    raw = str((m or {}).get("timestamp") or "").strip()
                    if not raw:
                        continue
                    item_dt = _parse(raw)
                    if cut_dt is not None and item_dt is not None:
                        if item_dt >= cut_dt:
                            found_idx = i
                            break
                    elif (inclusive and raw >= cut_raw) or (not inclusive and raw > cut_raw):
                        found_idx = i
                        break
                if found_idx is None:
                    kept_m = list(messages)
                else:
                    kept_m = messages[:found_idx] if inclusive else messages[: found_idx + 1]
                    cut_idx = found_idx

            # Events: drop at/after cut timestamp when known; else drop all after last kept msg ts
            if cut_raw:
                from opensquad.time_utils import utc_from_iso

                def _parse2(raw: str):
                    try:
                        return utc_from_iso(raw)
                    except Exception:
                        return None

                cut_dt2 = _parse2(cut_raw)

                def _keep_e(item_ts: Any) -> bool:
                    raw = str(item_ts or "").strip()
                    if not raw:
                        return True
                    item_dt = _parse2(raw)
                    if cut_dt2 is not None and item_dt is not None:
                        return item_dt < cut_dt2 if inclusive else item_dt <= cut_dt2
                    return (raw < cut_raw) if inclusive else (raw <= cut_raw)

                kept_e = [e for e in events if _keep_e(e.get("timestamp"))]
            else:
                kept_e = events

            self.session_data["messages"] = kept_m
            self.session_data["events"] = kept_e
            self.session_data["last_updated"] = utc_now_iso()
            # Invalidate any async batch already dequeued before this lock.
            self._mutation_gen += 1
            try:
                self._save_session()
            except Exception:
                pass
            return {
                "ok": True,
                "timestamp": cut_raw or ts,
                "message_id": mid or None,
                "messages": len(kept_m),
                "events": len(kept_e),
                "cut_index": cut_idx,
            }

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_messages": len(self.session_data["messages"]),
            "total_archived_messages": len(self.session_data.get("archived_messages") or []),
            "total_archived_events": len(self.session_data.get("archived_events") or []),
            "created_at": self.session_data["created_at"],
            "last_updated": self.session_data["last_updated"],
        }

    @staticmethod
    def _has_user_input(data: dict | None) -> bool:
        """True when the user has actually sent something (text / images / files)."""
        if not isinstance(data, dict):
            return False
        for m in data.get("messages") or []:
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            if str(m.get("content") or "").strip():
                return True
            if m.get("images") or m.get("attachments"):
                return True
        return False

    @classmethod
    def _is_reusable_draft(cls, data: dict | None) -> bool:
        """Empty interactive shell that should be reused instead of spawning another sid."""
        if not isinstance(data, dict) or not str(data.get("id") or "").strip():
            return False
        if str(data.get("origin") or "").strip() == "scheduled_task":
            return False
        return not cls._has_user_input(data)

    @staticmethod
    def _session_hidden_from_list(data: dict | None) -> bool:
        """True when session should not appear in the interactive session sidebar."""
        if not isinstance(data, dict):
            return False
        if str(data.get("origin") or "").strip() == "scheduled_task":
            return True
        # Explicit New Session draft cache — never list until first user send.
        if data.get("draft"):
            return True
        # Legacy never-sent shells (no draft flag, no title) — same treatment.
        return SessionManager._is_reusable_draft(data) and not str(data.get("title") or "").strip()

    def get_session_list(self) -> list[dict[str, str]]:
        """Get all session list (excludes scheduled-task origin sessions)."""
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
        if curr_id and not self._session_hidden_from_list(self.session_data):
            messages = self.session_data.get("messages", [])
            title = self.resolve_session_title(messages, self.session_data.get("title"), curr_id)
            preview = _extract_preview(messages)
            sessions.append(
                {
                    "id": curr_id,
                    "title": title,
                    "preview": preview,
                    "current": True,
                    "primary": curr_id == self.get_primary_session_id(),
                    "created_at": self.session_data.get("created_at"),
                    "last_updated": self.session_data.get("last_updated"),
                }
            )
            seen_ids.add(curr_id)
        elif curr_id:
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
                    origin = ""
                    # Prefer reading title from cache (with mtime validation)
                    cached = self._cache_get(sid)
                    if cached is not None:
                        if self._session_hidden_from_list(cached):
                            seen_ids.add(sid)
                            continue
                        messages = cached.get("messages", [])
                        title = self.resolve_session_title(messages, cached.get("title"), sid)
                        preview = _extract_preview(messages)
                        created_at = cached.get("created_at")
                        last_updated = cached.get("last_updated")
                        origin = str(cached.get("origin") or "").strip()
                    else:
                        try:
                            with open(os.path.join(history_dir, f), encoding="utf-8") as jf:
                                content = jf.read()
                                try:
                                    parsed = json.loads(content)
                                    if isinstance(parsed, dict):
                                        # Merge log records newer than the snapshot
                                        # (title/preview/hidden flags stay fresh).
                                        self._replay_log_into(parsed, sid, int(parsed.get("_save_seq") or 0))
                                        if self._session_hidden_from_list(parsed):
                                            seen_ids.add(sid)
                                            continue
                                        messages = parsed.get("messages", []) or []
                                    else:
                                        messages = parsed.get("messages", []) or []
                                    title = self.resolve_session_title(messages, parsed.get("title"), sid)
                                    preview = _extract_preview(messages)
                                    if isinstance(parsed, dict):
                                        created_at = parsed.get("created_at")
                                        last_updated = parsed.get("last_updated")
                                        origin = str(parsed.get("origin") or "").strip()
                                except Exception:
                                    match = re.search(r"<title>(.*?)</title>", content, re.DOTALL)
                                    if match:
                                        title = match.group(1).strip() or sid
                        except Exception:
                            pass
                    entry = {
                        "id": sid,
                        "title": title,
                        "preview": preview,
                        "current": False,
                        "primary": sid == self.get_primary_session_id(),
                        "created_at": created_at,
                        "last_updated": last_updated,
                    }
                    if origin:
                        entry["origin"] = origin
                    sessions.append(entry)
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
        """Delete a history session, or abandon an empty current session.

        Non-empty current sessions cannot be deleted (would destroy in-flight work).
        Empty current is rotated to a fresh id so the deleted id disappears.
        """
        session_id = (session_id or "").strip()
        if not session_id:
            return False
        with self._lock:
            self._cache_remove(session_id)
            self._live_sessions.pop(session_id, None)

            if session_id == self.session_data.get("id"):
                messages = self.session_data.get("messages") or []
                if messages:
                    return False
                # Abandon empty current — rotate so the old id is gone.
                self._mutation_gen += 1
                self._init_new_session()
                self._register_live(self.session_data)
                history_path = os.path.join(self.history_dir, f"{session_id}.json")
                log_path = self._log_path_for(session_id)
                for p in (history_path, log_path):
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                logger.info(
                    "[SessionManager] Abandoned empty current session %s → %s",
                    session_id,
                    self.session_data.get("id"),
                )
                return True

            history_dir = self.history_dir
            file_path = os.path.join(history_dir, f"{session_id}.json")
            removed = False
            for p in (file_path, self._log_path_for(session_id)):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        removed = True
                    except Exception:
                        pass
            return bool(removed)


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
