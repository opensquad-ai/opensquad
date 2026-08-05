"""abandon_current_draft() mints a fresh sid so the sidebar delete flow works.

Mirrors the user-reported regression: clicking delete on an empty draft
session used to fail with "无法放弃当前会话，删除失败" because
start_new_session reuses the empty draft (sid never changes) and the
frontend's waitForRotation poll would time out. abandon_current_draft
forces a fresh sid and (for non-empty currents) leaves a deletable
history snapshot behind.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opensquad.session_manager import SessionManager


class TestAbandonCurrentDraft(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.save_dir = self.root / "sessions"
        self.history_dir = self.root / "history"
        self.save_dir.mkdir()
        self.history_dir.mkdir()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _sm(self) -> SessionManager:
        return SessionManager(str(self.save_dir), str(self.history_dir))

    def test_abandon_empty_draft_mints_new_sid_and_drops_old(self) -> None:
        sm = self._sm()
        old = sm.get_current_session_id()
        self.assertTrue(old)

        old_sid, new_sid = sm.abandon_current_draft()

        # Fresh sid — frontend rotation poll will observe the change.
        self.assertEqual(old_sid, old)
        self.assertNotEqual(new_sid, old)
        self.assertEqual(sm.get_current_session_id(), new_sid)

        # No history leak: empty drafts are NOT archived to history/.
        self.assertFalse((self.history_dir / f"{old}.json").is_file())
        self.assertFalse((self.history_dir / f"{old}.json.log").is_file())

        # New draft is hidden from the sidebar list (regression — same as
        # any fresh empty draft).
        self.assertEqual(sm.get_session_list(), [])

    def test_abandon_non_empty_archives_old_and_starts_fresh_draft(self) -> None:
        sm = self._sm()
        old = sm.get_current_session_id()
        sm.add_message("user", "preserve me")
        sm._drain_pending_mutations_sync()
        with sm._lock:
            sm._save_session()

        old_sid, new_sid = sm.abandon_current_draft()

        self.assertEqual(old_sid, old)
        self.assertNotEqual(new_sid, old)
        # Old is now a history file (deletable from the sidebar).
        hist = self.history_dir / f"{old}.json"
        self.assertTrue(hist.is_file())
        data = json.loads(hist.read_text(encoding="utf-8"))
        self.assertEqual(data["messages"][0]["content"], "preserve me")
        # New current is an empty draft — hidden from the sidebar list.
        ids = [s["id"] for s in sm.get_session_list()]
        self.assertNotIn(new_sid, ids)
        self.assertIn(old, ids)

    def test_repeated_abandon_each_mints_new_sid(self) -> None:
        sm = self._sm()
        seen: set[str] = set()
        for _ in range(3):
            old_sid, new_sid = sm.abandon_current_draft()
            self.assertNotEqual(old_sid, new_sid)
            self.assertNotIn(new_sid, seen)
            seen.add(new_sid)


if __name__ == "__main__":
    unittest.main()
