"""
Cached JSON file loader with mtime-based staleness detection.

Allows hot paths to repeatedly read a config file without paying the cost of
disk I/O on every call.  The cache entry is invalidated automatically when the
file's modification time changes.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_cached: dict[str, tuple[float, Any]] = {}  # path -> (mtime, parsed_data)


def load_json_cached(path: str, default: dict | None = None) -> dict[str, Any]:
    """Load a JSON file, returning cached data when the file has not changed.

    Args:
        path: Absolute path to the JSON file.
        default: Value returned if the file does not exist or cannot be parsed.

    Returns:
        Parsed JSON as a dict/list, or ``default`` on error.
    """
    if default is None:
        default = {}

    if not os.path.isfile(path):
        return default

    try:
        current_mtime = os.path.getmtime(path)
    except OSError:
        return default

    cached_mtime, cached_data = _cached.get(path, (None, None))

    if cached_mtime == current_mtime:
        return cached_data

    try:
        # Use utf-8-sig to tolerate BOM-prefixed JSON files (e.g. written by
        # third-party tools on Windows such as Notepad, or by agents using the
        # filesystem tool with utf-8-sig). json.load() with plain "utf-8" would
        # raise "Unexpected UTF-8 BOM" on the first byte.
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        _cached[path] = (current_mtime, data)
        logger.debug("[json_cache] loaded (mtime=%.3f): %s", current_mtime, path)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[json_cache] failed to load %s: %s", path, exc)
        return default


def invalidate_json_cache(path: str) -> None:
    """Invalidate the cache entry for ``path`` (e.g. after a write)."""
    _cached.pop(path, None)


def clear_json_cache() -> None:
    """Clear the entire in-process cache."""
    _cached.clear()
