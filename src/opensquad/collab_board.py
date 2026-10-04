"""
Collaboration board storage.

Key behaviors:
- Every collaboration task has a 6-char alnum task_id (collab_id)
- Board entries are namespaced by task_id
- Only latest tool-call snapshot is kept per (task_id, agent_id, item_type)
- Task list supports history-style browsing with duration/progress stats
"""

from __future__ import annotations

import hashlib
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
# How deep this thread is inside _board_lock, so a nested acquisition is a no-op rather than a
# wait on the lock the same thread already holds.
_lock_depth = threading.local()

# Only replay WAL entries younger than this window. Stale entries left over from
# a previous session must not revive data that was legitimately removed (e.g.
# deleting the last task, which empties the main file).
_WAL_REPLAY_WINDOW = 3600  # seconds


def _board_key() -> str:
    """A stable, filename-safe id for this workspace's board, so its lock file is its own.

    The lock was one machine-wide resource named "collab_board", so a second deployment on the same
    box — or a test — contended for the same file as the stack serving users, and a lost
    acquisition was ignored rather than reported (a writer then went on unprotected). Keyed by the
    board directory, every writer of a given board still shares one lock, and nothing else does.
    """
    try:
        raw = _board_dir()
    except Exception:
        raw = "collab_board"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]


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

    Re-entrant within a thread: a caller that wants a whole read-modify-write
    sequence to be one critical section (``board_write_lock``) still calls
    ``upsert_item`` inside it, and that inner call must not wait on the lock its
    own thread is holding — the file lock would otherwise time out after 15s.
    """
    depth = getattr(_lock_depth, "depth", 0)
    if depth:
        _lock_depth.depth = depth + 1
        try:
            yield
        finally:
            _lock_depth.depth = depth
        return
    with _LOCK:
        from opensquad.distributed_lock import LockTimeoutError

        lock = SessionLock(f"collab_board:{_board_key()}", timeout=timeout)
        if not lock.acquire():
            # acquire() returns False on timeout rather than raising, and this call used to ignore
            # that: the board was then read and written with no mutual exclusion at all, which is
            # exactly the lost update the file lock is here to prevent — and it failed quietly.
            raise LockTimeoutError(f"collab_board lock not acquired within {timeout}s")
        _lock_depth.depth = 1
        try:
            yield
        finally:
            _lock_depth.depth = 0
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
    *,
    task_name: str,
    created_by: str,
    task_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    group_id: str = "",
) -> dict[str, Any]:
    with _board_lock():
        tasks = _read_tasks()
        existing = {str(t.get("task_id", "")) for t in tasks}
        cid = task_id or _gen_task_id(existing)
        if cid in existing:
            raise ValueError(f"task_id already exists: {cid}")
        now = _now_iso()
        extra = dict(metadata) if isinstance(metadata, dict) else {}
        if group_id:
            extra["group_id"] = str(group_id)
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
            "extra": extra,
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


@contextmanager
def board_write_lock():
    """Hold the board's file lock across a whole read-modify-write sequence.

    The lock is re-entrant, so the reads and writes inside the sequence (`list_items`,
    `upsert_item`) take it again without deadlocking, and the whole sequence becomes one critical
    section for every process on the machine. Callers used to hold a thread lock of their own
    instead, which only ever serialised the threads of a single process: two agents updating
    different subtasks of the same item could each read, then write, and the later write silently
    dropped the earlier one.
    """
    with _board_lock():
        yield


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
    *,
    collab_id: str,
    task_name: str,
    author_agent_id: str,
    title: str,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Append a message to the task's thread.

    ``attachments`` stay on the message itself (``extra.attachments``), not in a
    separate list: a picture sent with a sentence belongs beside that sentence, the way
    it does in the group chat.
    """
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
        if attachments:
            rec["extra"] = {"attachments": [dict(a) for a in attachments if isinstance(a, dict)]}
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


