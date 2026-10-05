"""Atomic read/write helpers for the ``.session_cwd`` signal file.

The launcher writes this file when the user picks a working directory; the
agent process reads it at the start of each conversation turn.

The file is **per session** when a session id is supplied
(``.session_cwd.<key>``), and a session that has never chosen a folder reads as unset rather than
borrowing the agent-level file — borrowing it is how one workspace's folder choice came back as
another's answer. Callers that pass no session id use the historical agent-level ``.session_cwd``.
Before this split, one agent process serving several panes had a single signal file: whichever pane
touched the folder picker last re-rooted every other pane's file operations, and a write that had
been legal a second earlier came back ``403 Path outside project root``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

SESSION_CWD_VERSION = 1
SESSION_CWD_FILENAME = ".session_cwd"


def _sid_key(session_id: str) -> str:
    """Filename-safe, collision-resistant key for one session's signal file.

    Raw session ids can contain ``:`` / ``/`` (they are used in URLs and file
    stamps), so they are sanitised — and a short digest of the *original* id is
    appended so two ids that sanitise to the same string cannot share a file.
    The digest is a name disambiguator, not an integrity check (nothing is
    authenticated with it); SHA-256 keeps both the security scanners and a
    future reader from mistaking it for one.
    """
    sid = (session_id or "").strip()
    if not sid:
        return ""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", sid)[:40]
    digest = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:8]
    return f"{safe}-{digest}"


def session_cwd_path(agent_dir: str, session_id: str = "") -> str:
    """Path of the signal file for *session_id* (agent-level when empty)."""
    key = _sid_key(session_id)
    if key:
        return os.path.join(agent_dir, f"{SESSION_CWD_FILENAME}.{key}")
    return os.path.join(agent_dir, SESSION_CWD_FILENAME)


def write_session_cwd(agent_dir: str, path: str, session_id: str = "") -> dict[str, Any]:
    """Atomically write ``.session_cwd`` with schema version 1.

    Uses a sibling ``.tmp`` file + ``os.replace`` so a crash cannot leave a
    half-written JSON that would break the agent reader.
    """
    abs_path = os.path.abspath(path)
    payload = {
        "version": SESSION_CWD_VERSION,
        "path": abs_path,
        "ts": time.time(),
    }
    if (session_id or "").strip():
        payload["session_id"] = str(session_id).strip()
    cwd_file = session_cwd_path(agent_dir, session_id)
    tmp_file = cwd_file + ".tmp"
    os.makedirs(agent_dir, exist_ok=True)
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_file, cwd_file)
    return payload


def clear_session_cwd(agent_dir: str, session_id: str = "") -> None:
    """Remove the signal file (reset to the workspace root)."""
    cwd_file = session_cwd_path(agent_dir, session_id)
    if os.path.isfile(cwd_file):
        os.remove(cwd_file)
    tmp_file = cwd_file + ".tmp"
    if os.path.isfile(tmp_file):
        try:
            os.remove(tmp_file)
        except OSError:
            pass


def _read_cwd_file(cwd_file: str) -> dict[str, Any] | None:
    """Parse one signal file; ``None`` when missing or unusable."""
    if not os.path.isfile(cwd_file):
        return None
    try:
        with open(cwd_file, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("[session_cwd] failed to read %s: %s", cwd_file, e)
        return None
    if not isinstance(data, dict):
        logger.warning("[session_cwd] invalid payload type in %s", cwd_file)
        return None
    path = str(data.get("path") or "").strip()
    if not path:
        return None
    version = data.get("version", 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 1
    ts = data.get("ts", 0.0)
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = 0.0
    out: dict[str, Any] = {"version": version, "path": path, "ts": ts}
    sid = str(data.get("session_id") or "").strip()
    if sid:
        out["session_id"] = sid
    return out


def read_session_cwd(agent_dir: str, session_id: str = "") -> dict[str, Any] | None:
    """Read and validate the signal file for *session_id*.

    Returns ``{"version": int, "path": str, "ts": float}`` (plus
    ``session_id`` when the file carries one), or ``None`` when it is missing or corrupt.
    Missing ``version`` defaults to 1 for backward compatibility with pre-schema files.

    A session that has never chosen a folder reports **nothing** rather than borrowing the
    agent-level file. That file is shared by every pane, so borrowing it is how a question asked in
    one workspace came back answered against another: the second session had no file of its own and
    read the first one's pick. Reporting nothing lets the caller fall back to the session's own
    workspace, which is what the operator expects.

    Callers that pass no session id — the serial path, the launcher's own read — keep reading the
    agent-level file exactly as they always have.
    """
    if _sid_key(session_id):
        return _read_cwd_file(session_cwd_path(agent_dir, session_id))
    return _read_cwd_file(session_cwd_path(agent_dir))
