"""
Collaboration board storage.

Key behaviors:
- Every collaboration task has a 6-char alnum task_id (collab_id)
- Board entries are namespaced by task_id
- Only latest tool-call snapshot is kept per (task_id, agent_id, item_type)
- Task list supports history-style browsing with duration/progress stats
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from typing import Any

from opensquad._storage.json_io import atomic_write_json, read_json
from opensquad.distributed_lock import SessionLock
from opensquad.system_config import syscfg

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

# Only replay WAL entries younger than this window. Stale entries left over from
# a previous session must not revive data that was legitimately removed (e.g.
# deleting the last task, which empties the main file).
_WAL_REPLAY_WINDOW = 3600  # seconds


@contextmanager
def _board_lock(timeout: float = 15.0):
    """Hold both an in-process lock and a cross-process file lock.

    ``collab_board`` is shared across multiple agent processes. The in-process
    ``_LOCK`` only serialises threads within one process; a ``SessionLock``
    (OS file lock) is additionally required to prevent lost updates when
    several processes read-modify-write the same JSON files concurrently.

    A single cross-process resource (rather than separate items/tasks locks)
    avoids lock-ordering deadlocks for operations that touch both files
    (e.g. ``delete_task``, ``list_tasks``).
    """
    with _LOCK:
        lock = SessionLock("collab_board", timeout=timeout)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _board_dir() -> str:
    d = syscfg.workspace_data_dir("collab_board")
    os.makedirs(d, exist_ok=True)
    return d


def _items_file() -> str:
    return os.path.join(_board_dir(), "board_items.json")


def _tasks_file() -> str:
    return os.path.join(_board_dir(), "board_tasks.json")


def _read_json(path: str, default):
    """Deprecated: delegates to opensquad._storage.json_io.read_json."""
    return read_json(path, default)


def _write_json(path: str, data) -> None:
    """Deprecated: delegates to opensquad._storage.json_io.atomic_write_json."""
    atomic_write_json(path, data)


def _read_items() -> list[dict[str, Any]]:
    data = _read_json(_items_file(), [])
    return data if isinstance(data, list) else []


def _write_items(items: list[dict[str, Any]]) -> None:
    wal_file = _wal_append("write_items", {"items": items})
    _write_json(_items_file(), items)
    # Main file committed successfully — the WAL entry has served its purpose.
    _wal_remove(wal_file)


def _read_tasks() -> list[dict[str, Any]]:
    data = _read_json(_tasks_file(), [])
    return data if isinstance(data, list) else []


def _write_tasks(tasks: list[dict[str, Any]]) -> None:
    wal_file = _wal_append("write_tasks", {"tasks": tasks})
    _write_json(_tasks_file(), tasks)
    # Main file committed successfully — the WAL entry has served its purpose.
    _wal_remove(wal_file)


# ---------------------------------------------------------------------------
# Write-Ahead Log (WAL) for crash safety
# ---------------------------------------------------------------------------
_WAL_LOCK = threading.Lock()
_WAL_DIR_CACHE = None


def _wal_dir() -> str:
    global _WAL_DIR_CACHE
    if _WAL_DIR_CACHE is None:
        _WAL_DIR_CACHE = os.path.join(_board_dir(), "wal")
        os.makedirs(_WAL_DIR_CACHE, exist_ok=True)
    return _WAL_DIR_CACHE


def _wal_append(op_type: str, data: dict) -> str | None:
    """Append an operation to the WAL before performing the actual write.

    Returns the WAL filename so the caller can remove it once the main file
    has been committed, preventing unbounded WAL growth and stale-data revival.
    """
    with _WAL_LOCK:
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%d_%H%M%S_%f")
        wal_entry = {
            "op": op_type,
            "timestamp": now.isoformat().replace("+00:00", "Z"),
            "data": data,
        }
        wal_file = os.path.join(_wal_dir(), f"{ts}_{os.urandom(4).hex()}.wal")
        try:
            fd, tmp = tempfile.mkstemp(suffix=".tmp", prefix=".wal_", dir=_wal_dir())
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(wal_entry, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, wal_file)
        except Exception as e:
            logger.warning(f"[WAL] Failed to write WAL entry: {e}")
            return None
        return wal_file


def _wal_remove(wal_file: str | None) -> None:
    """Remove a committed WAL entry. Silently ignores missing/None."""
    if not wal_file:
        return
    with suppress(OSError):
        os.remove(wal_file)


def _wal_replay() -> None:
    """Replay WAL entries on module init to recover any lost writes.

    Safety rules:
    - Only consider WAL entries from the last ``_WAL_REPLAY_WINDOW`` seconds, so
      that a legitimately emptied main file (e.g. deleting the last task) is not
      overwritten by stale WAL snapshots from a previous session.
    - Apply the most recent items/tasks snapshot (later mtime wins) so the
      recovered state reflects the latest committed intent.
    - Always clean up the WAL directory afterwards, whether or not a replay
      happened, to prevent unbounded growth.
    """
    wdir = _wal_dir()
    if not os.path.isdir(wdir):
        return
    wal_files = sorted(f for f in os.listdir(wdir) if f.endswith(".wal"))
    if not wal_files:
        return

    now_ts = datetime.now(timezone.utc).timestamp()
    items_recovered = False
    tasks_recovered = False
    replayable_items = None
    replayable_tasks = None

    for fname in wal_files:
        fpath = os.path.join(wdir, fname)
        # Age guard: ignore stale entries left over from a prior session.
        try:
            if (now_ts - os.path.getmtime(fpath)) > _WAL_REPLAY_WINDOW:
                continue
        except OSError:
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                entry = json.load(f)
        except Exception:
            continue

        op = entry.get("op", "")
        data = entry.get("data", {})
        # Take the latest snapshot for each kind (files are sorted by name,
        # which embeds a microsecond timestamp, so later == newer).
        if op == "write_items" and isinstance(data.get("items"), list):
            current = _read_json(_items_file(), [])
            if not current:
                replayable_items = data["items"]
                items_recovered = True
        elif op == "write_tasks" and isinstance(data.get("tasks"), list):
            current = _read_json(_tasks_file(), [])
            if not current:
                replayable_tasks = data["tasks"]
                tasks_recovered = True

    if items_recovered and replayable_items is not None:
        _write_json(_items_file(), replayable_items)
    if tasks_recovered and replayable_tasks is not None:
        _write_json(_tasks_file(), replayable_tasks)

    if items_recovered or tasks_recovered:
        logger.info(f"[WAL] Replay complete: items_recovered={items_recovered}, tasks_recovered={tasks_recovered}")

    # Always clean the WAL dir on a successful startup so committed entries
    # (whose main file was already written) do not accumulate forever.
    for fname in wal_files:
        with suppress(OSError):
            os.remove(os.path.join(wdir, fname))


def _gen_task_id(existing: set[str]) -> str:
    """Generate a unique task ID using a UUID4 hex prefix.

    Retries a few times in the unlikely event of a collision with an
    existing ID, so callers never need to handle the collision themselves.
    """
    for _ in range(8):
        cid = uuid.uuid4().hex[:6].upper()
        if cid not in existing:
            return cid
    # Vanishingly unlikely — fall back to a longer prefix.
    return uuid.uuid4().hex[:8].upper()


def create_task(
    *, task_name: str, created_by: str, task_id: str | None = None, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    with _board_lock():
        tasks = _read_tasks()
        existing = {str(t.get("task_id", "")) for t in tasks}
        cid = task_id or _gen_task_id(existing)
        if cid in existing:
            raise ValueError(f"task_id already exists: {cid}")
        now = _now_iso()
        rec = {
            "task_id": cid,
            "task_name": task_name or cid,
            "created_by": created_by,
            "members": [created_by] if created_by else [],
            "status": "active",
            "progress": 0,
            "board_rev": 0,
            "created_at": now,
            "started_at": now,
            "updated_at": now,
            "closed_at": None,
            "ended_at": None,
            "extra": metadata if isinstance(metadata, dict) else {},
        }
        tasks.append(rec)
        _write_tasks(tasks)
        return rec


def update_task(
    *,
    task_id: str,
    progress: int | None = None,
    task_name: str | None = None,
    status: str | None = None,
    add_member: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _board_lock():
        tasks = _read_tasks()
        idx = next((i for i, t in enumerate(tasks) if str(t.get("task_id", "")) == task_id), -1)
        if idx < 0:
            raise ValueError(f"task_id not found: {task_id}")
        rec = dict(tasks[idx])
        if progress is not None:
            rec["progress"] = max(0, min(100, int(progress)))
        if task_name is not None and task_name.strip():
            rec["task_name"] = task_name.strip()
        if status is not None and status in ("active", "done", "failed", "archived", "stale"):
            rec["status"] = status
            if status in ("done", "failed", "archived"):
                if not rec.get("closed_at"):
                    rec["closed_at"] = _now_iso()
                if not rec.get("ended_at"):
                    rec["ended_at"] = rec.get("closed_at") or _now_iso()
        if add_member:
            members = rec.get("members")
            if not isinstance(members, list):
                members = []
            if add_member not in members:
                members.append(add_member)
            rec["members"] = members
        if extra is not None:
            current_extra = rec.get("extra", {})
            if not isinstance(current_extra, dict):
                current_extra = {}
            current_extra.update(extra)
            rec["extra"] = current_extra
        rec["updated_at"] = _now_iso()
        # Bump board_rev on meaningful task metadata changes
        if any(x is not None for x in (progress, task_name, status, add_member, extra)):
            rec["board_rev"] = int(rec.get("board_rev") or 0) + 1
        tasks[idx] = rec
        _write_tasks(tasks)
        new_rev = int(rec.get("board_rev") or 0)

    if any(x is not None for x in (progress, task_name, status, add_member, extra)):
        _notify_board_changed(
            task_id,
            board_rev=new_rev,
            reason="update_task",
            item_type="task_meta",
            item_key="",
            actor_id=str(add_member or ""),
        )
    return rec


def _parse_iso(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        # Support trailing Z
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def list_tasks(*, include_stale: bool = False) -> list[dict[str, Any]]:
    with _board_lock():
        tasks = _read_tasks()
        items = _read_items()

    if not include_stale:
        tasks = [t for t in tasks if t.get("status") != "stale"]

    # enrich stats
    by_task: dict[str, list[dict[str, Any]]] = {}
    for i in items:
        tid = str(i.get("collab_id", ""))
        if not tid:
            continue
        by_task.setdefault(tid, []).append(i)

    now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    out = []
    for t in tasks:
        tid = str(t.get("task_id", ""))
        lst = by_task.get(tid, [])
        member_set = {str(x.get("agent_id", "")) for x in lst if x.get("agent_id")}

        started_at = t.get("started_at") or t.get("created_at")
        ended_at = t.get("ended_at") or t.get("closed_at")
        s_dt = _parse_iso(started_at)
        e_dt = _parse_iso(ended_at)
        if s_dt is not None:
            if e_dt is None:
                duration_sec = max(0, int((now_dt - s_dt.replace(tzinfo=None)).total_seconds()))
            else:
                duration_sec = max(0, int((e_dt.replace(tzinfo=None) - s_dt.replace(tzinfo=None)).total_seconds()))
        else:
            duration_sec = 0

        task_members = t.get("members") if isinstance(t.get("members"), list) else []
        merged_members = set(task_members) | member_set
        out.append(
            {
                **t,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_seconds": duration_sec,
                "members": list(merged_members),
                "member_count": len(merged_members),
                "item_count": len(lst),
            }
        )
    out.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
    return out


def _match_identity(item: dict[str, Any], collab_id: str, agent_id: str, item_type: str, item_key: str = "") -> bool:
    """Match item identity for upsert. If item_key is provided, it is also matched."""
    return (
        str(item.get("collab_id", "")) == str(collab_id)
        and str(item.get("agent_id", "")) == str(agent_id)
        and str(item.get("item_type", "")) == str(item_type)
        and str(item.get("item_key", "")) == str(item_key)
    )


def _derive_task_status_progress_from_content(content: str) -> tuple[str | None, int | None]:
    """Derive task status/progress from checklist markers in content.

    Supported markers (matched only at the start of a line, after up to 3
    spaces of indentation, and not inside fenced code blocks):
    - [x] completed
    - [>] in progress
    - [ ] pending/blocked
    """
    if not isinstance(content, str) or not content.strip():
        return None, None

    done = 0
    doing = 0
    pending = 0
    in_code_fence = False

    for raw in content.splitlines():
        stripped = raw.lstrip()
        # Toggle fenced code block on ``` markers; skip checkbox detection
        # inside code blocks (where [x] is just literal text).
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue
        # Only count checkboxes at the very start of the (left-stripped) line
        # so that inline references like "`[x]`" in prose are not counted.
        if len(raw) - len(stripped) > 3:
            continue
        line = stripped.lower()
        if line.startswith("[x]"):
            done += 1
        elif line.startswith("[>]"):
            doing += 1
        elif line.startswith("[ ]"):
            pending += 1

    total = done + doing + pending
    if total <= 0:
        return None, None

    # [>] counts as half progress for aggregate percentage.
    progress = round(((done + doing * 0.5) / total) * 100)

    if doing > 0:
        status = "doing"
    elif done == total:
        status = "done"
    else:
        status = "pending"

    return status, progress


def upsert_item(
    *,
    collab_id: str,
    agent_id: str,
    item_type: str,
    title: str = "",
    content: str = "",
    status: str = "doing",
    progress: int = 0,
    visibility: str = "public",
    latest_tool_name: str | None = None,
    latest_tool_summary: str | None = None,
    task_name: str = "",
    item_key: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not collab_id:
        raise ValueError("collab_id(task_id) is required")
    # Unified assignment-progress logic:
    # for task items, checklist markers in content are the source of truth.
    derived_status = None
    derived_progress = None
    if item_type == "task":
        derived_status, derived_progress = _derive_task_status_progress_from_content(content)

    final_status = derived_status or status
    final_progress = derived_progress if derived_progress is not None else max(0, min(100, int(progress or 0)))

    with _board_lock():
        items = _read_items()
        now = _now_iso()
        idx = next((i for i, x in enumerate(items) if _match_identity(x, collab_id, agent_id, item_type, item_key)), -1)

        base = {
            "collab_id": collab_id,
            "task_id": collab_id,
            "task_name": task_name or collab_id,
            "agent_id": agent_id,
            "item_type": item_type,
            "item_key": item_key,
            "title": title,
            "content": content,
            "status": final_status,
            "progress": final_progress,
            "visibility": visibility if visibility in ("public", "private") else "public",
            "latest_tool_name": latest_tool_name,
            "latest_tool_summary": latest_tool_summary,
            "extra": extra if isinstance(extra, dict) else {},
            "updated_at": now,
        }

        if idx >= 0:
            old = items[idx]
            base["id"] = old.get("id") or f"{collab_id}:{agent_id}:{item_type}:{item_key or 'default'}"
            base["created_at"] = old.get("created_at", now)
            if latest_tool_name is None:
                base["latest_tool_name"] = old.get("latest_tool_name")
            if latest_tool_summary is None:
                base["latest_tool_summary"] = old.get("latest_tool_summary")
            if not task_name:
                base["task_name"] = old.get("task_name") or collab_id
            if not item_key:
                base["item_key"] = old.get("item_key", "")
            if not isinstance(extra, dict):
                base["extra"] = old.get("extra") if isinstance(old.get("extra"), dict) else {}
            # Auto-snapshot before overwriting when content actually changes
            _old_content = str(old.get("content", ""))
            _new_content = str(content or "")
            if _old_content and _old_content != _new_content and item_type not in ("status", "discussion"):
                _zone_map = {
                    "requirement": "requirement",
                    "requirement_doc": "requirement",
                    "plan": "plan",
                }
                _zone = _zone_map.get(item_type, "status")
                # Skip trivial auto-sync noise for non-document zones
                if _zone in ("requirement", "plan") or len(_old_content) > 20:
                    save_snapshot(
                        collab_id=collab_id,
                        zone=_zone,
                        content=_old_content,
                        title=str(old.get("title", "")),
                        author_agent_id=str(old.get("agent_id", "")),
                        item_key=item_key or str(old.get("item_key", "")),
                    )
            items[idx] = base
        else:
            base["id"] = f"{collab_id}:{agent_id}:{item_type}:{item_key or 'default'}"
            base["created_at"] = now
            items.append(base)

        _write_items(items)

        # Bump board_rev for meaningful item types (skip noisy auto status sync)
        new_rev = 0
        should_notify = item_type in (
            "requirement",
            "requirement_doc",
            "plan",
            "task",
            "change_request",
            "approval",
            "discussion",
        )
        if should_notify:
            tasks = _read_tasks()
            tidx = next((i for i, t in enumerate(tasks) if str(t.get("task_id", "")) == str(collab_id)), -1)
            if tidx >= 0:
                trec = dict(tasks[tidx])
                new_rev = int(trec.get("board_rev") or 0) + 1
                trec["board_rev"] = new_rev
                trec["updated_at"] = now
                tasks[tidx] = trec
                _write_tasks(tasks)

    if should_notify and new_rev:
        _notify_board_changed(
            collab_id,
            board_rev=new_rev,
            reason="upsert_item",
            item_type=item_type,
            item_key=item_key or "",
            actor_id=agent_id,
        )
        base = dict(base)
        base["board_rev"] = new_rev
    return base


def list_items(*, collab_id: str, agent_id: str | None = None, visibility: str = "public") -> list[dict[str, Any]]:
    if not collab_id:
        raise ValueError("collab_id(task_id) is required")
    with _board_lock():
        items = _read_items()

    out = []
    for x in items:
        if str(x.get("collab_id", "")) != str(collab_id):
            continue
        if agent_id and str(x.get("agent_id", "")) != str(agent_id):
            continue
        if visibility == "public" and x.get("visibility") != "public":
            continue
        out.append(x)

    out.sort(key=lambda i: i.get("updated_at", ""), reverse=True)
    return out


def append_public_discussion(
    *, collab_id: str, task_name: str, author_agent_id: str, title: str, content: str
) -> dict[str, Any]:
    if not collab_id:
        raise ValueError("collab_id(task_id) is required")
    with _board_lock():
        items = _read_items()
        now = _now_iso()
        rec = {
            "id": f"discussion:{collab_id}:{author_agent_id}:{int(datetime.now(timezone.utc).timestamp() * 1000)}:{uuid.uuid4().hex[:6]}",
            "collab_id": collab_id,
            "task_id": collab_id,
            "task_name": task_name or collab_id,
            "agent_id": author_agent_id,
            "item_type": "discussion",
            "title": title or "Public discussion",
            "content": content or "",
            "status": "info",
            "progress": 0,
            "visibility": "public",
            "latest_tool_name": None,
            "latest_tool_summary": None,
            "created_at": now,
            "updated_at": now,
        }
        items.append(rec)
        _write_items(items)
        return rec


def update_latest_tool(
    *, collab_id: str, agent_id: str, tool_name: str, tool_result: Any, task_name: str = ""
) -> dict[str, Any]:
    summary = str(tool_result)
    if len(summary) > 300:
        summary = summary[:300] + "..."
    return upsert_item(
        collab_id=collab_id,
        task_name=task_name,
        agent_id=agent_id,
        item_type="status",
        title="Current status",
        content="Auto-updated from latest tool call",
        status="doing",
        progress=0,
        visibility="public",
        latest_tool_name=tool_name,
        latest_tool_summary=summary,
    )


def delete_item(*, item_id: str) -> bool:
    """Delete a board item by its unique id. Returns True if deleted, False if not found."""
    if not item_id:
        raise ValueError("item_id is required")
    with _board_lock():
        items = _read_items()
        idx = next((i for i, x in enumerate(items) if str(x.get("id", "")) == str(item_id)), -1)
        if idx < 0:
            return False
        items.pop(idx)
        _write_items(items)
        return True


def delete_task(*, task_id: str) -> dict[str, Any]:
    """Delete a collaboration task and all its associated board items.

    Removes:
    1. The task record from board_tasks.json
    2. All board items whose collab_id matches task_id

    Returns a summary of what was deleted.
    """
    if not task_id:
        raise ValueError("task_id is required")
    with _board_lock():
        # Remove task record
        tasks = _read_tasks()
        idx = next((i for i, t in enumerate(tasks) if str(t.get("task_id", "")) == task_id), -1)
        if idx < 0:
            return {"deleted": False, "reason": f"Task '{task_id}' not found"}
        tasks.pop(idx)
        _write_tasks(tasks)

        # Remove all associated board items
        items = _read_items()
        remaining = [x for x in items if str(x.get("collab_id", "")) != task_id]
        removed_count = len(items) - len(remaining)
        if removed_count > 0:
            _write_items(remaining)

    return {
        "deleted": True,
        "task_id": task_id,
        "items_removed": removed_count,
    }


# ---- Plan history ----

_PLAN_HISTORY_DIR = os.path.join(_board_dir(), "plan_history")


def _plan_history_dir(collab_id: str) -> str:
    d = os.path.join(_PLAN_HISTORY_DIR, collab_id)
    os.makedirs(d, exist_ok=True)
    return d


def save_plan_snapshot(*, collab_id: str, content: str, title: str = "", author_agent_id: str = "") -> dict[str, Any]:
    """Save current plan content as a snapshot before overwriting. Returns snapshot metadata."""
    if not collab_id:
        raise ValueError("collab_id is required")
    os.makedirs(_plan_history_dir(collab_id), exist_ok=True)
    now = _now_iso()
    # Microsecond precision + random suffix so two snapshots saved within the
    # same second never overwrite each other.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{ts}.md"
    filepath = os.path.join(_plan_history_dir(collab_id), filename)
    # Atomic write: write to a temp file then os.replace, so a crash never
    # leaves a half-written snapshot that readers would see.
    fd, tmp = tempfile.mkstemp(suffix=".tmp", prefix=".plan_", dir=_plan_history_dir(collab_id))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, filepath)
    except Exception:
        with suppress(OSError):
            os.remove(tmp)
        raise
    # Also write to unified snapshot store (same path upsert_item uses for Agent updates).
    save_snapshot(
        collab_id=collab_id,
        zone="plan",
        content=content,
        title=title or "Plan snapshot",
        author_agent_id=author_agent_id,
        item_key="plan",
    )
    return {
        "filename": filename,
        "filepath": filepath,
        "content": content,
        "title": title or "Plan snapshot",
        "author_agent_id": author_agent_id,
        "saved_at": now,
        "collab_id": collab_id,
    }


# ---------------------------------------------------------------------------
# Stale task cleanup
# ---------------------------------------------------------------------------

STALE_TASK_TIMEOUT_SECONDS = 86400  # 24 hours without update = stale


def cleanup_stale_tasks(*, max_age_seconds: int = STALE_TASK_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    """Mark tasks with no updates within max_age_seconds as 'stale'.

    Returns list of tasks that were marked stale.
    Should be called periodically (e.g. before listing tasks).
    """
    with _board_lock():
        tasks = _read_tasks()
        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
        stale_list = []
        for i, t in enumerate(tasks):
            if t.get("status") not in ("active",):
                continue
            updated_str = t.get("updated_at", t.get("created_at", ""))
            updated_dt = _parse_iso(updated_str)
            if updated_dt is None:
                continue
            elapsed = (now_dt - updated_dt.replace(tzinfo=None)).total_seconds()
            if elapsed > max_age_seconds:
                tasks[i] = {**t, "status": "stale", "updated_at": _now_iso()}
                stale_list.append(tasks[i])
        if stale_list:
            _write_tasks(tasks)
            logger.info(f"[CollabBoard] Marked {len(stale_list)} task(s) as stale (timeout={max_age_seconds}s)")
        return stale_list


# ---------------------------------------------------------------------------
# Plan / Requirement / Status snapshot history
# ---------------------------------------------------------------------------

_SNAPSHOT_DIR = os.path.join(_board_dir(), "snapshots")


def _snapshot_subdir(collab_id: str, zone: str) -> str:
    """Get the snapshot directory for a given collab_id and zone."""
    d = os.path.join(_SNAPSHOT_DIR, collab_id, zone)
    os.makedirs(d, exist_ok=True)
    return d


def save_snapshot(
    *, collab_id: str, zone: str, content: str, title: str = "", author_agent_id: str = "", item_key: str = ""
) -> dict[str, Any]:
    """Save current board item content as a snapshot before overwriting.

    Zones: 'requirement', 'plan', 'status', 'discussion'
    Returns snapshot metadata.

    This is called automatically by upsert_item() for non-discussion zones.
    """
    if not collab_id:
        raise ValueError("collab_id is required")
    if zone not in ("requirement", "plan", "status", "discussion"):
        raise ValueError(f"Invalid snapshot zone: {zone}")
    sub = _snapshot_subdir(collab_id, zone)
    now = _now_iso()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    key_part = f"_{item_key}" if item_key else ""
    filename = f"{ts}{key_part}.json"
    filepath = os.path.join(sub, filename)
    entry = {
        "content": content,
        "title": title or f"{zone} snapshot",
        "author_agent_id": author_agent_id or "",
        "item_key": item_key,
        "saved_at": now,
        "collab_id": collab_id,
        "zone": zone,
    }
    try:
        fd, tmp = tempfile.mkstemp(suffix=".tmp", prefix=".snap_", dir=sub)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False)
        os.replace(tmp, filepath)
    except Exception as e:
        logger.warning(f"[CollabBoard] Failed to save snapshot: {e}")
    return {"filename": filename, "saved_at": now, "zone": zone, "collab_id": collab_id}


def list_snapshots(*, collab_id: str, zone: str = "") -> list[dict[str, Any]]:
    """List snapshots for a collaboration task, newest first.

    Args:
        collab_id: collaboration task id
        zone: optional zone filter ('requirement', 'plan', 'status')
              If empty, returns all zones.
    """
    if not collab_id:
        raise ValueError("collab_id is required")
    base = _snapshot_subdir(collab_id, "")
    if not os.path.isdir(base):
        return []

    zones_to_scan = [zone] if zone else ["requirement", "plan", "status", "discussion"]
    snapshots = []
    for zn in zones_to_scan:
        zd = os.path.join(base, zn)
        if not os.path.isdir(zd):
            continue
        for fname in sorted(os.listdir(zd), reverse=True):
            fpath = os.path.join(zd, fname)
            if not os.path.isfile(fpath) or not fname.endswith(".json"):
                continue
            try:
                with open(fpath, encoding="utf-8") as f:
                    entry = json.load(f)
                entry["filename"] = fname
                entry["size"] = os.path.getsize(fpath)
                snapshots.append(entry)
            except Exception:
                snapshots.append(
                    {
                        "filename": fname,
                        "zone": zn,
                        "content": "(Failed to read)",
                        "saved_at": datetime.fromtimestamp(os.path.getmtime(fpath), tz=timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "size": os.path.getsize(fpath),
                    }
                )

    snapshots.sort(key=lambda x: str(x.get("saved_at", "")), reverse=True)
    return snapshots


def list_plan_snapshots(*, collab_id: str) -> list[dict[str, Any]]:
    """List all plan snapshots for a collaboration task, newest first.

    Merges legacy plan_history/*.md files with unified snapshots/plan/*.json
    (written by upsert_item when Agents update via board_update).
    """
    if not collab_id:
        raise ValueError("collab_id is required")

    snapshots: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def _add(entry: dict[str, Any]) -> None:
        key = f"{entry.get('saved_at', '')}|{entry.get('filename', '')}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        snapshots.append(entry)

    d = _plan_history_dir(collab_id)
    if os.path.isdir(d):
        for fname in sorted(os.listdir(d), reverse=True):
            fpath = os.path.join(d, fname)
            if not os.path.isfile(fpath) or not fname.endswith(".md"):
                continue
            try:
                with open(fpath, encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                content = "(Failed to read)"
            saved_at = (
                datetime.fromtimestamp(os.path.getmtime(fpath), tz=timezone.utc).isoformat().replace("+00:00", "Z")
            )
            _add(
                {
                    "filename": fname,
                    "saved_at": saved_at,
                    "title": f"Plan snapshot — {fname}",
                    "content": content,
                    "size": os.path.getsize(fpath),
                    "collab_id": collab_id,
                    "source": "plan_history",
                }
            )

    for entry in list_snapshots(collab_id=collab_id, zone="plan"):
        _add(
            {
                "filename": entry.get("filename", ""),
                "saved_at": entry.get("saved_at", ""),
                "title": entry.get("title") or entry.get("filename", "Plan snapshot"),
                "content": entry.get("content", ""),
                "size": entry.get("size", 0),
                "collab_id": collab_id,
                "author_agent_id": entry.get("author_agent_id", ""),
                "item_key": entry.get("item_key", ""),
                "source": "snapshots",
            }
        )

    snapshots.sort(key=lambda x: str(x.get("saved_at", "")), reverse=True)
    return snapshots


def _notify_board_changed(
    collab_id: str,
    board_rev: int,
    reason: str,
    item_type: str,
    item_key: str,
    actor_id: str = "",
) -> None:
    """Publish a board_changed event via EventBus (lazy import to avoid circular deps)."""
    try:
        from opensquad.events import bus

        bus.emit(
            "board_changed",
            {
                "collab_id": collab_id,
                "board_rev": board_rev,
                "reason": reason,
                "item_type": item_type,
                "item_key": item_key,
                "actor_id": actor_id,
            },
        )
    except Exception:
        logger.debug("EventBus not available; skipping board_changed notification", exc_info=True)


# Run WAL replay on module import — recovers data from any uncommitted WAL entries
# that were written before a crash. Must be at end of file so all helpers are defined.
_wal_replay()
