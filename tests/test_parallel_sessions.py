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
    is_external_ingress,
    resolve_primary_session_id,
)


def test_is_external_channel():
    assert is_external_channel("telegram")
    assert is_external_channel("telegram_group")
    assert is_external_channel("feishu")
    assert is_external_channel("chatpro")
    assert is_external_channel("chatpro_group")
    assert is_external_channel("chatpro_dm")
    assert not is_external_channel("web")
    assert not is_external_channel("gateway")
    assert not is_external_channel("")


def test_is_external_ingress_sources():
    assert is_external_ingress("group:demo", "")
    assert is_external_ingress("wake", "")
    assert is_external_ingress("chatpro", "")
    assert is_external_ingress("", "chatpro_group")
    assert not is_external_ingress("gateway", "web")
    assert not is_external_ingress("", "web")


def test_start_new_session_archives_previous(tmp_path: Path):
    """A session with content must land in history/ so it appears in the list.

    Empty drafts are deliberately reused on New Session (see
    test_new_session_reuses_empty_draft), so the previous session needs at
    least one message to be archived.
    """
    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    sm = SessionManager(save_dir=str(save), history_dir=str(hist))
    first = sm.get_current_session_id()
    assert first and first != "unknown"
    sm.add_message("user", "seed the session")
    assert sm.session_data.get("messages")

    sm.start_new_session()
    second = sm.get_current_session_id()
    assert second != first
    assert (hist / f"{first}.json").is_file()
    # Promote the new draft so it appears in the sidebar listing.
    sm.add_message("user", "second seed")
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

    sm.add_message("user", "seed")
    sm.start_new_session()
    second = sm.get_current_session_id()
    assert second != first
    # New session does not steal primary
    assert sm.get_primary_session_id() == first

    assert sm.set_primary_session_id(second)
    assert sm.get_primary_session_id() == second
    with open(save / "primary_session.json", encoding="utf-8") as f:
        assert json.load(f)["primary_session_id"] == second

    # Promote the new draft so it appears in the sidebar listing.
    sm.add_message("user", "second seed")
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
    sm.add_message("user", "seed A", sid=sid_a)
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


def test_resolve_primary_session_id(tmp_path: Path):
    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    sm = SessionManager(save_dir=str(save), history_dir=str(hist))
    first = sm.get_current_session_id()
    sm.add_message("user", "seed")
    sm.start_new_session()
    second = sm.get_current_session_id()
    assert second != first
    assert resolve_primary_session_id(sm) == first
    sm.set_primary_session_id(second)
    assert resolve_primary_session_id(sm) == second


@pytest.mark.asyncio
async def test_message_router_idle_pushes_process_queue_to_primary(tmp_path: Path, monkeypatch):
    """Group @mention while idle must wake primary via __PROCESS_QUEUE__ (not focused)."""
    from opensquad import message_router as mr_mod
    from opensquad.message_queue import get_message_queue

    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    sm = SessionManager(save_dir=str(save), history_dir=str(hist))
    first = sm.get_current_session_id()
    sm.add_message("user", "seed")
    sm.start_new_session()
    second = sm.get_current_session_id()
    assert sm.get_primary_session_id() == first
    assert sm.get_focused_session_id() == second
    assert second != first

    hub = InputHub()

    class _State:
        async def get_state(self):
            return "idle"

        async def get_wake_mode(self):
            return "strict"

    monkeypatch.setattr(mr_mod, "get_state_manager", lambda: _State())
    monkeypatch.setattr(
        mr_mod,
        "get_sleep_controller",
        lambda: type("SC", (), {"wake_up": staticmethod(lambda *_: None)})(),
    )
    monkeypatch.setattr(mr_mod.MessageRouter, "_check_mention", staticmethod(lambda *_: True))
    # trigger_process_queue → push_ingress imports get_input_hub locally
    monkeypatch.setattr("opensquad.input_hub.get_input_hub", lambda: hub)
    monkeypatch.setattr(
        "opensquad.ingress_policy.resolve_primary_session_id",
        lambda _sm=None: first,
    )

    get_message_queue().get_all()

    router = mr_mod.MessageRouter()
    result = await router.route_group_message(
        {
            "id": "m1",
            "group_id": "g1",
            "group_name": "demo-group",
            "sender_id": "u1",
            "sender_name": "alice",
            "content": "@agent305 hello",
            "mentions": [],
        }
    )
    assert result.get("pushed") is True
    assert result.get("action") == "push_trigger"

    popped = await hub.wait_any(timeout=1.0)
    assert popped is not None
    sid, item = popped
    assert sid == first
    assert item["content"] == "__PROCESS_QUEUE__"
    assert item.get("channel") == "chatpro_group"


@pytest.mark.asyncio
async def test_dispatcher_process_queue_starts_primary_turn(tmp_path: Path, monkeypatch):
    """Parallel dispatcher must drain message_queue and schedule primary turn."""
    from opensquad import session_dispatcher as sd
    from opensquad.message_queue import QueueMessage, get_message_queue

    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    sm = SessionManager(save_dir=str(save), history_dir=str(hist))
    primary = sm.get_current_session_id()
    sm.add_message("user", "seed")
    sm.start_new_session()  # focused ≠ primary

    q = get_message_queue()
    q.get_all()
    await q.put(
        QueueMessage(
            id=f"m-dispatcher-{time.time()}",
            type="group",
            source_id="g1",
            source_name="demo",
            sender_id="u1",
            sender_name="alice",
            content="@agent hello",
            timestamp=time.time(),
            mentions=[],
            raw_data={},
            images=[],
        )
    )

    hub = InputHub()
    hub.push("__PROCESS_QUEUE__", source="group:demo", session_id=primary, channel="chatpro_group")

    started: list[tuple[str, object]] = []

    class _Sched:
        busy_sessions: set[str] = set()

        def reap(self):
            return None

        def is_session_busy(self, sid: str) -> bool:
            return False

        def request_stop_session(self, sid: str):
            return None

        async def acquire_slot(self, sid: str) -> bool:
            return True

        def start(self, sid: str, coro):
            started.append((sid, coro))
            if asyncio.iscoroutine(coro):
                coro.close()

    class _Runner:
        _agent_id = "agent305"
        _current_images: list = []

        async def _emit(self, *a, **k):
            return None

        async def _emit_busy_sessions(self, busy):
            return None

        async def _handle_agent_level_command(self, item):
            raise AssertionError(f"must not ignore PROCESS_QUEUE: {item}")

        async def _parallel_session_turn(self, sid, item):
            return None

        async def _dispatcher_idle_tick(self):
            return None

    monkeypatch.setattr("opensquad.session_manager.get_session_manager", lambda: sm)
    monkeypatch.setattr(sd, "get_input_hub", lambda: hub)
    monkeypatch.setattr(sd, "ParallelTurnScheduler", lambda max_parallel=4: _Sched())
    monkeypatch.setattr(sd, "resolve_primary_session_id", lambda _sm=None: primary)
    monkeypatch.setattr(sd, "resolve_session_id", lambda **kwargs: primary)

    task = asyncio.create_task(sd.run_parallel_dispatcher(_Runner()))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert started, "expected a primary turn to be scheduled"
    assert started[0][0] == primary
