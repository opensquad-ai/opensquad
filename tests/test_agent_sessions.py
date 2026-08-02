"""Tests for agent_sessions.py — Agent session reader (local, HTTP, WS).

Covers:
- AgentSessionReader (local disk) with tmp_path and mtime/LRU caching
- _RemoteSessionReader (HTTP proxy) with mocked httpx
- _WsSessionReader (WS tunnel) with fake RPC
- async_get_reader / get_reader factory functions
- refresh_agent_id_map / invalidate_reader
"""

import importlib.util
import json
import os
from unittest.mock import MagicMock, patch

import pytest

# ── Direct import of agent_sessions.py (bypass ai_web/__init__.py eagerly
#    importing routes which cascades into app.api, app.models, etc.) ──────────
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
_spec = importlib.util.spec_from_file_location(
    "agent_sessions_test",
    _AGENT_SESSIONS_PATH,
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Export all names needed by tests
AgentSessionReader = _mod.AgentSessionReader
_RemoteSessionReader = _mod._RemoteSessionReader
_WsSessionReader = _mod._WsSessionReader
async_get_reader = _mod.async_get_reader
get_reader = _mod.get_reader
invalidate_reader = _mod.invalidate_reader
refresh_agent_id_map = _mod.refresh_agent_id_map
set_ws_handler = _mod.set_ws_handler


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset module-level state between tests to avoid cross-test pollution."""
    _mod._readers.clear()
    _mod._agent_id_map = None
    _mod._ws_rpc = None
    _mod._ws_node_id_func = None


@pytest.fixture
def local_reader(tmp_path):
    """Create an AgentSessionReader with pre-created temp save/history dirs."""
    save_dir = str(tmp_path / "sessions")
    history_dir = str(tmp_path / "history")
    os.makedirs(save_dir, exist_ok=True)
    os.makedirs(history_dir, exist_ok=True)
    return AgentSessionReader(save_dir, history_dir)


def _make_agent_dir(root, agent_name, agent_id):
    """Create a fake agent directory with config.json and data/ subdirs."""
    agent_dir = root / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "config.json").write_text(
        json.dumps({"agent_id": agent_id, "agent_name": agent_name}),
        encoding="utf-8",
    )
    (agent_dir / "data" / "sessions").mkdir(parents=True, exist_ok=True)
    (agent_dir / "data" / "history").mkdir(parents=True, exist_ok=True)
    return str(agent_dir)


# =====================================================================
# AgentSessionReader — local disk reader
# =====================================================================


class TestAgentSessionReader:
    """AgentSessionReader: local disk session reader tests."""

    # ── Init ──────────────────────────────────────────────────────────

    def test_init_creates_directories(self, tmp_path):
        """AgentSessionReader stores directory paths and works with existing dirs."""
        save_dir = str(tmp_path / "sessions")
        history_dir = str(tmp_path / "history")
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        reader = AgentSessionReader(save_dir, history_dir)
        assert reader.save_dir == save_dir
        assert reader.history_dir == history_dir
        assert reader.current_session_file == os.path.join(
            save_dir,
            "current_session.json",
        )
        assert os.path.isdir(save_dir)
        assert os.path.isdir(history_dir)

    def test_init_without_existing_dirs(self, tmp_path):
        """Gracefully handles non-existent directories (no crash)."""
        reader = AgentSessionReader(
            str(tmp_path / "nonexistent" / "sessions"),
            str(tmp_path / "nonexistent" / "history"),
        )
        # session_data is initialised with id=None (not "unknown") when
        # no file exists; get_current_session_id returns None.
        assert reader.get_current_session_id() is None
        assert reader.get_session_list() == []

    def test_session_data_defaults_when_missing(self, tmp_path):
        """_reload fills missing id/title/events keys after reading a file."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        # Minimal JSON without id, title, or events
        (save_dir / "current_session.json").write_text(
            json.dumps({"messages": [{"role": "user", "content": "Hi"}]}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        assert reader.session_data["id"] == "unknown"
        assert reader.session_data["title"] is None
        assert reader.session_data["events"] == []

    # ── get_current_session_id ────────────────────────────────────────

    def test_get_current_session_id_no_file(self, local_reader):
        """Returns None when no current_session.json exists (id is None in init)."""
        sid = local_reader.get_current_session_id()
        assert sid is None

    def test_get_current_session_id_with_file(self, tmp_path):
        """Reads session ID from current_session.json."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "session-abc", "messages": []}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        assert reader.get_current_session_id() == "session-abc"

    # ── get_session_list ──────────────────────────────────────────────

    def test_get_session_list_empty(self, local_reader):
        """Returns empty list when no sessions exist."""
        assert local_reader.get_session_list() == []

    def test_get_session_list_with_current_only(self, tmp_path):
        """Returns list with current session when no history files exist."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps(
                {
                    "id": "cur-1",
                    "title": "My Chat",
                    "messages": [
                        {"role": "user", "content": "Hello"},
                        {"role": "assistant", "content": "<title>My Chat</title>Hi"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        sessions = reader.get_session_list()
        assert len(sessions) == 1
        assert sessions[0]["id"] == "cur-1"
        assert sessions[0]["title"] == "My Chat"
        assert sessions[0]["current"] is True

    def test_get_session_list_with_history(self, tmp_path):
        """Returns current + history sessions, newest first by mtime."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        # Current
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "title": "Current", "messages": []}),
            encoding="utf-8",
        )
        # History
        (history_dir / "hist-1.json").write_text(
            json.dumps({"id": "hist-1", "title": "Hist 1", "messages": []}),
            encoding="utf-8",
        )
        import time

        time.sleep(0.01)
        (history_dir / "hist-2.json").write_text(
            json.dumps({"id": "hist-2", "title": "Hist 2", "messages": []}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        sessions = reader.get_session_list()
        assert len(sessions) == 3
        by_id = {s["id"]: s for s in sessions}
        assert by_id["cur-1"]["current"] is True
        assert by_id["hist-1"]["current"] is False
        assert by_id["hist-2"]["current"] is False

    def test_get_session_list_pagination(self, tmp_path):
        """Paged list returns the newest page without scanning all files."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "title": "Current", "messages": []}),
            encoding="utf-8",
        )
        for idx in range(3):
            path = history_dir / f"hist-{idx}.json"
            path.write_text(
                json.dumps({"id": f"hist-{idx}", "title": f"Hist {idx}", "messages": []}),
                encoding="utf-8",
            )
            ts = 1_700_000_000 + idx
            os.utime(path, (ts, ts))

        reader = AgentSessionReader(str(save_dir), str(history_dir))

        first = reader.get_session_list(limit=2, offset=0)
        second = reader.get_session_list(limit=2, offset=2)

        assert [s["id"] for s in first] == ["cur-1", "hist-2"]
        assert [s["id"] for s in second] == ["hist-1", "hist-0"]

    def test_current_session_log_reload_avoids_snapshot_reread(self, tmp_path, monkeypatch):
        """New incremental log lines replay without re-opening current_session.json."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        current_path = save_dir / "current_session.json"
        current_path.write_text(
            json.dumps({"id": "cur-1", "_save_seq": 0, "messages": [], "events": []}),
            encoding="utf-8",
        )

        reader = AgentSessionReader(str(save_dir), str(history_dir))
        log_path = history_dir / "cur-1.json.log"
        log_path.write_text(
            json.dumps({"seq": 1, "op": "msg_append", "msg": {"role": "user", "content": "hi"}}) + "\n",
            encoding="utf-8",
        )

        real_open = open
        open_calls = {"current": 0}

        def counting_open(*args, **kwargs):
            path = args[0] if args else kwargs.get("file")
            if str(path) == str(current_path):
                open_calls["current"] += 1
            return real_open(*args, **kwargs)

        monkeypatch.setattr("builtins.open", counting_open)
        reader._reload()

        assert open_calls["current"] == 0
        assert [m["content"] for m in reader.session_data["messages"]] == ["hi"]

    def test_get_session_list_meta_cache_skips_reread(self, tmp_path, monkeypatch):
        """Second list call with unchanged mtime must not re-parse history JSON."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "title": "Current", "messages": []}),
            encoding="utf-8",
        )
        hist_path = history_dir / "big.json"
        hist_path.write_text(
            json.dumps(
                {
                    "id": "big",
                    "title": "Big Session",
                    "messages": [{"role": "user", "content": f"m{i}"} for i in range(200)],
                    "last_updated": "2026-01-01T00:00:00Z",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        first = reader.get_session_list()
        assert any(s["id"] == "big" and s["title"] == "Big Session" for s in first)
        assert "big" in reader._list_meta_cache

        open_calls = {"n": 0}
        real_open = open

        def counting_open(*args, **kwargs):
            path = args[0] if args else kwargs.get("file")
            if str(path).endswith("big.json"):
                open_calls["n"] += 1
            return real_open(*args, **kwargs)

        monkeypatch.setattr("builtins.open", counting_open)
        second = reader.get_session_list()
        assert any(s["id"] == "big" and s["title"] == "Big Session" for s in second)
        assert open_calls["n"] == 0

    def test_get_session_list_title_from_head_only(self, tmp_path):
        """Without top-level title, extract from message head — not full scan needed."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        (history_dir / "t1.json").write_text(
            json.dumps(
                {
                    "id": "t1",
                    "messages": [
                        {"role": "user", "content": "Hello world title candidate"},
                        {"role": "assistant", "content": "ok"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        sessions = reader.get_session_list()
        by_id = {s["id"]: s for s in sessions}
        assert "Hello world" in by_id["t1"]["title"]

    # ── get_session_history ───────────────────────────────────────────

    def test_get_session_history_no_file(self, local_reader):
        """Returns None for non-existent history session."""
        assert local_reader.get_session_history("nonexistent") is None

    def test_get_session_history_returns_current(self, tmp_path):
        """Reads current session data when session_id matches current."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps(
                {
                    "id": "cur-1",
                    "title": "Current",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "events": [
                        {"name": "user_message", "data": "hello"},
                        {"name": "system__event_pipeline", "data": "internal"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        result = reader.get_session_history("cur-1")
        assert result is not None
        assert result["id"] == "cur-1"
        # Synthetic event should be filtered out
        names = [e["name"] for e in result["events"]]
        assert "system__event_pipeline" not in names
        assert "user_message" in names

    def test_get_session_history_from_disk(self, tmp_path):
        """Reads a history session from disk (not cached)."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        (history_dir / "hist-on-disk.json").write_text(
            json.dumps(
                {
                    "id": "hist-on-disk",
                    "title": "Disk Session",
                    "messages": [{"role": "user", "content": "Disk data"}],
                }
            ),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        result = reader.get_session_history("hist-on-disk")
        assert result is not None
        assert result["id"] == "hist-on-disk"
        assert result["title"] == "Disk Session"

    def test_get_session_history_list_format(self, tmp_path):
        """Handles top-level list format (array of messages)."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        (history_dir / "hist-list.json").write_text(
            json.dumps(
                [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi"},
                ]
            ),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        result = reader.get_session_history("hist-list")
        assert result is not None
        assert result["id"] == "hist-list"
        assert len(result["messages"]) == 2
        assert result["events"] == []
        assert "last_updated" in result
        assert "created_at" in result

    # ── invalidate_current_session_cache ──────────────────────────────

    def test_invalidate_clears_cache(self, tmp_path):
        """Clears mtime cache and re-reads current_session.json from disk."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        # Start with one session
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "old-session", "messages": []}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        assert reader.get_current_session_id() == "old-session"

        # Modify file on disk
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "new-session", "messages": []}),
            encoding="utf-8",
        )
        # Invalidate then re-read
        reader.invalidate_current_session_cache()
        assert reader.get_current_session_id() == "new-session"

    def test_invalidate_also_clears_history_cache(self, tmp_path):
        """Invalidate clears the history LRU cache (mtime is re-set after re-read)."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        # Populate the LRU cache
        (history_dir / "hist-a.json").write_text(
            json.dumps({"messages": [{"role": "user", "content": "A"}]}),
            encoding="utf-8",
        )
        assert reader.get_session_history("hist-a") is not None
        assert "hist-a" in reader._cache

        reader.invalidate_current_session_cache()
        # History LRU cache is cleared
        assert len(reader._cache) == 0

    # ── delete_session ────────────────────────────────────────────────

    def test_delete_session(self, tmp_path):
        """Deletes a history session file from disk and removes from cache."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        hist_path = history_dir / "hist-1.json"
        hist_path.write_text(
            json.dumps({"messages": [{"role": "user", "content": "Delete me"}]}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        # Populate cache
        reader.get_session_history("hist-1")
        assert "hist-1" in reader._cache

        assert reader.delete_session("hist-1") is True
        assert not os.path.exists(hist_path)
        assert "hist-1" not in reader._cache

    def test_delete_current_session_fails(self, tmp_path):
        """Cannot delete the current session (even when empty)."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        assert reader.delete_session("cur-1") is False

    def test_delete_empty_current_after_archive(self, tmp_path):
        """After archiving, the previous empty session is a history file and deletable."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-2", "messages": []}),
            encoding="utf-8",
        )
        hist_path = history_dir / "cur-1.json"
        hist_path.write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        assert reader.delete_session("cur-1") is True
        assert not hist_path.exists()

    def test_delete_non_existent_session(self, local_reader):
        """Returns False for a session that does not exist."""
        assert local_reader.delete_session("no-such") is False

    # ── LRU cache ─────────────────────────────────────────────────────

    def test_lru_cache_eviction(self, tmp_path):
        """LRU cache evicts the oldest entry when max size exceeded."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        reader._cache_max_size = 3

        for i in range(4):
            (history_dir / f"hist-{i}.json").write_text(
                json.dumps({"messages": [{"role": "user", "content": str(i)}]}),
                encoding="utf-8",
            )
            reader.get_session_history(f"hist-{i}")

        assert len(reader._cache) == 3
        assert "hist-0" not in reader._cache  # oldest evicted
        for i in (1, 2, 3):
            assert f"hist-{i}" in reader._cache

    def test_lru_cache_hit_moves_to_end(self, tmp_path):
        """Accessing a cached entry moves it to the MRU position."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        reader._cache_max_size = 2

        for i in range(2):
            (history_dir / f"hist-{i}.json").write_text(
                json.dumps({"messages": [{"role": "user", "content": str(i)}]}),
                encoding="utf-8",
            )
            reader.get_session_history(f"hist-{i}")

        # Order: hist-0 (LRU), hist-1 (MRU)
        assert list(reader._cache.keys()) == ["hist-0", "hist-1"]

        # Access hist-0 again → moves to end
        reader.get_session_history("hist-0")
        assert list(reader._cache.keys()) == ["hist-1", "hist-0"]

    # ── get_session_history_paged ─────────────────────────────────────

    def test_get_session_history_paged_basic(self, tmp_path):
        """Returns sliced messages from the end with offset=0."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        (history_dir / "paged.json").write_text(
            json.dumps({"messages": msgs, "events": []}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))

        r1 = reader.get_session_history_paged("paged", offset=0, limit=5)
        assert r1 is not None
        assert len(r1["messages"]) == 5
        assert r1["messages"][0]["content"] == "msg 15"
        assert r1["messages"][-1]["content"] == "msg 19"
        assert r1["total_messages"] == 20
        assert r1["has_more"] is True

        r2 = reader.get_session_history_paged("paged", offset=5, limit=5)
        assert len(r2["messages"]) == 5
        assert r2["messages"][0]["content"] == "msg 10"

        # Last page
        r3 = reader.get_session_history_paged("paged", offset=15, limit=10)
        assert len(r3["messages"]) == 5
        assert r3["has_more"] is False

    def test_get_session_history_paged_archived_only_on_first_page(self, tmp_path):
        """archived_* is returned only for offset=0 to keep later pages light."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        archived = [{"role": "user", "content": "old archived"}]
        archived_ev = [{"name": "tool_use", "data": "ls"}]
        (history_dir / "paged.json").write_text(
            json.dumps(
                {
                    "messages": msgs,
                    "events": [],
                    "archived_messages": archived,
                    "archived_events": archived_ev,
                }
            ),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))

        r0 = reader.get_session_history_paged("paged", offset=0, limit=5)
        assert r0["archived_messages"] == archived
        assert r0["archived_events"] == archived_ev

        r1 = reader.get_session_history_paged("paged", offset=5, limit=5)
        assert r1["archived_messages"] == []
        assert r1["archived_events"] == []

    def test_cache_get_returns_shallow_copy(self, tmp_path):
        """Default cache get avoids deepcopy while still isolating list containers."""
        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(history_dir, exist_ok=True)
        (save_dir / "current_session.json").write_text(
            json.dumps({"id": "cur-1", "messages": []}),
            encoding="utf-8",
        )
        (history_dir / "s1.json").write_text(
            json.dumps({"id": "s1", "messages": [{"role": "user", "content": "a"}], "events": []}),
            encoding="utf-8",
        )
        reader = AgentSessionReader(str(save_dir), str(history_dir))
        first = reader.get_session_history("s1")
        assert first is not None
        second = reader._cache_get("s1")
        assert second is not None
        assert second["messages"] is not first["messages"]
        # Mutating the list container must not mutate the cache entry's list.
        second["messages"].append({"role": "user", "content": "b"})
        third = reader._cache_get("s1")
        assert len(third["messages"]) == 1

    def test_get_session_history_paged_no_session(self, local_reader):
        """Returns None for non-existent session."""
        assert local_reader.get_session_history_paged("no-such", 0, 10) is None

    # ── _filter_events ────────────────────────────────────────────────

    def test_filter_events_strips_pipeline(self):
        """_filter_events removes synthetic system__event_pipeline entries."""
        events = [
            {"name": "user_message", "data": "hello"},
            {"name": "system__event_pipeline", "data": "internal"},
            {"name": "system.event_pipeline", "data": "internal"},
            {"name": "tool_use", "data": "ls"},
        ]
        filtered = AgentSessionReader._filter_events(events)
        names = [e["name"] for e in filtered]
        assert "user_message" in names
        assert "tool_use" in names
        assert "system__event_pipeline" not in names
        assert "system.event_pipeline" not in names

    # ── Async wrappers ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_async_get_session_list(self, local_reader):
        """Async wrapper delegates to sync method."""
        assert await local_reader.async_get_session_list() == []

    @pytest.mark.asyncio
    async def test_async_get_current_session_id(self, local_reader):
        """Async wrapper returns current session id from sync method."""
        sid = await local_reader.async_get_current_session_id()
        # No file exists ⇒ id is None
        assert sid is None

    @pytest.mark.asyncio
    async def test_async_get_session_history(self, local_reader):
        """Async wrapper returns None for missing session."""
        assert await local_reader.async_get_session_history("no-such") is None

    @pytest.mark.asyncio
    async def test_async_get_session_history_paged(self, local_reader):
        """Async wrapper for paged history."""
        assert await local_reader.async_get_session_history_paged("no-such", 0, 5) is None

    @pytest.mark.asyncio
    async def test_async_delete_session(self, local_reader):
        """Async wrapper for delete."""
        assert await local_reader.async_delete_session("no-such") is False


# =====================================================================
# _RemoteSessionReader — HTTP proxy reader
# =====================================================================


class TestRemoteSessionReader:
    """_RemoteSessionReader: HTTP-based remote session reader tests."""

    # ── Helper ────────────────────────────────────────────────────────

    def _make_client_mock(self, json_data, raises=None):
        """Build a mock httpx.Client for _RemoteSessionReader tests."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_data
        mock_resp.raise_for_status = MagicMock()

        mock_inst = MagicMock()
        if raises:
            mock_inst.get.side_effect = raises
            mock_inst.post.side_effect = raises
        else:
            mock_inst.get.return_value = mock_resp
            mock_inst.post.return_value = mock_resp
        mock_inst.__enter__.return_value = mock_inst
        return mock_inst

    # ── Sync methods ──────────────────────────────────────────────────

    @patch("httpx.Client")
    def test_remote_get_session_list(self, mock_cls):
        """Returns session list from mock HTTP response."""
        mock_inst = self._make_client_mock(
            {
                "sessions": [
                    {"id": "s1", "title": "Remote 1"},
                    {"id": "s2", "title": "Remote 2"},
                ],
            }
        )
        mock_cls.return_value = mock_inst

        with patch.object(_mod.syscfg, "launcher_url", return_value="http://launcher:8080"):
            reader = _RemoteSessionReader("agent-1")
            sessions = reader.get_session_list()

        assert len(sessions) == 2
        assert sessions[0]["id"] == "s1"
        mock_inst.get.assert_called_once_with(
            "http://launcher:8080/api/sessions/agent-1/list",
            params={},
        )

    @patch("httpx.Client")
    def test_remote_get_current_session_id(self, mock_cls):
        """Returns current_session_id from list endpoint."""
        mock_inst = self._make_client_mock(
            {
                "sessions": [],
                "current_session_id": "cur-remote",
            }
        )
        mock_cls.return_value = mock_inst

        with patch.object(_mod.syscfg, "launcher_url", return_value="http://launcher:8080"):
            reader = _RemoteSessionReader("agent-1")
            assert reader.get_current_session_id() == "cur-remote"

    @patch("httpx.Client")
    def test_remote_get_session_history(self, mock_cls):
        """Returns session data from history endpoint."""
        mock_inst = self._make_client_mock(
            {
                "session": {"id": "s1", "title": "Remote", "messages": []},
            }
        )
        mock_cls.return_value = mock_inst

        with patch.object(_mod.syscfg, "launcher_url", return_value="http://launcher:8080"):
            reader = _RemoteSessionReader("agent-1")
            session = reader.get_session_history("s1")

        assert session is not None
        assert session["id"] == "s1"

    @patch("httpx.Client")
    def test_remote_get_session_history_failed(self, mock_cls):
        """Returns None when HTTP call raises."""
        mock_inst = self._make_client_mock(None, raises=Exception("timeout"))
        mock_cls.return_value = mock_inst

        with patch.object(_mod.syscfg, "launcher_url", return_value="http://launcher:8080"):
            reader = _RemoteSessionReader("agent-1")
            assert reader.get_session_history("s1") is None

    @patch("httpx.Client")
    def test_remote_get_session_history_paged(self, mock_cls):
        """Returns paged data and passes query parameters."""
        mock_inst = self._make_client_mock(
            {
                "session": {"id": "s1", "has_more": True, "messages": []},
            }
        )
        mock_cls.return_value = mock_inst

        with patch.object(_mod.syscfg, "launcher_url", return_value="http://launcher:8080"):
            reader = _RemoteSessionReader("agent-1")
            result = reader.get_session_history_paged("s1", offset=5, limit=10)

        assert result is not None
        assert result["has_more"] is True
        _, kwargs = mock_inst.get.call_args
        assert kwargs["params"] == {"offset": 5, "limit": 10}

    @patch("httpx.Client")
    def test_remote_get_session_history_paged_failed(self, mock_cls):
        """Returns None when paged HTTP call raises."""
        mock_inst = self._make_client_mock(None, raises=Exception("timeout"))
        mock_cls.return_value = mock_inst

        with patch.object(_mod.syscfg, "launcher_url", return_value="http://launcher:8080"):
            reader = _RemoteSessionReader("agent-1")
            assert reader.get_session_history_paged("s1", 0, 10) is None

    @patch("httpx.Client")
    def test_remote_delete_session(self, mock_cls):
        """Calls POST on delete endpoint."""
        mock_inst = self._make_client_mock({"ok": True})
        mock_cls.return_value = mock_inst

        with patch.object(_mod.syscfg, "launcher_url", return_value="http://launcher:8080"):
            reader = _RemoteSessionReader("agent-1")
            assert reader.delete_session("s1") is True

        mock_inst.post.assert_called_once_with(
            "http://launcher:8080/api/sessions/agent-1/s1/delete",
        )

    @patch("httpx.Client")
    def test_remote_delete_session_failed(self, mock_cls):
        """Returns False when POST raises."""
        mock_inst = self._make_client_mock({"ok": True}, raises=Exception("timeout"))
        mock_cls.return_value = mock_inst

        with patch.object(_mod.syscfg, "launcher_url", return_value="http://launcher:8080"):
            reader = _RemoteSessionReader("agent-1")
            assert reader.delete_session("s1") is False

    # ── Async wrappers ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_remote_async_get_session_list(self):
        """Async wrapper delegates via asyncio.to_thread."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"sessions": [{"id": "s1"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_inst = MagicMock()
        mock_inst.get.return_value = mock_resp
        mock_inst.__enter__.return_value = mock_inst

        with (
            patch.object(_mod.syscfg, "launcher_url", return_value="http://launcher:8080"),
            patch("httpx.Client", return_value=mock_inst),
        ):
            reader = _RemoteSessionReader("agent-1")
            result = await reader.async_get_session_list()

        assert len(result) == 1
        assert result[0]["id"] == "s1"

    @pytest.mark.asyncio
    async def test_remote_async_get_current_session_id(self):
        """Async wrapper for current_session_id."""
        mock_inst = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"current_session_id": "cur-1"}
        mock_resp.raise_for_status = MagicMock()
        mock_inst.get.return_value = mock_resp
        mock_inst.__enter__.return_value = mock_inst

        with (
            patch.object(_mod.syscfg, "launcher_url", return_value="http://launcher:8080"),
            patch("httpx.Client", return_value=mock_inst),
        ):
            reader = _RemoteSessionReader("agent-1")
            assert await reader.async_get_current_session_id() == "cur-1"

    @pytest.mark.asyncio
    async def test_remote_async_delete_session(self):
        """Async wrapper for delete."""
        mock_inst = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()
        mock_inst.post.return_value = mock_resp
        mock_inst.__enter__.return_value = mock_inst

        with (
            patch.object(_mod.syscfg, "launcher_url", return_value="http://launcher:8080"),
            patch("httpx.Client", return_value=mock_inst),
        ):
            reader = _RemoteSessionReader("agent-1")
            assert await reader.async_delete_session("s1") is True


# =====================================================================
# _WsSessionReader — WebSocket tunnel reader
# =====================================================================


class TestWsSessionReader:
    """_WsSessionReader: WebSocket tunnel session reader tests."""

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _ok_rpc(result_dict):
        """Build an RPC function that returns result_dict."""

        async def rpc(node_id, method, path, body=None):
            return result_dict

        return rpc

    @staticmethod
    def _failing_rpc(exc=Exception("WS error")):
        """Build an RPC function that always raises."""

        async def rpc(node_id, method, path, body=None):
            raise exc

        return rpc

    # ── Tests ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_async_get_session_list(self):
        """Returns session list from WS RPC response."""
        reader = _WsSessionReader("agent-1", self._ok_rpc({"sessions": [{"id": "s1"}]}), "n1")
        assert len(await reader.async_get_session_list()) == 1

    @pytest.mark.asyncio
    async def test_async_get_current_session_id(self):
        """Returns current_session_id from WS RPC."""
        reader = _WsSessionReader("agent-1", self._ok_rpc({"current_session_id": "ws-cur"}), "n1")
        assert await reader.async_get_current_session_id() == "ws-cur"

    @pytest.mark.asyncio
    async def test_async_get_session_history(self):
        """Returns session data from WS RPC."""
        rpc = self._ok_rpc({"session": {"id": "s1", "title": "WS Session"}})
        reader = _WsSessionReader("agent-1", rpc, "n1")
        sess = await reader.async_get_session_history("s1")
        assert sess["id"] == "s1"
        assert sess["title"] == "WS Session"

    @pytest.mark.asyncio
    async def test_async_get_session_history_failed(self):
        """Returns None when WS RPC raises."""
        reader = _WsSessionReader("agent-1", self._failing_rpc(), "n1")
        assert await reader.async_get_session_history("s1") is None

    @pytest.mark.asyncio
    async def test_async_get_session_history_paged(self):
        """Returns paged data; verifies query string in path."""

        async def check_rpc(node_id, method, path, body=None):
            assert "offset=3" in path
            assert "limit=7" in path
            return {"session": {"id": "s1", "has_more": True}}

        reader = _WsSessionReader("agent-1", check_rpc, "n1")
        result = await reader.async_get_session_history_paged("s1", 3, 7)
        assert result["has_more"] is True

    @pytest.mark.asyncio
    async def test_async_get_session_history_paged_failed(self):
        """Returns None when WS RPC raises."""
        reader = _WsSessionReader("agent-1", self._failing_rpc(), "n1")
        assert await reader.async_get_session_history_paged("s1", 0, 10) is None

    @pytest.mark.asyncio
    async def test_async_delete_session(self):
        """Calls POST via WS RPC, returns ok."""

        async def del_rpc(node_id, method, path, body=None):
            assert method == "POST"
            assert path.endswith("/delete")
            return {"ok": True}

        reader = _WsSessionReader("agent-1", del_rpc, "n1")
        assert await reader.async_delete_session("s1") is True

    @pytest.mark.asyncio
    async def test_async_delete_session_failed(self):
        """Returns False when WS RPC raises."""
        reader = _WsSessionReader("agent-1", self._failing_rpc(), "n1")
        assert await reader.async_delete_session("s1") is False


# =====================================================================
# Factory functions — get_reader / async_get_reader
# =====================================================================


class TestFactoryFunctions:
    """Factory function tests: get_reader and async_get_reader."""

    # ── sync get_reader ───────────────────────────────────────────────

    def test_get_reader_local(self, tmp_path):
        """Returns AgentSessionReader for a known local agent."""
        agents_dir = tmp_path / "agents"
        _make_agent_dir(agents_dir, "test-agent", "test-agent")

        with (
            patch.object(_mod.syscfg, "workspace_agents_dir", return_value=str(agents_dir)),
            patch.object(_mod.syscfg, "get_workspace", return_value=str(tmp_path)),
        ):
            reader = get_reader("test-agent")

        assert reader is not None
        assert isinstance(reader, AgentSessionReader)

    def test_get_reader_cached(self, tmp_path):
        """Returns the same cached reader for repeated agent_id."""
        agents_dir = tmp_path / "agents"
        _make_agent_dir(agents_dir, "my-agent", "test-agent")

        with (
            patch.object(_mod.syscfg, "workspace_agents_dir", return_value=str(agents_dir)),
            patch.object(_mod.syscfg, "get_workspace", return_value=str(tmp_path)),
        ):
            r1 = get_reader("test-agent")
            r2 = get_reader("test-agent")

        assert r1 is r2

    def test_get_reader_no_agent(self):
        """Returns None for unknown agent without launcher_url."""
        with (
            patch.object(_mod.syscfg, "workspace_agents_dir", return_value="/nonexistent/agents"),
            patch.object(_mod.syscfg, "launcher_url", return_value=""),
        ):
            assert get_reader("unknown") is None

    def test_get_reader_remote_fallback(self):
        """Returns _RemoteSessionReader when local not found but launcher_url set."""
        with (
            patch.object(_mod.syscfg, "workspace_agents_dir", return_value="/nonexistent/agents"),
            patch.object(_mod.syscfg, "launcher_url", return_value="http://remote:8080"),
        ):
            reader = get_reader("remote-agent")

        assert isinstance(reader, _RemoteSessionReader)

    # ── async async_get_reader ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_async_get_reader_local(self, tmp_path):
        """Returns AgentSessionReader for a known local agent."""
        agents_dir = tmp_path / "agents"
        _make_agent_dir(agents_dir, "test-agent", "test-agent")

        with (
            patch.object(_mod.syscfg, "workspace_agents_dir", return_value=str(agents_dir)),
            patch.object(_mod.syscfg, "get_workspace", return_value=str(tmp_path)),
        ):
            reader = await async_get_reader("test-agent")

        assert isinstance(reader, AgentSessionReader)

    @pytest.mark.asyncio
    async def test_async_get_reader_cached(self, tmp_path):
        """Returns same cached reader for repeated agent_id."""
        agents_dir = tmp_path / "agents"
        _make_agent_dir(agents_dir, "my-agent", "test-agent")

        with (
            patch.object(_mod.syscfg, "workspace_agents_dir", return_value=str(agents_dir)),
            patch.object(_mod.syscfg, "get_workspace", return_value=str(tmp_path)),
        ):
            r1 = await async_get_reader("test-agent")
            r2 = await async_get_reader("test-agent")

        assert r1 is r2

    @pytest.mark.asyncio
    async def test_async_get_reader_no_agent(self):
        """Returns None for unknown agent without launcher_url."""
        with (
            patch.object(_mod.syscfg, "workspace_agents_dir", return_value="/nonexistent/agents"),
            patch.object(_mod.syscfg, "launcher_url", return_value=""),
        ):
            assert await async_get_reader("unknown") is None

    @pytest.mark.asyncio
    async def test_async_get_reader_ws_fallback(self):
        """Returns _WsSessionReader when WS handler is configured."""

        async def fake_rpc(*a, **kw):
            return {"ok": True}

        set_ws_handler(fake_rpc, lambda: "ws-node-1")

        with (
            patch.object(_mod.syscfg, "workspace_agents_dir", return_value="/nonexistent/agents"),
            patch.object(_mod.syscfg, "launcher_url", return_value="http://fallback:8080"),
        ):
            reader = await async_get_reader("ws-agent")

        assert isinstance(reader, _WsSessionReader)

    @pytest.mark.asyncio
    async def test_async_get_reader_ws_skipped_when_no_node(self):
        """Skips WS and falls through to HTTP when node_id_func returns None."""
        set_ws_handler(lambda *a, **kw: None, lambda: None)

        with (
            patch.object(_mod.syscfg, "workspace_agents_dir", return_value="/nonexistent/agents"),
            patch.object(_mod.syscfg, "launcher_url", return_value="http://fallback:8080"),
        ):
            reader = await async_get_reader("ws-agent")

        # Falls through to HTTP
        assert isinstance(reader, _RemoteSessionReader)

    @pytest.mark.asyncio
    async def test_async_get_reader_no_ws_no_http(self):
        """Returns None when no local, no WS handler, and no launcher_url."""
        # reset_globals already cleared WS handler, so no WS path is taken.
        # launcher_url returns "" => no HTTP path either.
        with (
            patch.object(_mod.syscfg, "workspace_agents_dir", return_value="/nonexistent/agents"),
            patch.object(_mod.syscfg, "launcher_url", return_value=""),
        ):
            reader = await async_get_reader("ghost")

        assert reader is None

    # ── refresh_agent_id_map / invalidate_reader ──────────────────────

    def test_refresh_agent_id_map_clears_cache(self):
        """refresh_agent_id_map re-scans and replaces cached map."""
        # Set a non-None map, so we can verify it gets replaced
        _mod._agent_id_map = {"cached": "/tmp/cached"}
        with patch.object(_mod.syscfg, "workspace_agents_dir", return_value="/nonexistent/agents"):
            refresh_agent_id_map()
        # With a non-existent agents dir the new map is empty
        assert _mod._agent_id_map == {}

    def test_invalidate_reader_calls_invalidate_on_reader(self):
        """invalidate_reader delegates to the reader's invalidate method."""
        mock_reader = MagicMock()
        _mod._readers["test-agent"] = mock_reader
        invalidate_reader("test-agent")
        mock_reader.invalidate_current_session_cache.assert_called_once()

    def test_invalidate_reader_unknown_agent(self):
        """invalidate_reader silently does nothing for unknown agent."""
        invalidate_reader("no-such-agent")  # should not raise
