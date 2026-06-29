# -*- coding: utf-8 -*-
"""
Timezone utility: ALL timestamps in the system are stored and transmitted as UTC.

Rules:
  - Storage (DB, JSON, files): always UTC
  - APIs / WebSocket: ISO-8601 with 'Z' suffix (e.g. 2024-01-15T08:30:00Z)
  - Display (UI): convert to local time in the frontend
  - Internal calculation: use timezone-aware datetime or time.time() monotonic

Migration note:
  - Legacy code used beijing_now() (UTC+8), datetime.now() (local), datetime.utcnow() (naive UTC).
  - New code should use functions from this module.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Union

__all__ = [
    "utc_now",
    "utc_now_iso",
    "utc_now_ms",
    "utc_from_iso",
    "utc_from_timestamp",
    "format_iso",
    "format_beijing_iso",  # for display-only, not storage
    "monotonic_ms",
]


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 string with 'Z' suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now_ms() -> int:
    """Return current UTC time as millisecond timestamp (int)."""
    return int(time.time() * 1000)


def utc_from_iso(iso_str: str) -> datetime:
    """Parse ISO-8601 string to timezone-aware UTC datetime.

    Supports:
      - 2024-01-15T08:30:00Z
      - 2024-01-15T08:30:00+00:00
      - 2024-01-15T08:30:00 (treated as UTC)
    """
    s = iso_str.strip()
    # Replace Z with +00:00 for fromisoformat compatibility
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Try common format without T
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_from_timestamp(ts: Union[int, float]) -> datetime:
    """Convert Unix timestamp (seconds or milliseconds) to UTC datetime."""
    if ts > 1e11:  # likely milliseconds
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def format_iso(dt: datetime) -> str:
    """Format datetime to ISO-8601 UTC string with 'Z' suffix."""
    if dt.tzinfo is None:
        # Treat naive datetime as UTC (legacy data)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_beijing_iso(dt: datetime | None = None) -> str:
    """Format datetime to Beijing time ISO string (for display only)."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    beijing = timezone(timedelta(hours=8))
    return dt.astimezone(beijing).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def monotonic_ms() -> int:
    """Return monotonic clock in milliseconds (for elapsed time measurement)."""
    return int(time.monotonic() * 1000)
