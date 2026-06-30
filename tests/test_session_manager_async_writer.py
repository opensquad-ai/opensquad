"""Tests for SessionManager async batch writer (P0-1 optimization).

Validates:
1. add_event() / add_message() return immediately (non-blocking)
2. Events are eventually persisted to disk
3. tool_call / tool_result still flush synchronously
4. Graceful shutdown drains pending writes
"""

import asyncio
import json
import os
import tempfile

import pytest

from opensquad.session_manager import SessionManager


@pytest.fixture
def temp_session_manager():
    """Create an isolated SessionManager in a temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = SessionManager(save_dir=tmpdir, history_dir=tmpdir)
        yield sm


@pytest.mark.asyncio
async def test_async_writer_starts_and_stops(temp_session_manager):
    sm = temp_session_manager
    assert not sm._writer_running
    sm.start_async_writer()
    assert sm._writer_running
    assert sm._writer_task is not None
    await sm.stop_async_writer()
    assert not sm._writer_running
    assert sm._writer_task is None


@pytest.mark.asyncio
async def test_add_message_returns_immediately(temp_session_manager):
    """add_message() should not block on disk I/O."""
    sm = temp_session_manager
    sm.start_async_writer()

    t0 = asyncio.get_event_loop().time()
    sm.add_message("user", "hello world")
    t1 = asyncio.get_event_loop().time()

    # Should return in < 5ms (queue put is O(1))
    assert (t1 - t0) < 0.005

    # Wait for background flush (generous timeout for CI environments)
    await asyncio.sleep(sm._writer_flush_interval + 0.5)

    # Verify disk persistence
    assert os.path.exists(sm.current_session_file)
    with open(sm.current_session_file, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["messages"]) == 1
    assert data["messages"][0]["content"] == "hello world"

    await sm.stop_async_writer()


@pytest.mark.asyncio
async def test_add_event_non_critical_is_async(temp_session_manager):
    """Non-critical events (e.g. 'thought') should be batched via queue."""
    sm = temp_session_manager
    sm.start_async_writer()

    for i in range(5):
        sm.add_event("thought", {"text": f"thought {i}"})

    # Events are queued, not yet applied to session_data (mutation runs in background)
    # The queue should have 5 items pending
    assert sm._write_queue.qsize() == 5

    # Wait for background flush
    await asyncio.sleep(sm._writer_flush_interval + 0.1)

    # After flush, mutations are applied and persisted
    assert len(sm.session_data["events"]) == 5
    with open(sm.current_session_file, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["events"]) == 5

    await sm.stop_async_writer()


@pytest.mark.asyncio
async def test_tool_call_flushes_sync(temp_session_manager):
    """tool_call events must be on disk immediately (crash-recoverable)."""
    sm = temp_session_manager
    sm.start_async_writer()

    sm.add_event("tool_call", {"tool": "filesystem", "args": {"path": "/tmp"}})

    # Should be on disk immediately, no need to wait for flush interval
    with open(sm.current_session_file, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["events"]) == 1
    assert data["events"][0]["type"] == "tool_call"

    await sm.stop_async_writer()


@pytest.mark.asyncio
async def test_graceful_shutdown_drains_queue(temp_session_manager):
    """stop_async_writer() must flush all pending mutations."""
    sm = temp_session_manager
    sm.start_async_writer()

    # Queue many messages rapidly
    for i in range(50):
        sm.add_message("user", f"msg {i}")

    # Stop immediately — should drain all pending writes
    await sm.stop_async_writer()

    with open(sm.current_session_file, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["messages"]) == 50


@pytest.mark.asyncio
async def test_fallback_when_writer_not_started(temp_session_manager):
    """If start_async_writer() was never called, add_message should still work (sync fallback)."""
    sm = temp_session_manager
    assert not sm._writer_running

    sm.add_message("user", "sync fallback test")

    # Should be on disk immediately (sync fallback)
    with open(sm.current_session_file, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["messages"]) == 1
    assert data["messages"][0]["content"] == "sync fallback test"
