# -*- coding: utf-8 -*-
"""
Token Analytics - Query Module

Reads from the SQLite analytics database and returns aggregated statistics
for the frontend dashboard. Designed to be called from the Launcher HTTP
handler (runs in a separate process from the agents).

Standard entry point (called by Launcher's dynamic plugin data routing):
    query_data(project_root: str, params: dict) -> dict

Legacy entry point (direct call):
    query_dashboard(db_path: str, time_range, agent_id) -> dict

All functions return plain dicts suitable for JSON serialization.
"""
import os
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


# ── Standard entry point for dynamic plugin data routing ──

# Metric selects which per-call delta is summed for chartable fields.
# `cumul_*` columns are running totals (per agent, across sessions). We never
# SUM them directly — that would double-count (e.g. 75 calls × ~2.7M each
# would render as 200M). Instead we compute per-call deltas with LAG() and
# SUM those. See _DELTAS_CTE_TEMPLATE below.
#   total          → input + output (what users pay for)
#   cache_read     → Anthropic/OpenAI cache hits
#   cache_creation → Anthropic cache warm-up
_METRIC_DELTA_COLS = {
    "total": "delta_total",
    "cache_read": "delta_cache_read",
    "cache_creation": "delta_cache_creation",
}


def _metric_delta_col(metric: str) -> str:
    return _METRIC_DELTA_COLS.get(metric, "delta_total")


def _bucket_expr(time_range: str) -> tuple:
    """Pick a SQL bucket expression + label for the time range.

    Mirrors the legacy timeline so the chart stays informative at any zoom:
        1h / 6h → 10-minute buckets (substr 1..15)
        24h     → hourly         (substr 1..13)
        7d/30d/all → daily       (substr 1..10)
    """
    if time_range in ("1h", "6h"):
        return "substr(timestamp, 1, 15)", "minute"
    if time_range == "24h":
        return "substr(timestamp, 1, 13)", "hour"
    return "substr(timestamp, 1, 10)", "day"


# CTE that materializes per-call deltas over the FULL unfiltered table.
# Window function LAG() gives the previous row's cumul within the same
# agent; the difference is this row's incremental token cost.
#
# IMPORTANT: the time-range / agent filter is applied by each OUTER query
# (`WHERE timestamp >= ? ...`), NOT inside this CTE. Filtering here would
# make LAG() return NULL for the first row of every filtered window, and
# the COALESCE fallback would book that row's entire running cumulative
# total as a single call's cost — inflating every non-"all" range by the
# agent's whole history up to that point (24h ~4x, 1h ~60x in practice).
# Keeping LAG over the unfiltered table makes the filtered window's first
# row correctly cost (cumul_now - cumul_just_before_window).
#
# We MAX(0, ...) to clamp the negative spike at session boundaries (e.g.
# when the agent process restarts and the runner's restored hist is
# smaller than the backfilled historical cumul). Clamping to zero means
# we lose the first call of each new session from the totals, which is a
# small acceptable loss vs. a per-agent state table.
_DELTAS_CTE_TEMPLATE = """\
deltas AS (
  SELECT
    agent_id,
    COALESCE(NULLIF(model, ''), '(unknown)') AS model,
    timestamp,
    id,
    {bucket_expr} AS bucket,
    MAX(0, COALESCE(cumul_total_tokens
             - LAG(cumul_total_tokens) OVER (PARTITION BY agent_id ORDER BY id),
             cumul_total_tokens)) AS delta_total,
    MAX(0, COALESCE(cumul_input_tokens
             - LAG(cumul_input_tokens) OVER (PARTITION BY agent_id ORDER BY id),
             cumul_input_tokens)) AS delta_input,
    MAX(0, COALESCE(cumul_output_tokens
             - LAG(cumul_output_tokens) OVER (PARTITION BY agent_id ORDER BY id),
             cumul_output_tokens)) AS delta_output,
    MAX(0, COALESCE(cumul_cache_read_tokens
             - LAG(cumul_cache_read_tokens) OVER (PARTITION BY agent_id ORDER BY id),
             cumul_cache_read_tokens)) AS delta_cache_read,
    MAX(0, COALESCE(cumul_cache_creation_tokens
             - LAG(cumul_cache_creation_tokens) OVER (PARTITION BY agent_id ORDER BY id),
             cumul_cache_creation_tokens)) AS delta_cache_creation
  FROM token_snapshots
)"""


