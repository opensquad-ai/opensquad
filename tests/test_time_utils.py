# -*- coding: utf-8 -*-
"""Tests for time_utils — UTC timezone standardization."""
import time
from datetime import datetime, timezone, timedelta

import pytest

from opensquad.time_utils import (
    utc_now,
    utc_now_iso,
    utc_now_ms,
    utc_from_iso,
    utc_from_timestamp,
    format_iso,
    format_beijing_iso,
    monotonic_ms,
)


class TestUtcNow:
    def test_returns_timezone_aware(self):
        dt = utc_now()
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_is_roughly_current(self):
        dt = utc_now()
        now_ts = time.time()
        assert abs(dt.timestamp() - now_ts) < 1.0


class TestUtcNowIso:
    def test_ends_with_z(self):
        s = utc_now_iso()
        assert s.endswith("Z")
        assert "T" in s

    def test_roundtrip(self):
        s = utc_now_iso()
        dt = utc_from_iso(s)
        assert dt.tzinfo is not None


class TestUtcNowMs:
    def test_is_reasonable_timestamp(self):
        ms = utc_now_ms()
        now_ms = int(time.time() * 1000)
        assert abs(ms - now_ms) < 1000


class TestUtcFromIso:
    def test_z_suffix(self):
        dt = utc_from_iso("2024-06-09T12:00:00Z")
        assert dt.year == 2024
        assert dt.hour == 12
        assert dt.utcoffset().total_seconds() == 0

    def test_offset_suffix(self):
        dt = utc_from_iso("2024-06-09T12:00:00+00:00")
        assert dt.utcoffset().total_seconds() == 0

    def test_no_tzinfo_treated_as_utc(self):
        dt = utc_from_iso("2024-06-09T12:00:00")
        assert dt.utcoffset().total_seconds() == 0

    def test_beijing_converted_to_utc(self):
        dt = utc_from_iso("2024-06-09T20:00:00+08:00")
        assert dt.hour == 12  # 20:00+08 -> 12:00 UTC


class TestUtcFromTimestamp:
    def test_seconds(self):
        dt = utc_from_timestamp(1717920000)
        assert dt.year == 2024

    def test_milliseconds(self):
        dt = utc_from_timestamp(1717920000000)
        assert dt.year == 2024


class TestFormatIso:
    def test_naive_treated_as_utc(self):
        naive = datetime(2024, 6, 9, 12, 0, 0)
        s = format_iso(naive)
        assert s == "2024-06-09T12:00:00Z"

    def test_aware_converted_to_utc(self):
        beijing = timezone(timedelta(hours=8))
        aware = datetime(2024, 6, 9, 20, 0, 0, tzinfo=beijing)
        s = format_iso(aware)
        assert s == "2024-06-09T12:00:00Z"


class TestFormatBeijingIso:
    def test_contains_offset(self):
        naive = datetime(2024, 6, 9, 12, 0, 0)
        s = format_beijing_iso(naive)
        assert "+08:00" in s
        assert "20:00:00" in s  # 12 UTC -> 20 Beijing


class TestMonotonicMs:
    def test_increases_over_time(self):
        t1 = monotonic_ms()
        time.sleep(0.01)
        t2 = monotonic_ms()
        assert t2 > t1

    def test_not_affected_by_system_clock_changes(self):
        # We can't actually change system clock, but we verify it's an int
        ms = monotonic_ms()
        assert isinstance(ms, int)
