"""Tests for native folder picker helper (logic only; no GUI)."""

from __future__ import annotations

from opensquad.utils import pick_directory as pd


def test_pick_lock_busy_returns_error(monkeypatch):
    """Second concurrent pick should refuse instead of stacking dialogs."""
    assert pd._pick_lock.acquire(blocking=False)
    try:
        result = pd.pick_directory()
        assert result.get("path") is None
        assert "already open" in (result.get("error") or "")
    finally:
        pd._pick_lock.release()


def test_suggest_helpers_exist():
    assert callable(pd.pick_directory)
