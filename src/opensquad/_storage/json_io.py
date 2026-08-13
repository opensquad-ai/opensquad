"""
Unified JSON read/write with atomic file replacement.

Replaces ad-hoc ``_read_json`` / ``_write_json`` helpers duplicated across
``collab_board.py``, ``launcher.py``, ``task_logger.py`` and other modules.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any


def read_json(path: str, default: Any = None) -> Any:
    """Read and parse a JSON file.

    Args:
        path: Absolute path to the JSON file.
        default: Value returned when the file does not exist or is malformed.
            ``None`` is treated as ``{}`` so launcher config reads stay dict-shaped.

    Returns:
        Parsed Python object, or *default* on any error.
    """
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        import logging

        logging.getLogger(__name__).warning("[JsonIO] Failed to read %s: %s", path, exc)
        return default


def atomic_write_json(path: str, data: Any, *, indent: int | None = 2) -> None:
    """Atomically write *data* as JSON to *path*.

    Uses the ``write-temp → os.replace`` pattern so that concurrent readers
    never see a partial file.  Falls back to a direct write if the atomic
    path fails (e.g. cross-device rename).

    Args:
        path: Target file path.
        data: Serialisable Python object.
        indent: JSON indent level.  ``None`` produces compact output.
    """
    dir_name = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_name, exist_ok=True)

    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=dir_name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(fd)
        os.replace(tmp_path, path)
    except OSError:
        # Fallback: direct write (e.g. cross-device rename not possible)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