def query_data(project_root: str, params: dict) -> dict:
    """
    Standard plugin query entry point.

    Called by Launcher's dynamic ``_handle_get_plugin_data`` with:
        project_root  - absolute path to project root
        params        - flat dict of query-string params (values are strings)

    Supported params:
        range     - 1h / 6h / 24h / 7d / 30d / all
        agent_id  - filter to one agent
        metric    - total (default) / cache_read / cache_creation
                    controls SUM(column) used in timeline / by_model / by_agent

    Returns JSON-serializable dict.
    """
    time_range = params.get("range", "24h")
    agent_id = params.get("agent_id") or None
    metric = params.get("metric", "total")
    if metric not in _METRIC_DELTA_COLS:
        metric = "total"

    # Resolve DB path: use workspace, not install dir
    # Launcher passes PROJECT_ROOT which is often the install dir (deploy_test/src/).
    # Agent writes data to the workspace (runtime_deploy/). Detect and correct.
    ws_root = os.environ.get("OPENSQUAD_WORKSPACE", "")
    actual_root = ws_root if ws_root and os.path.isdir(ws_root) else project_root

    db_rel = "data/plugins/token_analytics/analytics.db"
    config_path = os.path.join(actual_root, "data", "plugins",
                               "token_analytics", "config.json")
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            db_rel = cfg.get("db_path", db_rel)
        except Exception:
            pass

    db_path = os.path.join(actual_root, db_rel)
    return query_dashboard(db_path, time_range=time_range, agent_id=agent_id,
                            metric=metric)


# ── Internal helpers ──

def _connect(db_path: str) -> Optional[sqlite3.Connection]:
    """Open a read-only connection to the analytics DB."""
    if not os.path.isfile(db_path):
        return None
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA query_only=ON")
    return conn


def _range_to_cutoff(time_range: str) -> str:
    """Convert a range string like '24h', '7d', '30d' to an ISO timestamp."""
    now = datetime.now(timezone.utc)
    if time_range == "1h":
        cutoff = now - timedelta(hours=1)
    elif time_range == "6h":
        cutoff = now - timedelta(hours=6)
    elif time_range == "24h":
        cutoff = now - timedelta(hours=24)
    elif time_range == "7d":
        cutoff = now - timedelta(days=7)
    elif time_range == "30d":
        cutoff = now - timedelta(days=30)
    elif time_range == "all":
        return "1970-01-01T00:00:00+00:00"
    else:
        # default 24h
        cutoff = now - timedelta(hours=24)
    return cutoff.isoformat()


def query_dashboard(db_path: str, time_range: str = "24h",
                    agent_id: Optional[str] = None,
                    metric: str = "total") -> Dict[str, Any]:
    """
    Main dashboard query. Returns all data needed for the Token Analytics
    dashboard in a single call.

    Args:
        metric: which DB column to SUM for chartable fields. One of
                'total' (default), 'cache_read', 'cache_creation'.

    Returns:
        {
            "metric": "total",
            "summary": {total_tokens, total_requests, total_input, total_output,
                        unique_models, unique_agents, total_cache_read,
                        total_cache_creation},
            "timeline": [{bucket, tokens, requests, label}, ...],   # metric-aware
            "timeline_by_model": [{bucket, by_model: {model: tokens}}, ...],  # daily, metric-aware
            "by_model": [{model, tokens, requests, cache_read_tokens,
                          cache_creation_tokens}, ...],   # primary field metric-aware
            "by_agent": [{agent_id, tokens, requests, cache_read_tokens,
                          cache_creation_tokens}, ...],
            "top_tools": [...],                  # metric-agnostic
            "recent_snapshots": [...],           # raw rows
            "meta": {db_path, time_range, cutoff, query_time_ms, metric}
        }
    """
    import time as _time
    t0 = _time.monotonic()
    if metric not in _METRIC_DELTA_COLS:
        metric = "total"

    conn = _connect(db_path)
    if conn is None:
        return {
            "metric": metric,
            "summary": _empty_summary(),
            "timeline": [],
            "timeline_by_model": [],
            "by_model": [],
            "by_agent": [],
            "top_tools": [],
            "recent_snapshots": [],
            "meta": {"db_path": db_path, "time_range": time_range,
                     "cutoff": "", "query_time_ms": 0, "error": "DB not found",
                     "metric": metric},
        }

    cutoff = _range_to_cutoff(time_range)
    agent_filter = ""
    params: List[Any] = [cutoff]
    if agent_id:
        agent_filter = " AND agent_id = ?"
        params.append(agent_id)

    try:
        summary = _query_summary(conn, cutoff, agent_filter, params, time_range)
        timeline = _query_timeline(conn, cutoff, agent_filter, params, time_range, metric)
        timeline_by_model = _query_timeline_by_model(conn, cutoff, agent_filter, params, time_range, metric)
        by_model = _query_by_model(conn, cutoff, agent_filter, params, time_range, metric)
        by_agent = _query_by_agent(conn, cutoff, agent_filter, params, time_range, metric)
        top_tools = _query_top_tools(conn, cutoff, agent_filter, params)
        recent = _query_recent_snapshots(conn, cutoff, agent_filter, params)
    finally:
        conn.close()

    elapsed_ms = round((_time.monotonic() - t0) * 1000, 1)

    return {
        "metric": metric,
        "summary": summary,
        "timeline": timeline,
        "timeline_by_model": timeline_by_model,
        "by_model": by_model,
        "by_agent": by_agent,
        "top_tools": top_tools,
        "recent_snapshots": recent,
        "meta": {
            "db_path": db_path,
            "time_range": time_range,
            "cutoff": cutoff,
            "query_time_ms": elapsed_ms,
            "metric": metric,
        },
    }


