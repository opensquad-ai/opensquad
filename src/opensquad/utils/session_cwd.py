"""Atomic read/write helpers for the per-agent ``.session_cwd`` signal file.

The launcher writes this file when the user picks a working directory; the
agent process reads it at the start of each conversation turn.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

SESSION_CWD_VERSION = 1
SESSION_CWD_FILENAME = ".session_cwd"


def session_cwd_path(agent_dir: str) -> str:
    return os.path.join(agent_dir, SESSION_CWD_FILENAME)


def write_session_cwd(agent_dir: str, path: str) -> dict[str, Any]:
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
    cwd_file = session_cwd_path(agent_dir)
    tmp_file = cwd_file + ".tmp"
    os.makedirs(agent_dir, exist_ok=True)
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_file, cwd_file)
    return payload


def clear_session_cwd(agent_dir: str) -> None:
    """Remove the signal file (reset to permanent workspace root)."""
    cwd_file = session_cwd_path(agent_dir)
    if os.path.isfile(cwd_file):
        os.remove(cwd_file)
    tmp_file = cwd_file + ".tmp"
    if os.path.isfile(tmp_file):
        try:
            os.remove(tmp_file)
        except OSError:
            pass


def read_session_cwd(agent_dir: str) -> dict[str, Any] | None:
    """Read and validate ``.session_cwd``.

    Returns ``{"version": int, "path": str, "ts": float}`` or ``None`` if the
    file is missing / corrupt. Missing ``version`` defaults to 1 for
    backward compatibility with pre-schema files.
    """
    cwd_file = session_cwd_path(agent_dir)
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
    return {"version": version, "path": path, "ts": ts}