# ---------------------------------------------------------------------------
# Collaboration task metadata + participant state
#
# Stored under task["extra"] (no schema change, no migration):
#   extra["card"]         — collab card name the team runs on
#   extra["skills"]       — skill names the team loaded for this task
#   extra["files"]        — files the task produced / touches
#   extra["participants"] — {agent_id: {name, state, invited_at, responded_at}}
# ---------------------------------------------------------------------------
def get_task(*, task_id: str) -> dict[str, Any] | None:
    """Return a task record, or None when it does not exist."""
    if not task_id:
        return None
    with _board_lock():
        tasks = _read_tasks()
    return next((t for t in tasks if str(t.get("task_id", "")) == str(task_id)), None)


def _mutate_task_extra(collab_id: str, mutate) -> dict[str, Any]:
    """Apply ``mutate`` to a copy of the task's extra dict and persist it."""
    if not collab_id:
        return {}
    try:
        with _board_lock():
            tasks = _read_tasks()
            idx = next((i for i, t in enumerate(tasks) if str(t.get("task_id", "")) == str(collab_id)), -1)
            if idx < 0:
                return {}
            rec = dict(tasks[idx])
            extra = rec.get("extra")
            extra = dict(extra) if isinstance(extra, dict) else {}
            mutate(extra)
            rec["extra"] = extra
            rec["updated_at"] = _now_iso()
            rec["board_rev"] = int(rec.get("board_rev") or 0) + 1
            tasks[idx] = rec
            _write_tasks(tasks)
            new_rev = int(rec["board_rev"])
    except Exception:
        logger.debug("Failed to update task extra for %s", collab_id, exc_info=True)
        return {}
    _notify_board_changed(collab_id, board_rev=new_rev, reason="task_extra", item_type="task_meta", item_key="")
    return rec


def set_card_and_skills(
    *,
    collab_id: str,
    card: str = "",
    skills: list[str] | None = None,
    files: list[str] | None = None,
    project_dir: str = "",
) -> dict[str, Any]:
    """Record which collab card + skills (+ produced files, project dir) this task runs on."""

    def _apply(extra: dict[str, Any]) -> None:
        if card:
            extra["card"] = str(card)
        if project_dir:
            extra["project_dir"] = str(project_dir).strip()
        if skills:
            merged = [str(s) for s in (extra.get("skills") or []) if str(s)]
            for s in skills:
                s = str(s).strip()
                if s and s not in merged:
                    merged.append(s)
            extra["skills"] = merged
        if files:
            merged_f = [str(f) for f in (extra.get("files") or []) if str(f)]
            for f in files:
                f = str(f).strip()
                if f and f not in merged_f:
                    merged_f.append(f)
            extra["files"] = merged_f

    return _mutate_task_extra(collab_id, _apply)


def mark_participant(*, collab_id: str, agent_id: str, state: str, name: str = "") -> dict[str, Any]:
    """Record a participant's invite state: invited / accepted / declined."""
    aid = (agent_id or "").strip()
    if not aid:
        return {}
    st = (state or "").strip().lower()
    if st not in ("invited", "accepted", "declined"):
        st = "invited"
    now = _now_iso()

    def _apply(extra: dict[str, Any]) -> None:
        parts = extra.get("participants")
        parts = dict(parts) if isinstance(parts, dict) else {}
        cur = parts.get(aid)
        cur = dict(cur) if isinstance(cur, dict) else {}
        cur["name"] = (name or cur.get("name") or aid).strip()
        cur["state"] = st
        if st == "invited":
            cur.setdefault("invited_at", now)
        else:
            cur["responded_at"] = now
            cur.setdefault("invited_at", now)
        parts[aid] = cur
        extra["participants"] = parts

    return _mutate_task_extra(collab_id, _apply)


GATE_STEPS = ("确定需求", "讨论方案", "任务分配", "任务验收")