def _empty_summary() -> Dict[str, Any]:
    return {
        "total_tokens": 0,
        "total_requests": 0,
        "total_input": 0,
        "total_output": 0,
        "unique_models": 0,
        "unique_agents": 0,
        "total_cache_read": 0,
        "total_cache_creation": 0,
    }


def _query_summary(conn: sqlite3.Connection, cutoff: str,
                   agent_filter: str, params: List,
                   time_range: str) -> Dict[str, Any]:
    """Aggregate summary stats from token_snapshots.

    All token columns come from SUM(delta_*) so each call is counted once,
    even though the stored columns are running totals per agent.
    """
    bucket_expr, _ = _bucket_expr(time_range)
    deltas_cte = _DELTAS_CTE_TEMPLATE.format(bucket_expr=bucket_expr)
    sql = f"""
        WITH {deltas_cte}
        SELECT
            COALESCE(SUM(delta_total), 0) AS total_tokens,
            COUNT(*) AS total_requests,
            COALESCE(SUM(delta_input), 0) AS total_input,
            COALESCE(SUM(delta_output), 0) AS total_output,
            COUNT(DISTINCT model) AS unique_models,
            COUNT(DISTINCT agent_id) AS unique_agents,
            COALESCE(SUM(delta_cache_read), 0) AS total_cache_read,
            COALESCE(SUM(delta_cache_creation), 0) AS total_cache_creation
        FROM deltas
        WHERE timestamp >= ?{agent_filter}
    """
    row = conn.execute(sql, params).fetchone()
    if not row:
        return _empty_summary()
    return {
        "total_tokens": row["total_tokens"],
        "total_requests": row["total_requests"],
        "total_input": row["total_input"],
        "total_output": row["total_output"],
        "unique_models": row["unique_models"],
        "unique_agents": row["unique_agents"],
        "total_cache_read": row["total_cache_read"],
        "total_cache_creation": row["total_cache_creation"],
    }


def _query_timeline(conn: sqlite3.Connection, cutoff: str,
                    agent_filter: str, params: List,
                    time_range: str, metric: str = "total") -> List[Dict[str, Any]]:
    """Per-bucket totals + request counts for a simple line/bar chart."""
    bucket_expr, bucket_label = _bucket_expr(time_range)
    deltas_cte = _DELTAS_CTE_TEMPLATE.format(bucket_expr=bucket_expr)
    delta_col = _metric_delta_col(metric)
    sql = f"""
        WITH {deltas_cte}
        SELECT
            bucket,
            COALESCE(SUM({delta_col}), 0) AS tokens,
            COUNT(*) AS requests
        FROM deltas
        WHERE timestamp >= ?{agent_filter}
        GROUP BY bucket
        ORDER BY bucket ASC
        LIMIT 500
    """
    rows = conn.execute(sql, params).fetchall()
    return [{"bucket": r["bucket"], "tokens": r["tokens"],
             "requests": r["requests"], "label": bucket_label} for r in rows]


def _query_timeline_by_model(conn: sqlite3.Connection, cutoff: str,
                              agent_filter: str, params: List,
                              time_range: str, metric: str = "total") -> List[Dict[str, Any]]:
    """
    Per-bucket × per-model token breakdown — drives the stacked bar chart.

    Bucket size adapts to time_range (10-min / hourly / daily) so the chart
    stays informative at any zoom level. Each cell is SUM(delta_*) over the
    per-call increments.
    """
    bucket_expr, _ = _bucket_expr(time_range)
    deltas_cte = _DELTAS_CTE_TEMPLATE.format(bucket_expr=bucket_expr)
    delta_col = _metric_delta_col(metric)
    sql = f"""
        WITH {deltas_cte}
        SELECT
            bucket,
            model,
            COALESCE(SUM({delta_col}), 0) AS tokens
        FROM deltas
        WHERE timestamp >= ?{agent_filter}
        GROUP BY bucket, model
        ORDER BY bucket ASC, tokens DESC
    """
    rows = conn.execute(sql, params).fetchall()

    bucket_map: Dict[str, Dict[str, int]] = {}
    bucket_order: List[str] = []
    for r in rows:
        b = r["bucket"]
        if b not in bucket_map:
            bucket_map[b] = {}
            bucket_order.append(b)
        bucket_map[b][r["model"]] = r["tokens"]

    return [{"bucket": b, "by_model": bucket_map[b]} for b in bucket_order]


