# -*- coding: utf-8 -*-
"""
Long Memory - Query Module

Reads/writes the agent's long_memory SQLite database for the
frontend memory management panel. Designed to be called from the
Launcher HTTP handler.

Standard entry points (called by Launcher's dynamic routing):
    query_data(project_root: str, params: dict) -> dict
    handle_action(project_root: str, action: str, data: dict) -> dict
"""
import os
import json
import sqlite3
import time
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Helpers ──


def _get_last_workspace() -> str:
    """Read last_workspace.json for fallback agent scanning."""
    try:
        lw_path = os.path.join(os.path.expanduser("~"), ".opensquad", "last_workspace.json")
        if os.path.isfile(lw_path):
            with open(lw_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ws = data.get("last_workspace", "")
            if ws and os.path.isdir(os.path.join(ws, "agents")):
                return ws
    except Exception:
        pass
    return ""


def _scan_agents(root: str) -> List[Dict[str, str]]:
    """Scan a single root's agents/ directory."""
    agents_root = os.path.join(root, "agents")
    result = []
    if not os.path.isdir(agents_root):
        return result
    for name in sorted(os.listdir(agents_root)):
        cfg_path = os.path.join(agents_root, name, "config.json")
        if not os.path.isfile(cfg_path):
            continue
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            result.append({
                "agent_id": cfg.get("agent_id", name),
                "agent_name": cfg.get("agent_name", name),
                "dir_name": name,
                "root": root,
            })
        except Exception:
            continue
    return result


def _find_agent_dir(project_root: str, agent_id: str) -> Optional[str]:
    """Resolve agent_id to its directory. Checks project_root first, then last_workspace."""
    # Check primary workspace
    agents_root = os.path.join(project_root, "agents")
    if os.path.isdir(agents_root):
        for name in os.listdir(agents_root):
            cfg_path = os.path.join(agents_root, name, "config.json")
            if not os.path.isfile(cfg_path):
                continue
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if cfg.get("agent_id") == agent_id:
                    return os.path.join(agents_root, name)
            except Exception:
                continue

    # Fallback: check last_workspace
    alt_ws = _get_last_workspace()
    if alt_ws and alt_ws != project_root:
        alt_root = os.path.join(alt_ws, "agents")
        if os.path.isdir(alt_root):
            for name in os.listdir(alt_root):
                cfg_path = os.path.join(alt_root, name, "config.json")
                if not os.path.isfile(cfg_path):
                    continue
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    if cfg.get("agent_id") == agent_id:
                        return os.path.join(alt_root, name)
                except Exception:
                    continue

    return None


def _list_agents(project_root: str) -> List[Dict[str, str]]:
    """List all agents across current workspace and last_workspace fallback."""
    seen_ids: set = set()
    result: List[Dict[str, str]] = []

    # Primary workspace
    for a in _scan_agents(project_root):
        if a["agent_id"] not in seen_ids:
            seen_ids.add(a["agent_id"])
            a["workspace"] = project_root
            result.append(a)

    # Fallback: last_workspace
    alt_ws = _get_last_workspace()
    if alt_ws and alt_ws != project_root:
        for a in _scan_agents(alt_ws):
            if a["agent_id"] not in seen_ids:
                seen_ids.add(a["agent_id"])
                a["workspace"] = alt_ws
                result.append(a)

    return result


def _open_db(db_path: str, read_only: bool = True) -> Optional[sqlite3.Connection]:
    """Open SQLite connection with appropriate pragma."""
    if not os.path.isfile(db_path):
        return None
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    if read_only:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict with parsed keywords."""
    d = dict(row)
    # Parse keywords_json if present
    kw = d.get("keywords_json")
    if isinstance(kw, str):
        try:
            d["keywords"] = json.loads(kw)
        except Exception:
            d["keywords"] = []
    else:
        d["keywords"] = []
    # Format timestamp as ISO string
    ts = d.get("timestamp")
    if isinstance(ts, (int, float)) and ts > 0:
        d["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    else:
        d["created_at"] = ""
    return d


# ── Standard entry point (query data for frontend) ──


def query_data(project_root: str, params: dict) -> dict:
    """
    Standard plugin query entry point.

    params:
        agent_id (str, required)  – target agent
        search  (str, optional)   – filter by topic/summary/body
        sort    (str, optional)   – "date_desc" (default), "date_asc", "importance"
        type    (str, optional)   – filter by entry_type
        category (str, optional)  – filter by category
        limit   (int, optional)   – max rows (default 200)
        offset  (int, optional)   – pagination offset (default 0)

    Returns:
        {"agents": [...], "memories": [...], "total": N, "meta": {...}}
    """
    t0 = time.monotonic()
    agent_id = params.get("agent_id", "").strip()

    # Always include agent list (for the dropdown selector)
    agents = _list_agents(project_root)

    if not agent_id:
        return {
            "agents": agents,
            "memories": [],
            "total": 0,
            "meta": {"error": "agent_id is required", "query_time_ms": 0},
        }

    agent_dir = _find_agent_dir(project_root, agent_id)
    if not agent_dir:
        return {
            "agents": agents,
            "memories": [],
            "total": 0,
            "meta": {"error": f"Agent '{agent_id}' not found", "query_time_ms": 0},
        }

    db_path = os.path.join(agent_dir, "data", "long_memory", "memory.db")
    conn = _open_db(db_path, read_only=True)
    if conn is None:
        return {
            "agents": agents,
            "memories": [],
            "total": 0,
            "meta": {"db_path": db_path, "error": "Memory database not found", "query_time_ms": 0},
        }

    try:
        # Build filters
        where_clauses: List[str] = []
        bind_params: List[Any] = []

        search = params.get("search", "").strip()
        if search:
            where_clauses.append("(topic LIKE ? OR summary LIKE ? OR body LIKE ?)")
            like_val = f"%{search}%"
            bind_params.extend([like_val, like_val, like_val])

        entry_type = params.get("type", "").strip()
        if entry_type:
            where_clauses.append("entry_type = ?")
            bind_params.append(entry_type)

        category = params.get("category", "").strip()
        if category:
            where_clauses.append("category = ?")
            bind_params.append(category)

        where_sql = ""
        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)

        # Count total
        count_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM entries{where_sql}", bind_params
        ).fetchone()
        total = count_row["cnt"] if count_row else 0

        # Sort
        sort = params.get("sort", "date_desc")
        order_map = {
            "date_desc": "timestamp DESC",
            "date_asc": "timestamp ASC",
            "importance": "importance DESC, timestamp DESC",
        }
        order_sql = order_map.get(sort, "timestamp DESC")

        # Pagination
        limit = min(int(params.get("limit", 200)), 1000)
        offset = max(int(params.get("offset", 0)), 0)

        rows = conn.execute(
            f"SELECT * FROM entries{where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            bind_params + [limit, offset],
        ).fetchall()

        memories = [_row_to_dict(r) for r in rows]
    finally:
        conn.close()

    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)

    return {
        "agents": agents,
        "memories": memories,
        "total": total,
        "meta": {
            "db_path": db_path,
            "query_time_ms": elapsed_ms,
            "limit": limit,
            "offset": offset,
        },
    }


# ── Standard entry point (actions: delete) ──


def handle_action(project_root: str, action: str, data: dict) -> dict:
    """
    Handle write actions from the frontend.

    Actions:
        "delete"     – delete a single memory entry
        "delete_multi" – delete multiple entries at once
    """
    if action == "delete":
        return _action_delete(project_root, data)
    elif action == "delete_multi":
        return _action_delete_multi(project_root, data)
    else:
        return {"status": "error", "message": f"Unknown action: {action}"}


def _action_delete(project_root: str, data: dict) -> dict:
    """Delete a single memory entry by id."""
    agent_id = (data.get("agent_id") or "").strip()
    entry_id = (data.get("id") or "").strip()

    if not agent_id or not entry_id:
        return {"status": "error", "message": "agent_id and id are required"}

    agent_dir = _find_agent_dir(project_root, agent_id)
    if not agent_dir:
        return {"status": "error", "message": f"Agent '{agent_id}' not found"}

    db_path = os.path.join(agent_dir, "data", "long_memory", "memory.db")
    conn = _open_db(db_path, read_only=False)
    if conn is None:
        return {"status": "error", "message": "Memory database not found"}

    try:
        conn.execute("DELETE FROM keyword_index WHERE entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        conn.commit()
        return {"status": "ok", "message": f"Deleted {entry_id}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


def _action_delete_multi(project_root: str, data: dict) -> dict:
    """Delete multiple memory entries by id list."""
    agent_id = (data.get("agent_id") or "").strip()
    ids = data.get("ids", [])

    if not agent_id or not ids:
        return {"status": "error", "message": "agent_id and ids are required"}

    agent_dir = _find_agent_dir(project_root, agent_id)
    if not agent_dir:
        return {"status": "error", "message": f"Agent '{agent_id}' not found"}

    db_path = os.path.join(agent_dir, "data", "long_memory", "memory.db")
    conn = _open_db(db_path, read_only=False)
    if conn is None:
        return {"status": "error", "message": "Memory database not found"}

    placeholders = ",".join("?" for _ in ids)
    try:
        conn.execute(
            f"DELETE FROM keyword_index WHERE entry_id IN ({placeholders})", ids
        )
        conn.execute(
            f"DELETE FROM entries WHERE id IN ({placeholders})", ids
        )
        conn.commit()
        return {"status": "ok", "message": f"Deleted {len(ids)} entries"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()