def active_tasks_for(agent_id: str, *, exclude: str = "") -> list[dict[str, Any]]:
    """Live collaborations this agent is part of — as creator or as any participant.

    **One at a time.** An agent in two live tasks reports progress on the wrong board,
    answers the wrong thread, and (from the field) starts collaborations it cannot
    finish. A finished task — ``status`` done/failed/archived, or ``closed_at`` set —
    stops counting, so the way out of this refusal is to end the one it is in.
    """
    who = str(agent_id or "").strip()
    if not who:
        return []
    out: list[dict[str, Any]] = []
    for task in _read_tasks():
        task_id = str(task.get("task_id") or "")
        if not task_id or task_id == str(exclude or ""):
            continue
        if str(task.get("status") or "active") != "active" or task.get("closed_at") or task.get("ended_at"):
            continue
        members = {str(m) for m in (task.get("members") or []) if str(m)}
        extra = task.get("extra") if isinstance(task.get("extra"), dict) else {}
        participants = extra.get("participants") if isinstance(extra.get("participants"), dict) else {}
        creator = str(task.get("created_by") or "")
        if who != creator and who not in members and who not in {str(k) for k in participants}:
            continue
        out.append(
            {
                "task_id": task_id,
                "task_name": str(task.get("task_name") or task_id),
                "status": str(task.get("status") or "active"),
                "group_id": str(extra.get("group_id") or ""),
                "role": "creator" if who == creator else "member",
            }
        )
    return out


def gate_states(collab_id: str) -> dict[str, str]:
    """The latest state of each collaboration gate.

    The four gates (四门闸) live on the board as ``item_type="approval"`` items carrying
    ``extra.approval.step``; the user approving one from the task window (or the group
    card) flips that item's ``status``. Returns ``approved`` / ``pending`` /
    ``rejected`` / ``missing`` per gate, newest item wins.
    """
    from opensquad.collab_approval import normalize_step

    latest: dict[str, tuple[str, str]] = {}
    for item in _read_items():
        if str(item.get("collab_id") or "") != str(collab_id):
            continue
        if str(item.get("item_type") or "") != "approval":
            continue
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        approval = extra.get("approval") if isinstance(extra.get("approval"), dict) else {}
        try:
            step = normalize_step(str(approval.get("step") or item.get("title") or ""))
        except Exception:
            step = str(approval.get("step") or "")
        if step not in GATE_STEPS:
            continue
        stamp = str(item.get("updated_at") or item.get("created_at") or "")
        state = str(item.get("status") or "pending").strip().lower()
        if state not in ("approved", "pending", "rejected"):
            state = "pending"
        current = latest.get(step)
        if current is None or stamp >= current[1]:
            latest[step] = (state, stamp)
    return {step: (latest.get(step) or ("missing", ""))[0] for step in GATE_STEPS}


def pending_members(collab_id: str) -> list[dict[str, Any]]:
    """Members who have not accepted yet — what blocks assigning work.

    Invited, declined, and never-recorded (a name on the task without a participant row)
    all count: work goes to a team that has agreed to be one. The creator is never pending
    (it is accepted at creation).
    """
    task = get_task(task_id=collab_id) or {}
    creator = str(task.get("created_by") or "")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for part in list_participants(collab_id=collab_id):
        agent_id = str(part.get("agent_id") or "")
        if not agent_id:
            continue
        seen.add(agent_id)
        state = str(part.get("state") or "invited")
        if agent_id != creator and state != "accepted":
            out.append({"agent_id": agent_id, "state": state, "name": str(part.get("name") or agent_id)})
    for member in task.get("members") or []:
        name = str(member if isinstance(member, str) else (member or {}).get("agent_id") or "")
        if name and name != creator and name not in seen:
            out.append({"agent_id": name, "state": "not_invited", "name": name})
    return out


def accepted_members(collab_id: str) -> set[str]:
    """Agent ids that may be given work: those who accepted, plus the task's creator.

    An invite is a request, not a draft: until the invitee runs ``join_collaboration``
    it is ``invited``, and assigning work to it either fails or quietly gets done by an
    agent that never agreed to be part of the task.
    """
    task = get_task(task_id=collab_id) or {}
    out: set[str] = set()
    creator = str(task.get("created_by") or "")
    if creator:
        out.add(creator)
    for part in list_participants(collab_id=collab_id):
        if str(part.get("state") or "") == "accepted":
            out.add(str(part.get("agent_id") or ""))
    out.discard("")
    return out


