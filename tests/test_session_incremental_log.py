"""Peer tests for the incremental log + throttled snapshot persistence.

The agent appends O(1) seq-tagged records to history/{sid}.json.log between
throttled full snapshots. These tests verify the core invariant:

    snapshot + log replay == live in-memory state

for the agent-side SessionManager, the gateway-side AgentSessionReader
(cross-process mirror), and crash recovery (corrupt tail tolerance,
truncate/archive superseding the log).
"""

import asyncio
import importlib.util
import json
import os
from pathlib import Path

import pytest

from opensquad.session_manager import SessionManager

# ── Direct import of the gateway reader (same mechanism as test_agent_sessions.py) ──
_AGENT_SESSIONS_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "opensquad",
        "gateway",
        "backend",
        "app",
        "ai_web",
        "agent_sessions.py",
    )
)
_spec = importlib.util.spec_from_file_location("agent_sessions_log_test", _AGENT_SESSIONS_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
AgentSessionReader = _mod.AgentSessionReader


def _make_sm(tmp_path) -> SessionManager:
    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    return SessionManager(save_dir=str(save), history_dir=str(hist))


def _log_records(sm: SessionManager) -> list[dict]:
    sid = sm.get_current_session_id()
    log_path = os.path.join(sm.history_dir, f"{sid}.json.log")
    if not os.path.exists(log_path):
        return []
    with open(log_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f.read().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_log_replay_matches_snapshot_content(tmp_path: Path):
    """Crash recovery must reconstruct exactly the in-memory state.

    Writer era flow: first flush takes a fresh snapshot (and truncates the
    log), later flushes only append incremental records — so the snapshot
    file goes stale while the log carries the durable tail.
    """
    sm = _make_sm(tmp_path)
    sm.start_async_writer()

    # Era-first flush → snapshot (log truncated right after).
    sm.add_message("user", "hello")
    await asyncio.sleep(sm._writer_flush_interval + 0.1)

    # Throttled era: only log records hit disk, the snapshot stays stale.
    sm.add_message("assistant", "hi there")
    sm.add_event("thought", {"text": "thinking"})
    sm.add_event("tool_call", {"tool": "filesystem", "args": {"path": "/tmp"}})
    sm.update_last_message_elapsed_ms(4321)
    sm.set_title("peer test")
    await asyncio.sleep(sm._writer_flush_interval + 0.1)

    # Sanity: the snapshot file is the stale pre-second-batch state...
    with open(sm.current_session_file, encoding="utf-8") as f:
        snap = json.load(f)
    assert len(snap["messages"]) == 1

    # ...and the log carries the delta records.
    recs = _log_records(sm)
    ops = [r["op"] for r in recs]
    assert "msg_append" in ops and "evt_append" in ops and "tail_patch" in ops and "meta" in ops

    # Crash recovery: a fresh manager must reconstruct the exact live state.
    sm2 = SessionManager(save_dir=sm.save_dir, history_dir=sm.history_dir)
    live_msgs = sm.get_messages()
    rec_msgs = sm2.get_messages()
    assert len(rec_msgs) == len(live_msgs) == 2
    assert [m["content"] for m in rec_msgs] == [m["content"] for m in live_msgs]
    assert rec_msgs[-1]["elapsed_ms"] == 4321
    assert sm2.get_events() == sm.get_events()
    assert sm2.get_title() == "peer test"
    # Focused snapshot on disk now equals the replayed state.
    sm2.flush()
    with open(sm2.current_session_file, encoding="utf-8") as f:
        snap2 = json.load(f)
    assert len(snap2["messages"]) == 2
    assert snap2["messages"][-1]["elapsed_ms"] == 4321
    assert len(snap2["events"]) == 2

    await sm.stop_async_writer()


@pytest.mark.asyncio
async def test_log_replay_tolerates_corrupt_tail(tmp_path: Path):
    """A partial (corrupt) tail line from a crash must not block replay."""
    sm = _make_sm(tmp_path)
    sm.start_async_writer()
    sm.add_message("user", "seed")
    await asyncio.sleep(sm._writer_flush_interval + 0.1)  # era snapshot

    sm.add_message("assistant", "survives")
    await asyncio.sleep(sm._writer_flush_interval + 0.1)

    # Simulate a crash mid-append: garbage tail line.
    sid = sm.get_current_session_id()
    log_path = os.path.join(sm.history_dir, f"{sid}.json.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write('{"seq": 999999, "op": "msg_appen')  # truncated json

    sm2 = SessionManager(save_dir=sm.save_dir, history_dir=sm.history_dir)
    assert [m["content"] for m in sm2.get_messages()] == ["seed", "survives"]

    await sm.stop_async_writer()


@pytest.mark.asyncio
async def test_gateway_reader_replays_incremental_log(tmp_path: Path):
    """Gateway AgentSessionReader (cross-process) sees the log tail."""
    sm = _make_sm(tmp_path)
    sm.start_async_writer()
    sm.add_message("user", "seed")
    await asyncio.sleep(sm._writer_flush_interval + 0.1)  # era snapshot
    sm.add_message("assistant", "from the log")
    sm.add_event("thought", {"text": "gateway sees me"})
    await asyncio.sleep(sm._writer_flush_interval + 0.1)  # log-only tail

    reader = AgentSessionReader(save_dir=sm.save_dir, history_dir=sm.history_dir)
    data = reader.get_session_history(sm.get_current_session_id())
    assert data is not None
    assert [m["content"] for m in data["messages"]] == ["seed", "from the log"]
    assert [e["type"] for e in data["events"]] == ["thought"]

    # Roll over so the old sid becomes a deletable history session.
    sid = sm.get_current_session_id()
    assert reader.delete_session(sid) is False  # current session is protected
    sm.start_new_session()
    assert sm.get_current_session_id() != sid

    reader2 = AgentSessionReader(save_dir=sm.save_dir, history_dir=sm.history_dir)
    assert reader2.delete_session(sid) is True
    assert not os.path.exists(os.path.join(sm.history_dir, f"{sid}.json"))
    assert not os.path.exists(os.path.join(sm.history_dir, f"{sid}.json.log"))

    await sm.stop_async_writer()


@pytest.mark.asyncio
async def test_start_new_session_archives_supersede_log(tmp_path):
    """Archive writes must truncate the old session's log (complete snapshot)."""
    sm = _make_sm(tmp_path)
    sm.start_async_writer()
    sm.add_message("user", "seed")
    await asyncio.sleep(sm._writer_flush_interval + 0.1)  # era snapshot
    sm.add_message("assistant", "tail")
    await asyncio.sleep(sm._writer_flush_interval + 0.1)  # log-only tail

    old_sid = sm.get_current_session_id()
    assert _log_records(sm)  # log non-empty before rollover

    sm.start_new_session()
    # Old log truncated; archived json is a complete standalone snapshot.
    assert not os.path.exists(os.path.join(sm.history_dir, f"{old_sid}.json.log"))
    with open(os.path.join(sm.history_dir, f"{old_sid}.json"), encoding="utf-8") as f:
        archived = json.load(f)
    assert [m["content"] for m in archived["messages"]] == ["seed", "tail"]

    # The new draft has no stale log.
    assert not _log_records(sm)

    await sm.stop_async_writer()
