"""Tests for parallel multi-session + primary session routing."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from opensquad.input_hub import InputHub
from opensquad.session_manager import SessionManager
from opensquad.session_parallel import (
    MAX_PARALLEL_TURNS,
    ParallelTurnScheduler,
    is_external_channel,
)


def test_is_external_channel():
    assert is_external_channel("telegram")
    assert is_external_channel("telegram_group")
    assert is_external_channel("feishu")
    assert not is_external_channel("web")
    assert not is_external_channel("gateway")
    assert not is_external_channel("")


def test_start_new_session_archives_empty(tmp_path: Path):
    """Empty previous session must still land in history/ so it appears in the list."""
    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    sm = SessionManager(save_dir=str(save), history_dir=str(hist))
    first = sm.get_current_session_id()
    assert first and first != "unknown"
    assert not (sm.session_data.get("messages") or [])

    sm.start_new_session()
    second = sm.get_current_session_id()
    assert second != first
    assert (hist / f"{first}.json").is_file()
    listing = sm.get_session_list()
    ids = {s["id"] for s in listing}
    assert first in ids
    assert second in ids


def test_delete_empty_current_abandons(tmp_path: Path):
    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    sm = SessionManager(save_dir=str(save), history_dir=str(hist))
    first = sm.get_current_session_id()
    assert sm.delete_session(first) is True
    second = sm.get_current_session_id()
    assert second and second != first


def test_primary_session_default_and_rebind(tmp_path: Path):
    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    sm = SessionManager(save_dir=str(save), history_dir=str(hist))
    first = sm.get_current_session_id()
    assert first and first != "unknown"
    assert sm.get_primary_session_id() == first
    assert (save / "primary_session.json").is_file()

    sm.start_new_session()
    second = sm.get_current_session_id()
    assert second != first
    # New session does not steal primary
    assert sm.get_primary_session_id() == first

    assert sm.set_primary_session_id(second)
    assert sm.get_primary_session_id() == second
    with open(save / "primary_session.json", encoding="utf-8") as f:
        assert json.load(f)["primary_session_id"] == second

    listing = sm.get_session_list()
    primaries = [s for s in listing if s.get("primary")]
    assert len(primaries) == 1
    assert primaries[0]["id"] == second


def test_concurrent_add_message_two_sessions(tmp_path: Path):
    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    sm = SessionManager(save_dir=str(save), history_dir=str(hist))
    sid_a = sm.get_current_session_id()
    sm.start_new_session()
    sid_b = sm.get_current_session_id()
    # Keep A live
    sm.ensure_session_loaded(sid_a)

    errors: list[BaseException] = []

    def write_a():
        try:
            for i in range(20):
                sm.add_message("user", f"A-{i}", sid=sid_a)
                time.sleep(0.001)
        except BaseException as e:
            errors.append(e)

    def write_b():
        try:
            for i in range(20):
                sm.add_message("user", f"B-{i}", sid=sid_b)
                time.sleep(0.001)
        except BaseException as e:
            errors.append(e)

    t1 = threading.Thread(target=write_a)
    t2 = threading.Thread(target=write_b)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors

    sm.flush()
    msgs_a = sm.get_messages(sid=sid_a)
    msgs_b = sm.get_messages(sid=sid_b)
    assert any("A-" in (m.get("content") or "") for m in msgs_a)
    assert any("B-" in (m.get("content") or "") for m in msgs_b)
    assert not any("B-" in (m.get("content") or "") for m in msgs_a)
    assert not any("A-" in (m.get("content") or "") for m in msgs_b)


@pytest.mark.asyncio
async def test_input_hub_wait_any_per_session():
    hub = InputHub()
    hub.push("hello-a", source="test", session_id="sid-a", channel="web")
    hub.push("hello-b", source="test", session_id="sid-b", channel="web")

    first = await hub.wait_any(timeout=1.0)
    assert first is not None
    sid1, item1 = first
    assert sid1 in ("sid-a", "sid-b")
    assert "hello" in item1["content"]

    second = await hub.wait_any(timeout=1.0)
    assert second is not None
    sid2, item2 = second
    assert {sid1, sid2} == {"sid-a", "sid-b"}
    assert item1["content"] != item2["content"]


@pytest.mark.asyncio
async def test_parallel_scheduler_slots():
    sched = ParallelTurnScheduler(max_parallel=2)
    assert MAX_PARALLEL_TURNS >= 1

    async def fake_turn(delay: float):
        await asyncio.sleep(delay)

    ok1 = await sched.acquire_slot("s1")
    assert ok1
    sched.start("s1", fake_turn(0.05))
    ok2 = await sched.acquire_slot("s2")
    assert ok2
    sched.start("s2", fake_turn(0.05))
    assert sched.is_session_busy("s1")
    assert "s1" in sched.busy_sessions
    # Same sid while busy → False
    assert await sched.acquire_slot("s1") is False
    await asyncio.sleep(0.12)
    sched.reap()
    assert not sched.is_session_busy("s1")