def list_participants(*, collab_id: str) -> list[dict[str, Any]]:
    """Participants in insertion order (invited first), for card rendering."""
    task = get_task(task_id=collab_id)
    if not task:
        return []
    extra = task.get("extra")
    parts = extra.get("participants") if isinstance(extra, dict) else None
    if not isinstance(parts, dict):
        return []
    out = []
    for aid, rec in parts.items():
        rec = rec if isinstance(rec, dict) else {}
        out.append(
            {
                "agent_id": str(aid),
                "name": str(rec.get("name") or aid),
                "state": str(rec.get("state") or "invited"),
                "invited_at": rec.get("invited_at"),
                "responded_at": rec.get("responded_at"),
            }
        )
    out.sort(key=lambda p: (0 if p["state"] == "invited" else 1, str(p.get("invited_at") or "")))
    return out


_SUMMARY_ITEM_TYPES = (
    "requirement",
    "requirement_doc",
    "plan",
    "task",
    "status",
    "discussion",
    "change_request",
    "approval",
    "attachment",
)


def attach_files(
    *,
    collab_id: str,
    agent_id: str,
    files: list[dict[str, Any]] | None = None,
    task_name: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Record uploaded files/images on the task.

    ``files`` entries are what the gateway's upload endpoint returns:
    ``{"url": "/uploads/xxx", "name": "...", "size": "...", "type": "image|file"}``.
    Each becomes an ``attachment`` item, so the task window can render
    thumbnails and downloads without re-reading the chat, and so every machine
    sees the same file list (the URL is served by the gateway).
    """
    if not collab_id:
        raise ValueError("collab_id(task_id) is required")
    stored: list[dict[str, Any]] = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        url = str(f.get("url") or "").strip()
        if not url:
            continue
        name = str(f.get("name") or url.rsplit("/", 1)[-1])
        kind = str(f.get("type") or "").strip().lower()
        if kind not in ("image", "video", "file", "folder", "voice"):
            ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
            kind = "image" if ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg") else "file"
        stored.append(
            upsert_item(
                collab_id=collab_id,
                task_name=task_name,
                agent_id=agent_id,
                item_type="attachment",
                item_key=url,
                title=name,
                content=note or "",
                status="done",
                visibility="public",
                extra={
                    "url": url,
                    "name": name,
                    "size": str(f.get("size") or ""),
                    "kind": kind,
                    "uploader": agent_id,
                    "note": note,
                },
            )
        )
    return {"status": "success", "count": len(stored), "items": stored}


def board_summary(*, collab_id: str) -> dict[str, Any]:
    """Everything the single-task window needs, in one call."""
    if not collab_id:
        raise ValueError("collab_id(task_id) is required")
    task = get_task(task_id=collab_id) or {}
    extra = task.get("extra") if isinstance(task.get("extra"), dict) else {}
    items = list_items(collab_id=collab_id)
    grouped: dict[str, list[dict[str, Any]]] = {t: [] for t in _SUMMARY_ITEM_TYPES}
    for it in items:
        grouped.setdefault(str(it.get("item_type") or ""), []).append(it)
    # Discussion reads chronologically; documents read oldest-first too.
    for bucket in grouped.values():
        bucket.sort(key=lambda i: str(i.get("created_at") or i.get("updated_at") or ""))
    files: list[str] = [str(f) for f in (extra.get("files") or []) if str(f)]
    for it in grouped.get("task", []):
        scope = (it.get("extra") or {}).get("file_scope") if isinstance(it.get("extra"), dict) else ""
        for f in str(scope or "").split(","):
            f = f.strip()
            if f and f not in files:
                files.append(f)
    attachments = [
        {
            "id": str(it.get("id") or ""),
            "url": str((it.get("extra") or {}).get("url") or it.get("content") or ""),
            "name": str((it.get("extra") or {}).get("name") or it.get("title") or ""),
            "size": str((it.get("extra") or {}).get("size") or ""),
            "kind": str((it.get("extra") or {}).get("kind") or "file"),
            "uploader": str((it.get("extra") or {}).get("uploader") or it.get("agent_id") or ""),
            "note": str((it.get("extra") or {}).get("note") or ""),
            "created_at": str(it.get("created_at") or ""),
        }
        for it in grouped.get("attachment", [])
    ]
    return {
        "collab_id": collab_id,
        "task": task,
        "title": task.get("task_name") or collab_id,
        "status": task.get("status") or "",
        "progress": task.get("progress") or 0,
        "board_rev": task.get("board_rev") or 0,
        "card": str(extra.get("card") or ""),
        "project_dir": str(extra.get("project_dir") or ""),
        "skills": [str(s) for s in (extra.get("skills") or []) if str(s)],
        "files": files,
        "attachments": attachments,
        "participants": list_participants(collab_id=collab_id),
        "items": grouped,
    }


# ---------------------------------------------------------------------------
# Remote boards
#
# A board belongs to the deployment that owns the group chat, not to each agent
# machine: two agents in one group must share one task list, one member set and
# one set of items. So when this agent talks to a gateway on another host
# (``group_chat.base_url`` points somewhere that is not loopback), every board
# call is forwarded there over HTTP — authenticated with the same
# ``auth.node_secret`` the agent already uses for ``/ai-ws/register``.
#
# The local files stay the implementation of record *on the gateway* (the board
# bridge calls the un-dispatched functions through :func:`local_call`) and stay
# the behaviour on single-machine installs, where nothing is forwarded.
# ---------------------------------------------------------------------------
_BOARD_AGENT_PATH = "/api/ai-web/agent/board"
_BOARD_TIMEOUT = 20.0

# Every operation an agent may drive remotely (the public surface of this module).
REMOTE_OPS = (
    "create_task",
    "update_task",
    "list_tasks",
    "get_task",
    "upsert_item",
    "list_items",
    "append_public_discussion",
    "attach_files",
    "update_latest_tool",
    "delete_item",
    "delete_task",
    "set_card_and_skills",
    "mark_participant",
    "list_participants",
    "board_summary",
    "save_snapshot",
    "list_snapshots",
    "save_plan_snapshot",
    "list_plan_snapshots",
    "gate_states",
    "cleanup_stale_tasks",
)

_LOCAL_IMPL: dict[str, Any] = {}


class BoardRemoteError(RuntimeError):
    """The board lives on a remote gateway and the call could not reach it."""


def _is_loopback(url: str) -> bool:
    host = str(url or "").strip().lower()
    for prefix in ("http://", "https://", "ws://", "wss://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    host = host.split("/", 1)[0].split(":", 1)[0]
    return host in ("", "localhost", "127.0.0.1", "::1", "0.0.0.0")


def _http_url(url: str) -> str:
    base = str(url or "").strip()
    if base.startswith("ws://"):
        base = "http://" + base[len("ws://") :]
    elif base.startswith("wss://"):
        base = "https://" + base[len("wss://") :]
    for suffix in ("/ws", "/ai-ws/register"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base.rstrip("/")


def _agent_config() -> dict[str, Any]:
    """This agent's config.json (``{}`` for a non-agent process such as the gateway)."""
    try:
        from opensquad.input_hub import input_hub
        from opensquad.json_cache import load_json_cached

        agent_dir = input_hub.agent_dir or ""
        if not agent_dir:
            return {}
        cfg = load_json_cached(os.path.join(agent_dir, "config.json"))
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def board_mode() -> str:
    """``local`` | ``remote`` | ``auto`` (default: auto)."""
    env = (os.environ.get("OPENSQUAD_BOARD_MODE") or "").strip().lower()
    if env in ("local", "remote", "auto"):
        return env
    cfg = _agent_config().get("collab_board")
    mode = str((cfg or {}).get("mode") or "").strip().lower() if isinstance(cfg, dict) else ""
    return mode if mode in ("local", "remote", "auto") else "auto"


# ---------------------------------------------------------------------------
# Which machine owns a board
#
# The board belongs to the deployment that owns the *group*, not to whichever
# machine an agent happens to run on. Pairing a second machine no longer repoints
# the agent's chat bridge, so the old "forward whenever group_chat.base_url is
# off this host" test is no longer the whole story: a group joined on a paired
# peer must forward to *that peer*, while the agent's own groups stay local.
#
# The mapping is recorded when the agent joins: the group in
# ``group_chat.peers[<host>].groups`` (via peer_bridge.remember_peer_group), and
# the collaboration task in ``board_owners.json`` — because most board calls carry
# only a ``collab_id``, and the task id does not say which machine minted it.
# ---------------------------------------------------------------------------
def _board_owners_file() -> str:
    return os.path.join(syscfg.workspace_data_dir("collab_board"), "board_owners.json")


def remember_board_owner(collab_id: str, host: str) -> bool:
    """Record that collaboration task ``collab_id`` lives on ``host``.

    A no-op for an empty host: an empty host means "local", and local needs no
    entry (the absence of one is exactly what keeps it local).
    """
    cid = str(collab_id or "").strip()
    key = str(host or "").strip()
    if not cid or not key:
        return False
    try:
        data = read_json(_board_owners_file(), {})
        if not isinstance(data, dict):
            data = {}
        data[cid] = key
        atomic_write_json(_board_owners_file(), data)
        return True
    except Exception:
        logger.debug("Failed to record board owner for %s", cid, exc_info=True)
        return False


def board_owners() -> dict[str, str]:
    """Every collaboration this machine knows lives elsewhere: ``{collab_id: host}``.

    The local record of boards reached through a paired machine — written when this agent joined
    the group there, and again by ``join_collaboration``. It is what lets a caller with no hint of
    its own (listing which collaborations it is part of, say) still find the remote ones: each id
    it names can be read back with ``get_task``, and those calls carry the hint that routes them.
    """
    try:
        data = read_json(_board_owners_file(), {})
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in data.items() if str(k).strip() and str(v).strip()}


