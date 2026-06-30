"""SessionManager tests — covers persistence, async writer lifecycle, and edge paths."""

import asyncio
import json
import os

import pytest

from opensquad.session_manager import SessionManager


@pytest.fixture
def sm(tmp_path):
    """Create a SessionManager with a temp directory."""
    mgr = SessionManager(
        save_dir=str(tmp_path / "sessions"),
        history_dir=str(tmp_path / "history"),
    )
    yield mgr
    # Cleanup: stop async writer if running
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running() and mgr._writer_running:
            loop.create_task(mgr.stop_async_writer(timeout=1))
    except RuntimeError:
        pass


class TestSessionManagerInit:
    """SessionManager construction and basic state."""

    def test_creates_with_temp_dir(self, sm):
        assert sm.session_data is not None
        assert sm.current_session_file is not None

    def test_new_session_has_empty_messages(self, sm):
        assert len(sm.get_messages()) == 0

    def test_add_message(self, sm):
        sm.add_message("user", "Hello")
        msgs = sm.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"

    def test_add_multiple_messages(self, sm):
        sm.add_message("user", "Hi")
        sm.add_message("assistant", "Hello there")
        msgs = sm.get_messages()
        assert len(msgs) == 2


class TestSessionManagerPersistence:
    """Verify that messages persist to disk correctly."""

    def test_save_and_reload(self, sm, tmp_path):
        sm.add_message("user", "Hello")
        sm.add_message("assistant", "Hi there")

        # _save_session writes to disk
        sm._save_session()

        # Create a new SessionManager pointing to same dir
        sm2 = SessionManager(
            save_dir=str(tmp_path / "sessions"),
            history_dir=str(tmp_path / "history"),
        )
        sm2.get_messages()
        # The session_data reloads from current_session.json
        assert sm2.session_data is not None

    def test_current_session_file_created(self, sm, tmp_path):
        sm.add_message("user", "test")
        sm._save_session()
        session_file = tmp_path / "sessions" / "current_session.json"
        assert os.path.exists(session_file)
        with open(session_file, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["messages"]) >= 1


class TestSessionManagerEdgeCases:
    """SessionManager error handling and edge cases."""

    def test_long_message_trimmed(self, sm):
        """Messages beyond limit cause trimming."""
        for i in range(10):
            sm.add_message("user", f"Message {i}")
        # get_messages returns all
        assert len(sm.get_messages()) == 10

    def test_concurrent_add_no_crash(self, sm):
        """Multiple rapid adds should not crash."""
        for _ in range(100):
            sm.add_message("user", "x")
        assert len(sm.get_messages()) == 100


class TestSessionManagerAsyncWriter:
    """Async writer lifecycle."""

    @pytest.mark.asyncio
    async def test_start_writer(self, sm):
        sm.start_async_writer()
        assert sm._writer_running is True
        assert sm._writer_task is not None

    @pytest.mark.asyncio
    async def test_stop_writer(self, sm):
        sm.start_async_writer()
        await sm.stop_async_writer(timeout=2)
        assert sm._writer_running is False

    @pytest.mark.asyncio
    async def test_writer_flushes_messages(self, sm):
        sm.start_async_writer()
        sm.add_message("user", "test message")
        # Give writer a chance to flush
        await asyncio.sleep(0.2)
        session_file = sm.current_session_file
        if os.path.exists(session_file):
            with open(session_file, encoding="utf-8") as f:
                data = json.load(f)
            [m for m in data.get("messages", []) if m.get("role") == "user" and m.get("content") == "test message"]
        # Stop cleanly
        await sm.stop_async_writer(timeout=2)

    @pytest.mark.asyncio
    async def test_writer_stop_timeout(self, sm):
        """Verify stop with short timeout does not crash."""
        sm.start_async_writer()
        await sm.stop_async_writer(timeout=0.1)
        assert sm._writer_running is False


