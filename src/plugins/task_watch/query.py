"""
Task Watch - Query Module

Reads from the SQLite task_watch database and the live TaskSupervisor
singleton to return data for the dashboard UI.

Standard entry point (called by Launcher's dynamic plugin data routing):
    query_data(project_root: str, params: dict) -> dict
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Standard entry point
# ---------------------------------------------------------------------------


def query_data(project_root: str, params: dict) -> dict:
    """
    Standard plugin query entry point.

    Called by Launcher's _handle_get_plugin_data with:
        project_root  - absolute path to project root
        params        - flat dict of query-string params
    """
    time_range = params.get("range", "24h")
    agent_id = params.get("agent_id") or None

    # Resolve DB path
    db_rel = "data/plugins/task_watch/task_watch.db"
    config_path = os.path.join(project_root, "data", "plugins", "task_watch", "config.json")
    if os.path.isfile(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            db_rel = cfg.get("db_path", db_rel)
        except Exception:
            pass

    db_path = os.path.join(project_root, db_rel)

    # Get live supervisor state
    live_state = _get_live_supervisor_state()

    return _query_all(db_path, time_range=time_range, agent_id=agent_id, live_state=live_state)


# ---------------------------------------------------------------------------
# Live TaskSupervisor state
# ---------------------------------------------------------------------------


def _get_live_supervisor_state() -> dict:
    """
    Try to read the in-memory TaskSupervisor singleton.
    This works when running inside the agent process.
    In the Launcher process it will fail gracefully.
    """
    try:
        from opensquad.task_supervisor import task_supervisor

        status = task_supervisor.get_status()
        history = list(task_supervisor._history) if task_supervisor._history else []
        # Enrich with progress log from current task
        progress_log = []
        if task_supervisor.current_task:
            progress_log = [
                {"time": p.get("time", ""), "text": p.get("text", ""), "elapsed": p.get("elapsed", 0)}
                for p in (task_supervisor.current_task.progress_log or [])
            ]
        return {
            "status": status,
            "history": history,
            "progress_log": progress_log,
        }
    except Exception:
        return {"status": {"active": False}, "history": [], "progress_log": []}


# ---------------------------------------------------------------------------
# Internal query functions
# ---------------------------------------------------------------------------


def _connect(db_path: str) -> sqlite3.Connection | None:
    if not os.path.isfile(db_path):
        return None
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA query_only=ON")
    return conn


def _range_to_cutoff(time_range: str) -> str:
    now = datetime.now(timezone.utc)
    deltas = {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    if time_range == "all":
        return "1970-01-01T00:00:00+00:00"
    delta = deltas.get(time_range, timedelta(hours=24))
    return (now - delta).isoformat()


def _query_all(
    db_path: str, time_range: str = "24h", agent_id: str | None = None, live_state: dict | None = None
) -> dict:
    """Main dashboard query returning all data."""
    t0 = time.monotonic()

    conn = _connect(db_path)
    cutoff = _range_to_cutoff(time_range)

    agent_filter = ""
    params: list[Any] = [cutoff]
    if agent_id:
        agent_filter = " AND agent_id = ?"
        params.append(agent_id)

    if conn is None:
        # No DB yet — return live state only
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        return {
            "summary": _empty_summary(),
            "live_task": live_state.get("status", {}) if live_state else {},
            "live_progress": live_state.get("progress_log", []) if live_state else [],
            "task_history": live_state.get("history", []) if live_state else [],
            "task_events": [],
            "tool_timeline": [],
            "tool_stats": [],
            "meta": {"db_path": db_path, "time_range": time_range, "cutoff": cutoff, "query_time_ms": elapsed_ms},
        }

    try:
        summary = _query_summary(conn, cutoff, agent_filter, params)
        task_events = _query_task_events(conn, cutoff, agent_filter, params)
        tool_timeline = _query_tool_timeline(conn, cutoff, agent_filter, params, time_range)
        tool_stats = _query_tool_stats(conn, cutoff, agent_filter, params)
        task_history_db = _query_task_history(conn, cutoff, agent_filter, params)
    finally:
        conn.close()

    # Merge live supervisor history with DB history
    task_history = task_history_db
    if live_state and live_state.get("history"):
        # Deduplicate by task_id
        existing_ids = {h.get("task_id") for h in task_history_db}
        for h in live_state["history"]:
            if h.get("task_id") not in existing_ids:
                task_history.append(h)

    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    return {
        "summary": summary,
        "live_task": live_state.get("status", {}) if live_state else {},
        "live_progress": live_state.get("progress_log", []) if live_state else [],
        "task_history": task_history,
        "task_events": task_events,
        "tool_timeline": tool_timeline,
        "tool_stats": tool_stats,
        "meta": {
            "db_path": db_path,
            "time_range": time_range,
            "cutoff": cutoff,
            "query_time_ms": elapsed_ms,
        },
    }


def _empty_summary() -> dict:
    return {
        "total_tasks": 0,
        "completed_tasks": 0,
        "abandoned_tasks": 0,
        "total_stalls": 0,
        "total_updates": 0,
        "avg_duration_sec": 0,
        "total_tool_calls": 0,
        "tool_error_count": 0,
    }


def _query_summary(conn: sqlite3.Connection, cutoff: str, agent_filter: str, params: list) -> dict:
    """Aggregate summary from task_events and tool_activity."""
    # Task summary
    sql = f"""
        SELECT
            COUNT(DISTINCT task_id) AS total_tasks,
            SUM(CASE WHEN event_type='complete' THEN 1 ELSE 0 END) AS completed_tasks,
            SUM(CASE WHEN event_type='abandon' THEN 1 ELSE 0 END) AS abandoned_tasks,
            SUM(CASE WHEN event_type='stall' THEN 1 ELSE 0 END) AS total_stalls,
            SUM(CASE WHEN event_type='update' THEN 1 ELSE 0 END) AS total_updates,
            AVG(CASE WHEN event_type IN ('complete','abandon') THEN elapsed_sec ELSE NULL END) AS avg_duration
        FROM task_events
        WHERE timestamp >= ?{agent_filter}
    """
    row = conn.execute(sql, params).fetchone()

    # Tool activity summary
    sql2 = f"""
        SELECT
            COUNT(*) AS total_tool_calls,
            SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS tool_errors
        FROM tool_activity
        WHERE timestamp >= ?{agent_filter}
    """
    row2 = conn.execute(sql2, params).fetchone()

    return {
        "total_tasks": row["total_tasks"] if row else 0,
        "completed_tasks": row["completed_tasks"] if row else 0,
        "abandoned_tasks": row["abandoned_tasks"] if row else 0,
        "total_stalls": row["total_stalls"] if row else 0,
        "total_updates": row["total_updates"] if row else 0,
        "avg_duration_sec": round(row["avg_duration"] or 0, 1) if row else 0,
        "total_tool_calls": row2["total_tool_calls"] if row2 else 0,
        "tool_error_count": row2["tool_errors"] if row2 else 0,
    }


def _query_task_events(conn: sqlite3.Connection, cutoff: str, agent_filter: str, params: list) -> list:
    """Recent task lifecycle events."""
    sql = f"""
        SELECT timestamp, event_type, task_id, agent_id, description,
               detail, stall_count, elapsed_sec
        FROM task_events
        WHERE timestamp >= ?{agent_filter}
        ORDER BY timestamp DESC
        LIMIT 100
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _query_tool_timeline(
    conn: sqlite3.Connection, cutoff: str, agent_filter: str, params: list, time_range: str
) -> list:
    """Tool activity grouped into time buckets."""
    if time_range in ("1h", "6h"):
        bucket_expr = "substr(timestamp, 1, 16)"  # minute-level
        bucket_label = "minute"
    elif time_range == "24h":
        bucket_expr = "substr(timestamp, 1, 13)"  # hourly
        bucket_label = "hour"
    else:
        bucket_expr = "substr(timestamp, 1, 10)"  # daily
        bucket_label = "day"

    sql = f"""
        SELECT
            {bucket_expr} AS bucket,
            COUNT(*) AS calls,
            SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS errors
        FROM tool_activity
        WHERE timestamp >= ?{agent_filter}
        GROUP BY bucket
        ORDER BY bucket ASC
        LIMIT 500
    """
    rows = conn.execute(sql, params).fetchall()
    return [{"bucket": r["bucket"], "calls": r["calls"], "errors": r["errors"], "label": bucket_label} for r in rows]