def board_owner(collab_id: str = "", group_id: str = "") -> str:
    """The host owning this board, or ``""`` when it is local.

    Resolution order, most specific first: the collaboration task's recorded
    owner, then the group it belongs to. An unknown task or group is local — this
    never guesses a remote host, because forwarding a local board's calls to a
    machine that does not own them would split one group's board in two.
    """
    from opensquad import peer_bridge

    cid = str(collab_id or "").strip()
    if cid:
        try:
            data = read_json(_board_owners_file(), {})
            if isinstance(data, dict):
                host = str(data.get(cid) or "").strip()
                if host:
                    return host
        except Exception:
            pass
    group = str(group_id or "").strip()
    if group:
        entry = peer_bridge.peer_for_group(group)
        if entry:
            return str(entry.get("host") or entry.get("base_url") or "").strip()
    return ""


def _board_hint_base_url(host: str) -> str:
    """The base URL of the peer named/addressable as ``host``, or ``""``."""
    if not host:
        return ""
    try:
        from opensquad import peer_bridge

        entry = peer_bridge.find_peer(host) or {}
        return _http_url(str(entry.get("base_url") or ""))
    except Exception:
        return ""


def board_base_url(collab_id: str = "", group_id: str = "") -> str:
    """Gateway base URL owning this board, or ``""`` when the board is local.

    ``collab_id`` / ``group_id`` name the board when the caller knows it; without
    them the legacy behaviour applies (env, explicit config, else the chat bridge
    when it points off this machine).
    """
    env = (os.environ.get("OPENSQUAD_BOARD_URL") or "").strip()
    if env:
        return _http_url(env)
    mode = board_mode()
    if mode == "local":
        return ""
    cfg = _agent_config()
    collab_cfg = cfg.get("collab_board") if isinstance(cfg.get("collab_board"), dict) else {}
    explicit = str((collab_cfg or {}).get("url") or "").strip()
    if explicit:
        return _http_url(explicit)
    # A board this agent joined on a paired machine belongs to that machine.
    hinted = _board_hint_base_url(board_owner(collab_id=collab_id, group_id=group_id))
    if hinted:
        return hinted
    chat = cfg.get("group_chat") if isinstance(cfg.get("group_chat"), dict) else {}
    base = str((chat or {}).get("base_url") or "").strip()
    if mode == "remote":
        # Explicit remote: fall back to the gateway registration URL.
        gateway = cfg.get("gateway") if isinstance(cfg.get("gateway"), dict) else {}
        return _http_url(base or str((gateway or {}).get("url") or ""))
    # auto: only when the chat bridge itself points off this machine
    if not base or _is_loopback(base):
        return ""
    return _http_url(base)