def _query_by_model(conn: sqlite3.Connection, cutoff: str,
                    agent_filter: str, params: List,
                    time_range: str, metric: str = "total") -> List[Dict[str, Any]]:
    """Per-model totals (metric-aware) + cache fields (absolute)."""
    bucket_expr, _ = _bucket_expr(time_range)
    deltas_cte = _DELTAS_CTE_TEMPLATE.format(bucket_expr=bucket_expr)
    delta_col = _metric_delta_col(metric)
    sql = f"""
        WITH {deltas_cte}
        SELECT
            model,
            COALESCE(SUM({delta_col}), 0) AS tokens,
            COUNT(*) AS requests,
            COALESCE(SUM(delta_cache_read), 0) AS cache_read_tokens,
            COALESCE(SUM(delta_cache_creation), 0) AS cache_creation_tokens
        FROM deltas
        WHERE timestamp >= ?{agent_filter}
        GROUP BY model
        ORDER BY tokens DESC
        LIMIT 20
    """
    rows = conn.execute(sql, params).fetchall()
    return [{
        "model": r["model"],
        "tokens": r["tokens"],
        "requests": r["requests"],
        "cache_read_tokens": r["cache_read_tokens"],
        "cache_creation_tokens": r["cache_creation_tokens"],
    } for r in rows]


def _query_by_agent(conn: sqlite3.Connection, cutoff: str,
                    agent_filter: str, params: List,
                    time_range: str, metric: str = "total") -> List[Dict[str, Any]]:
    """Per-agent totals (metric-aware) + cache fields (absolute)."""
    bucket_expr, _ = _bucket_expr(time_range)
    deltas_cte = _DELTAS_CTE_TEMPLATE.format(bucket_expr=bucket_expr)
    delta_col = _metric_delta_col(metric)
    sql = f"""
        WITH {deltas_cte}
        SELECT
            agent_id,
            COALESCE(SUM({delta_col}), 0) AS tokens,
            COUNT(*) AS requests,
            COALESCE(SUM(delta_cache_read), 0) AS cache_read_tokens,
            COALESCE(SUM(delta_cache_creation), 0) AS cache_creation_tokens
        FROM deltas
        WHERE timestamp >= ?{agent_filter}
        GROUP BY agent_id
        ORDER BY tokens DESC
        LIMIT 20
    """
    rows = conn.execute(sql, params).fetchall()
    return [{
        "agent_id": r["agent_id"],
        "tokens": r["tokens"],
        "requests": r["requests"],
        "cache_read_tokens": r["cache_read_tokens"],
        "cache_creation_tokens": r["cache_creation_tokens"],
    } for r in rows]


def _query_top_tools(conn: sqlite3.Connection, cutoff: str,
                     agent_filter: str, params: List) -> List[Dict[str, Any]]:
    """Top tools by call count from tool_usage table."""
    sql = f"""
        SELECT
            tool_name,
            COUNT(*) AS call_count,
            COALESCE(SUM(args_tokens_est), 0) AS total_args_tokens,
            COALESCE(SUM(result_tokens_est), 0) AS total_result_tokens
        FROM tool_usage
        WHERE timestamp >= ?{agent_filter}
        GROUP BY tool_name
        ORDER BY call_count DESC
        LIMIT 20
    """
    rows = conn.execute(sql, params).fetchall()
    return [{"tool_name": r["tool_name"], "call_count": r["call_count"],
             "total_args_tokens": r["total_args_tokens"],
             "total_result_tokens": r["total_result_tokens"]} for r in rows]


def _query_recent_snapshots(conn: sqlite3.Connection, cutoff: str,
                            agent_filter: str,
                            params: List) -> List[Dict[str, Any]]:
    """Most recent snapshot records (for a detail table)."""
    sql = f"""
        SELECT
            timestamp, agent_id, model, session_id,
            window_used, window_max,
            breakdown_user, breakdown_thought, breakdown_tool, breakdown_tool_defs, breakdown_response,
            cumul_input_tokens, cumul_output_tokens, cumul_total_tokens, cumul_requests
        FROM token_snapshots
        WHERE timestamp >= ?{agent_filter}
        ORDER BY timestamp DESC
        LIMIT 50
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
