"""
Token Analytics - SQLite Storage Layer

Persists token usage snapshots and tool usage records for historical
trend analysis. Uses WAL mode for safe multi-process concurrent writes
(each agent process writes independently to the same DB).

Tables:
- token_snapshots: per-LLM-call token window state + cumulative stats
- tool_usage: per-tool-call estimated token consumption
"""

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("plugins.token_analytics.storage")

_CREATE_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS token_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    session_id TEXT DEFAULT '',

    -- context window state
    window_used INTEGER DEFAULT 0,
    window_max INTEGER DEFAULT 0,

    -- 4-dimension breakdown (estimated tokens, char_count / 3)
    breakdown_user INTEGER DEFAULT 0,
    breakdown_thought INTEGER DEFAULT 0,
    breakdown_tool INTEGER DEFAULT 0,
    breakdown_tool_defs INTEGER DEFAULT 0,
    breakdown_response INTEGER DEFAULT 0,

    -- cumulative precise stats (from API response)
    cumul_input_tokens INTEGER DEFAULT 0,
    cumul_output_tokens INTEGER DEFAULT 0,
    cumul_total_tokens INTEGER DEFAULT 0,
    cumul_requests INTEGER DEFAULT 0,

    -- cache stats (Anthropic: read + creation; OpenAI: read only)
    cumul_cache_read_tokens INTEGER DEFAULT 0,
    cumul_cache_creation_tokens INTEGER DEFAULT 0
);
"""

_CREATE_TOOL_USAGE_TABLE = """
CREATE TABLE IF NOT EXISTS tool_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    session_id TEXT DEFAULT '',
    tool_name TEXT NOT NULL,
    args_tokens_est INTEGER DEFAULT 0,
    result_tokens_est INTEGER DEFAULT 0
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_snapshots_agent_time ON token_snapshots(agent_id, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_model ON token_snapshots(model, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_tool_usage_agent ON tool_usage(agent_id, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_tool_usage_tool ON tool_usage(tool_name, timestamp);",
]

_INSERT_SNAPSHOT = """
INSERT INTO token_snapshots (
    timestamp, agent_id, model, session_id,
    window_used, window_max,
    breakdown_user, breakdown_thought, breakdown_tool, breakdown_tool_defs, breakdown_response,
    cumul_input_tokens, cumul_output_tokens, cumul_total_tokens, cumul_requests,
    cumul_cache_read_tokens, cumul_cache_creation_tokens
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_TOOL_USAGE = """
INSERT INTO tool_usage (
    timestamp, agent_id, model, session_id,
    tool_name, args_tokens_est, result_tokens_est
) VALUES (?, ?, ?, ?, ?, ?, ?);
"""


class TokenStorage:
    """
    SQLite-backed storage for token analytics data.

    Thread-safe with internal locking. Uses WAL journal mode for
    multi-process safety (multiple agent processes write concurrently).

    Records are buffered in memory and flushed to disk either when the
    buffer reaches `buffer_size` or when `flush_interval_sec` has passed.
    """

    def __init__(self, db_path: str, buffer_size: int = 10, flush_interval_sec: int = 30):
        """
        Args:
            db_path: absolute path to the SQLite database file.
            buffer_size: flush to disk after this many buffered records.
            flush_interval_sec: max seconds between automatic flushes.
        """
        self._db_path = db_path
        self._buffer_size = buffer_size
        self._flush_interval = flush_interval_sec

        # Ensure directory exists
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        # Open connection (check_same_thread=False because EventBus may
        # call from different threads)
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")

        # Create tables and indexes
        self._conn.execute(_CREATE_SNAPSHOTS_TABLE)
        self._conn.execute(_CREATE_TOOL_USAGE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            self._conn.execute(idx_sql)
        self._conn.commit()

        # Schema migration: add cache columns to existing DBs
        existing_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(token_snapshots)")}
        for col, definition in [
            ("cumul_cache_read_tokens", "INTEGER DEFAULT 0"),
            ("cumul_cache_creation_tokens", "INTEGER DEFAULT 0"),
            # breakdown_tool_defs added when tool-def schema was split out of
            # breakdown_tool (so "tool" = real IO, "tool_defs" = advertised schema).
            ("breakdown_tool_defs", "INTEGER DEFAULT 0"),
        ]:
            if col not in existing_cols:
                self._conn.execute(f"ALTER TABLE token_snapshots ADD COLUMN {col} {definition}")
        self._conn.commit()

        # Internal buffers
        self._snapshot_buf: list[tuple] = []
        self._tool_buf: list[tuple] = []
        self._lock = threading.Lock()
        self._last_flush_time = time.time()

        logger.info(
            f"[TokenStorage] Initialized: {db_path} (buffer={buffer_size}, flush_interval={flush_interval_sec}s)"
        )

    def record_snapshot(self, agent_id: str, session_id: str, model: str, token_data: dict[str, Any]) -> None:
        """
        Buffer a token snapshot record.

        Args:
            agent_id: agent identifier
            session_id: turn session ID
            model: LLM model name (e.g. "gpt-4o", "claude-3-5-sonnet")
            token_data: dict from EventBus token_stats event with keys:
                        used, max, breakdown, cumulative
        """
        now = datetime.now(timezone.utc).isoformat()
        breakdown = token_data.get("breakdown", {})
        cumulative = token_data.get("cumulative", {})

        row = (
            now,
            agent_id,
            model,
            session_id,
            token_data.get("used", 0),
            token_data.get("max", 0),
            breakdown.get("user", 0),
            breakdown.get("thought", 0),
            breakdown.get("tool", 0),
            breakdown.get("tool_defs", 0),
            breakdown.get("response", 0),
            cumulative.get("total_input_tokens", 0),
            cumulative.get("total_output_tokens", 0),
            cumulative.get("total_tokens", 0),
            cumulative.get("total_requests", 0),
            cumulative.get("cache_read_tokens", 0),
            cumulative.get("cache_creation_tokens", 0),
        )

        with self._lock:
            self._snapshot_buf.append(row)
            self._maybe_flush()

    def record_tool_usage(
        self, agent_id: str, session_id: str, model: str, tool_name: str, args_text: str, result_text: str
    ) -> None:
        """
        Buffer a tool usage record with estimated token counts.

        Token estimation: len(text) // 3  (same heuristic as runner.py)

        Args:
            agent_id: agent identifier
            session_id: turn session ID
            model: LLM model name
            tool_name: fully qualified tool name (e.g. "filesystem.read_file")
            args_text: stringified tool arguments
            result_text: stringified tool result
        """
        now = datetime.now(timezone.utc).isoformat()
        args_est = len(args_text) // 3 if args_text else 0
        result_est = len(result_text) // 3 if result_text else 0

        row = (
            now,
            agent_id,
            model,
            session_id,
            tool_name,
            args_est,
            result_est,
        )

        with self._lock:
            self._tool_buf.append(row)
            self._maybe_flush()

    def _maybe_flush(self) -> None:
        """Check if buffer should be flushed (called inside lock)."""
        total = len(self._snapshot_buf) + len(self._tool_buf)
        elapsed = time.time() - self._last_flush_time

        if total >= self._buffer_size or elapsed >= self._flush_interval:
            self._flush_locked()

    def flush(self) -> None:
        """Force flush all buffered records to disk."""
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        """Flush buffers to SQLite (must be called inside lock)."""
        snapshots = self._snapshot_buf[:]
        tools = self._tool_buf[:]
        self._snapshot_buf.clear()
        self._tool_buf.clear()
        self._last_flush_time = time.time()

        if not snapshots and not tools:
            return

        try:
            if snapshots:
                self._conn.executemany(_INSERT_SNAPSHOT, snapshots)
            if tools:
                self._conn.executemany(_INSERT_TOOL_USAGE, tools)
            self._conn.commit()
            logger.debug(f"[TokenStorage] Flushed {len(snapshots)} snapshots + {len(tools)} tool records")
        except Exception as e:
            logger.error(f"[TokenStorage] Flush error: {e}", exc_info=True)

    def close(self) -> None:
        """Flush remaining buffer and close database connection."""
        try:
            self.flush()
            self._conn.close()
            logger.info("[TokenStorage] Closed database connection")
        except Exception as e:
            logger.error(f"[TokenStorage] Error closing: {e}")