def _board_args_hint(op: str, args: tuple, kwargs: dict) -> dict[str, str]:
    """The ``collab_id`` / ``group_id`` a board op was called with, if any."""
    hint: dict[str, str] = {}
    for name in ("collab_id", "group_id", "task_id"):
        value = kwargs.get(name)
        if value is None and name == "task_id" and args:
            value = args[0]
        if value:
            hint[name] = str(value)
    return hint


def _board_auth_headers(base: str) -> dict[str, str]:
    """The headers a board call needs: this machine's secret, plus the peer token when paired.

    A paired machine does not hold the owner's ``node_secret``; it authenticates with the scoped
    token it was given. Pairing records that token in two places (the workspace peer store, and the
    agent's own config) and the store is not always where it ends up — a re-pairing, a migrated
    install — so both are read, or the board answers 401 "Invalid or missing node secret" and the
    write silently lands on this machine's board instead of the owner's. Shared by the board RPC and
    the task-message notify so the two cannot drift into that again.
    """
    try:
        from opensquad.system_config import syscfg

        secret = syscfg.node_secret()
    except Exception:
        secret = ""
    headers = {"Content-Type": "application/json", "X-Node-Secret": secret or ""}
    try:
        from opensquad import node_peers

        peer = node_peers.load_local_peer(base)
        if peer and peer.get("token"):
            headers["X-Node-Token"] = str(peer["token"])
    except Exception:
        pass
    if not headers.get("X-Node-Token"):
        try:
            from opensquad import peer_bridge

            token = peer_bridge.peer_token(base)
            if token:
                headers["X-Node-Token"] = str(token)
        except Exception:
            pass
    return headers


