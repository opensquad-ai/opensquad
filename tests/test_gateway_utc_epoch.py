"""Naive UTC DB datetimes must convert to epoch as UTC, not process local TZ."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

_BACKEND_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "src", "opensquad", "gateway", "backend")
)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.models import utc_epoch_ms, utc_iso  # noqa: E402


def test_naive_utc_wall_clock_matches_aware_utc():
    naive = datetime(2026, 9, 12, 5, 3, 0)
    aware = datetime(2026, 9, 12, 5, 3, 0, tzinfo=timezone.utc)
    assert utc_epoch_ms(naive) == utc_epoch_ms(aware)
    assert utc_epoch_ms(aware) == int(aware.timestamp() * 1000)


def test_utc_iso_has_z_suffix():
    naive = datetime(2026, 9, 12, 5, 3, 0)
    assert utc_iso(naive) == "2026-09-12T05:03:00Z"


def test_none_passthrough():
    assert utc_epoch_ms(None) is None
    assert utc_iso(None) is None