def _query_tool_stats(conn: sqlite3.Connection, cutoff: str, agent_filter: str, params: list) -> list:
    """Top tools by call count."""
    sql = f"""
        SELECT
            tool_name,
            COUNT(*) AS call_count,
            SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS error_count
        FROM tool_activity
        WHERE timestamp >= ?{agent_filter}
        GROUP BY tool_name
        ORDER BY call_count DESC
        LIMIT 20
    """
    rows = conn.execute(sql, params).fetchall()
    return [{"tool_name": r["tool_name"], "call_count": r["call_count"], "error_count": r["error_count"]} for r in rows]


def _query_task_history(conn: sqlite3.Connection, cutoff: str, agent_filter: str, params: list) -> list:
    """Task history from start/complete/abandon events (deduplicated by task_id)."""
    # Get the most recent event per task_id to determine final status
    sql = f"""
        SELECT
            task_id,
            MAX(CASE WHEN event_type='start' THEN timestamp END) AS started_at,
            MAX(CASE WHEN event_type IN ('complete','abandon') THEN event_type END) AS final_status,
            MAX(CASE WHEN event_type IN ('complete','abandon') THEN timestamp END) AS ended_at,
            MAX(elapsed_sec) AS elapsed_seconds,
            MAX(stall_count) AS max_stall_count,
            MAX(description) AS description,
            SUM(CASE WHEN event_type='update' THEN 1 ELSE 0 END) AS progress_updates
        FROM task_events
        WHERE timestamp >= ?{agent_filter}
        GROUP BY task_id
        ORDER BY started_at DESC
        LIMIT 50
    """
    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        result.append(
            {
                "task_id": r["task_id"],
                "description": r["description"] or "",
                "status": r["final_status"] or "active",
                "started_at": r["started_at"] or "",
                "ended_at": r["ended_at"] or "",
                "elapsed_seconds": r["elapsed_seconds"] or 0,
                "stall_count": r["max_stall_count"] or 0,
                "progress_updates": r["progress_updates"] or 0,
            }
        )
    return result