def _owner_base(collab_id: str) -> str:
    """The address of the machine that owns this board (loopback when that is us)."""
    base = board_base_url(collab_id=collab_id)
    if base:
        return base
    try:
        from opensquad.system_config import syscfg

        return f"http://127.0.0.1:{int(syscfg.port('gateway') or 9555)}"
    except Exception:
        return ""


def _post_to_owner(collab_id: str, path: str, payload: dict[str, Any]) -> bool:
    """POST to the owner's gateway, authenticated like a board call. Never raises."""
    import urllib.request

    cid = str(collab_id or "").strip()
    base = _owner_base(cid) if cid else ""
    if not cid or not base:
        return False
    req = urllib.request.Request(
        f"{base}{path.format(collab_id=cid)}",
        data=json.dumps(payload).encode("utf-8"),
        headers=_board_auth_headers(base),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            return bool(json.loads(resp.read().decode("utf-8") or "{}").get("ok"))
    except Exception:
        logger.debug("[Board] %s failed for %s", path, cid, exc_info=True)
        return False


def merged_tasks(include_stale: bool = False) -> tuple[list, list[str]]:
    """Every task this deployment knows about, including boards owned by paired machines.

    The local board file holds only this machine's own tasks, so a caller that reads nothing else
    reports a confident zero for a collaboration that lives on a peer — the failure mode that looks
    like "nothing is running" while the other machine is mid-task. board_owners.json is the local
    record of those, and reading each one by id carries the hint that routes the call to its owner.
    An owner we cannot reach contributes nothing and is named, so the reason is visible.
    """
    tasks = list_tasks(include_stale=include_stale)
    unreachable: list[str] = []
    seen = {str(t.get("task_id") or "") for t in tasks}
    try:
        owners = list(board_owners())
    except Exception:
        owners = []
    for cid in owners:
        if cid in seen:
            continue
        try:
            remote = get_task(task_id=cid)
        except Exception:  # noqa: BLE001 - named instead, never fatal
            unreachable.append(cid)
            continue
        if isinstance(remote, dict) and str(remote.get("task_id") or ""):
            tasks.append(remote)
            seen.add(cid)
    return tasks, unreachable


def announce_participant(collab_id: str, agent_id: str, state: str) -> bool:
    """Ask the machine that owns this board to rewrite a collaboration card's participant state.

    The card is a group message holding a snapshot of who was invited when it was sent.
    ``mark_participant`` updates the board — written by the agent process — while the card is a
    message this gateway owns, so a worker that joined showed as accepted in the task window and
    still as invited on the card in the group. The user's own accept is patched by the respond
    endpoint, which runs in the gateway; this is the same rewrite for the agent path.

    Best effort by design: the board is already correct, so a failure here must not fail the join.
    """
    return _post_to_owner(
        collab_id,
        "/api/ai-web/collab-board/tasks/{collab_id}/participant",
        {"agent_id": str(agent_id or ""), "state": str(state or "")},
    )


def _remote_call(op: str, args: tuple, kwargs: dict) -> Any:
    import urllib.error
    import urllib.request

    hint = _board_args_hint(op, args, kwargs)
    base = board_base_url(
        collab_id=hint.get("collab_id") or hint.get("task_id", ""),
        group_id=hint.get("group_id", ""),
    )
    if not base:
        raise BoardRemoteError("no remote board configured")
    # How a board call authenticates is decided in one place now, shared with the task-message
    # notify — two copies of this lookup is how a stale token once went unnoticed.
    headers = _board_auth_headers(base)
    payload = json.dumps({"op": op, "args": list(args), "kwargs": kwargs}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{_BOARD_AGENT_PATH}",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_BOARD_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = ""
        with suppress(Exception):
            body = exc.read().decode("utf-8")[:300]
        hint = ""
        if exc.code == 401:
            # The owner just told us it did not accept this machine's token; it now also says why
            # (revoked / unknown / no scope). The recovery is always the same, so say it here too.
            hint = (
                f" — {base} did not accept this machine's pairing token. Check the peer there "
                "(Node pairing: it must be listed and not revoked); if it was revoked, pair again "
                "with a fresh code to get a working token."
            )
        raise BoardRemoteError(f"board {op} rejected by {base}: HTTP {exc.code} {body}{hint}") from exc
    except Exception as exc:
        raise BoardRemoteError(f"board {op} could not reach {base}: {exc}") from exc
    if not isinstance(data, dict) or not data.get("ok"):
        raise BoardRemoteError(f"board {op} failed on {base}: {str(data)[:300]}")
    return data.get("result")


def local_call(op: str, *args: Any, **kwargs: Any) -> Any:
    """Run a board operation against the *local* files, bypassing forwarding.

    This is what the gateway's board bridge calls: the gateway owns the board,
    so it must never forward its own requests back out.
    """
    fn = _LOCAL_IMPL.get(op)
    if fn is None:
        raise KeyError(f"unknown board op: {op}")
    return fn(*args, **kwargs)


def _install_remote_dispatch() -> None:
    """Forward the public board surface to the owning gateway when remote."""
    g = globals()
    for op in REMOTE_OPS:
        local_fn = g.get(op)
        if local_fn is None or getattr(local_fn, "_board_dispatched", False):
            continue
        _LOCAL_IMPL[op] = local_fn

        def make(local_impl, name):
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                hint = _board_args_hint(name, args, kwargs)
                if board_base_url(
                    collab_id=hint.get("collab_id") or hint.get("task_id", ""),
                    group_id=hint.get("group_id", ""),
                ):
                    return _remote_call(name, args, kwargs)
                return local_impl(*args, **kwargs)

            wrapper.__name__ = name
            wrapper.__doc__ = local_impl.__doc__
            wrapper._board_dispatched = True
            return wrapper

        g[op] = make(local_fn, op)


_install_remote_dispatch()


# Run WAL replay on module import — recovers data from any uncommitted WAL entries
# that were written before a crash. Must be at end of file so all helpers are defined.
_wal_replay()