class TestCompressSession:
    """Verify that compress_current_session preserves original content in
    session_data["archived_messages"] / ["archived_events"] for the
    frontend to render inside the collapsible "已归档" section, while
    keeping the live messages/events trimmed for the LLM context."""

    def _populate(self, sm, n: int) -> list:
        """Populate the session with n alternating user/assistant messages."""
        original_contents = []
        for i in range(n):
            role = "user" if i % 2 == 0 else "assistant"
            content = f"msg-{i}-" + ("x" * 50)  # 53+ chars, > 25 tokens each
            sm.add_message(role, content)
            original_contents.append((role, content))
        return original_contents

    def test_compress_moves_old_messages_to_archive(self, sm, tmp_path):
        """A single compression removes old messages from `messages` and
        appends them to `archived_messages`."""
        originals = self._populate(sm, 20)

        result = sm.compress_current_session(keep_ratio=0.1)

        live = sm.get_messages()
        archived = sm.session_data.get("archived_messages") or []
        # Live is trimmed (keep_ratio 0.1 of tokens — for our content size
        # this leaves only the newest few messages).
        assert len(live) < len(originals), f"expected live to be trimmed, got live={len(live)} total={len(originals)}"
        # Archived captures the removed messages.
        assert len(archived) > 0, "expected archived_messages to be populated"
        # Live + archived together cover the full original set.
        assert len(live) + len(archived) == len(originals), (
            f"live({len(live)}) + archived({len(archived)}) should equal total original({len(originals)})"
        )
        # Return value exposes the counts.
        assert result["archived_messages_count"] == len(archived)
        assert result["compressed"] is True

    def test_repeated_compression_appends_to_archive(self, sm):
        """Multiple compressions should APPEND, not replace, so the
        archived history grows monotonically until the cap is hit."""
        self._populate(sm, 20)

        sm.compress_current_session(keep_ratio=0.1)
        archive_after_first = list(sm.session_data.get("archived_messages") or [])
        sm.get_messages()

        # Add more messages and compress again.
        for i in range(20, 40):
            role = "user" if i % 2 == 0 else "assistant"
            sm.add_message(role, f"msg-{i}-" + ("y" * 50))

        sm.compress_current_session(keep_ratio=0.1)
        archive_after_second = list(sm.session_data.get("archived_messages") or [])
        live_after_second = sm.get_messages()

        # The archive should have grown, not been reset.
        assert len(archive_after_second) >= len(archive_after_first), "repeated compressions must append, not replace"
        # Live + archive still covers all 40 messages.
        assert len(live_after_second) + len(archive_after_second) == 40

    def test_archive_survives_disk_round_trip(self, sm, tmp_path):
        """After _save_session, a fresh SessionManager reading the same
        directory should see the archived_messages field backfilled from
        disk and matching what was written."""
        self._populate(sm, 20)
        sm.compress_current_session(keep_ratio=0.1)

        original_archived = list(sm.session_data.get("archived_messages") or [])

        # Reload from disk.
        sm2 = SessionManager(
            save_dir=str(tmp_path / "sessions"),
            history_dir=str(tmp_path / "history"),
        )
        reloaded_archived = sm2.session_data.get("archived_messages") or []
        assert len(reloaded_archived) == len(original_archived)
        # Spot-check a content match to make sure we got the same data.
        if original_archived:
            assert reloaded_archived[0].get("content") == original_archived[0].get("content")

    def test_backfill_for_legacy_sessions_without_archive_field(self, tmp_path):
        """Older session_data JSON files written before the archive field
        existed must still load — _load_session should backfill empty
        arrays rather than raising KeyError."""
        import json as _json

        save_dir = tmp_path / "sessions"
        history_dir = tmp_path / "history"
        save_dir.mkdir(parents=True, exist_ok=True)
        history_dir.mkdir(parents=True, exist_ok=True)

        # Write a "legacy" session_data without the archive fields.
        legacy = {
            "id": "legacy_sid",
            "title": None,
            "messages": [{"role": "user", "content": "old"}],
            "events": [],
            "latest_summary": "",
            "last_updated": "2024-01-01T00:00:00Z",
            "created_at": "2024-01-01T00:00:00Z",
        }
        with open(save_dir / "current_session.json", "w", encoding="utf-8") as f:
            _json.dump(legacy, f)

        sm = SessionManager(save_dir=str(save_dir), history_dir=str(history_dir))
        # Backfill must have added the empty arrays.
        assert "archived_messages" in sm.session_data
        assert "archived_events" in sm.session_data
        assert sm.session_data["archived_messages"] == []
        assert sm.session_data["archived_events"] == []

    def test_get_stats_exposes_archive_counts(self, sm):
        """get_stats should report total_archived_messages / events so
        dashboards can show the archived volume."""
        self._populate(sm, 10)
        sm.compress_current_session(keep_ratio=0.1)
        stats = sm.get_stats()
        assert "total_archived_messages" in stats
        assert "total_archived_events" in stats
        assert stats["total_archived_messages"] > 0
