# -*- coding: utf-8 -*-
"""
Task Watch - SQLite Storage Layer

Persists task supervision events and tool activity records for the
Task Watch dashboard. Uses WAL mode for safe concurrent access.

Tables:
- task_events: lifecycle events (start, update, stall, complete, abandon)
- tool_activity: per-tool-call timestamps for activity heatmap
"""
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("plugins.task_watch.storage")

_CREATE_TASK_EVENTS = """
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,       -- start, update, stall, complete, abandon
    task_id TEXT NOT NULL,
    agent_id TEXT DEFAULT '',
    description TEXT DEFAULT '',
    detail TEXT DEFAULT '',         -- JSON or free text
    stall_count INTEGER DEFAULT 0,
    elapsed_sec REAL DEFAULT 0
);
"""

_CREATE_TOOL_ACTIVITY = """
CREATE TABLE IF NOT EXISTS tool_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent_id TEXT DEFAULT '',
    tool_name TEXT NOT NULL,
    success INTEGER DEFAULT 1       -- 1 = ok, 0 = error
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tevt_time ON task_events(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_tevt_task ON task_events(task_id, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_tact_time ON tool_activity(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_tact_agent ON tool_activity(agent_id, timestamp);",
]

_INSERT_TASK_EVENT = """
INSERT INTO task_events (
    timestamp, event_type, task_id, agent_id, description, detail,
    stall_count, elapsed_sec
) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_TOOL_ACTIVITY = """
INSERT INTO tool_activity (timestamp, agent_id, tool_name, success)
VALUES (?, ?, ?, ?);
"""


class TaskWatchStorage:
    """
    SQLite-backed storage for task watch data.

    Thread-safe with internal locking. Uses WAL journal mode.
    Records are buffered and flushed periodically.
    """

    def __init__(self, db_path: str, buffer_size: int = 5, flush_interval_sec: int = 15):
        self._db_path = db_path
        self._buffer_size = buffer_size
        self._flush_interval = flush_interval_sec

        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")

        self._conn.execute(_CREATE_TASK_EVENTS)
        self._conn.execute(_CREATE_TOOL_ACTIVITY)
        for idx_sql in _CREATE_INDEXES:
            self._conn.execute(idx_sql)
        self._conn.commit()

        self._event_buf: List[tuple] = []
        self._activity_buf: List[tuple] = []
        self._lock = threading.Lock()
        self._last_flush = time.time()

        logger.info(f"[TaskWatchStorage] Initialized: {db_path}")

    def record_task_event(
        self,
        event_type: str,
        task_id: str,
        agent_id: str = "",
        description: str = "",
        detail: str = "",
        stall_count: int = 0,
        elapsed_sec: float = 0,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        row = (now, event_type, task_id, agent_id, description[:500],
               detail[:1000], stall_count, round(elapsed_sec, 1))
        with self._lock:
            self._event_buf.append(row)
            self._maybe_flush()

    def record_tool_activity(
        self, agent_id: str, tool_name: str, success: bool = True
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        row = (now, agent_id, tool_name, 1 if success else 0)
        with self._lock:
            self._activity_buf.append(row)
            self._maybe_flush()

    def _maybe_flush(self) -> None:
        total = len(self._event_buf) + len(self._activity_buf)
        elapsed = time.time() - self._last_flush
        if total >= self._buffer_size or elapsed >= self._flush_interval:
            self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        events = self._event_buf[:]
        activities = self._activity_buf[:]
        self._event_buf.clear()
        self._activity_buf.clear()
        self._last_flush = time.time()

        if not events and not activities:
            return

        try:
            if events:
                self._conn.executemany(_INSERT_TASK_EVENT, events)
            if activities:
                self._conn.executemany(_INSERT_TOOL_ACTIVITY, activities)
            self._conn.commit()
            logger.debug(f"[TaskWatchStorage] Flushed {len(events)} events + {len(activities)} activities")
        except Exception as e:
            logger.error(f"[TaskWatchStorage] Flush error: {e}", exc_info=True)

    def close(self) -> None:
        try:
            self.flush()
            self._conn.close()
            logger.info("[TaskWatchStorage] Closed")
        except Exception as e:
            logger.error(f"[TaskWatchStorage] Error closing: {e}")
