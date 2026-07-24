"""Empty New Session shells are draft caches: hidden from list and reused."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opensquad.session_manager import SessionManager


class TestDraftSessionReuse(unittest.TestCase):
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

    def test_empty_current_hidden_from_list(self) -> None:
        sm = self._sm()
        sid = sm.get_current_session_id()
        self.assertTrue(sid)
        ids = [s["id"] for s in sm.get_session_list()]
        self.assertNotIn(sid, ids)

    def test_new_session_reuses_empty_draft(self) -> None:
        sm = self._sm()
        first = sm.get_current_session_id()
        created = sm.start_new_session()
        self.assertFalse(created)
        self.assertEqual(sm.get_current_session_id(), first)
        # Still no list entry
        self.assertEqual(sm.get_session_list(), [])

    def test_first_user_message_promotes_into_list(self) -> None:
        sm = self._sm()
        sid = sm.get_current_session_id()
        sm.add_message("user", "hello draft")
        # Drain async writer if any
        sm._drain_pending_mutations_sync()
        with sm._lock:
            sm._save_session()
        ids = [s["id"] for s in sm.get_session_list()]
        self.assertIn(sid, ids)
        self.assertFalse(sm.session_data.get("draft"))

    def test_new_session_after_message_archives_and_starts_draft(self) -> None:
        sm = self._sm()
        old = sm.get_current_session_id()
        sm.add_message("user", "keep me")
        sm._drain_pending_mutations_sync()
        with sm._lock:
            sm._save_session()
        created = sm.start_new_session()
        self.assertTrue(created)
        new = sm.get_current_session_id()
        self.assertNotEqual(old, new)
        ids = [s["id"] for s in sm.get_session_list()]
        self.assertIn(old, ids)
        self.assertNotIn(new, ids)
        hist = self.history_dir / f"{old}.json"
        self.assertTrue(hist.is_file())
        data = json.loads(hist.read_text(encoding="utf-8"))
        self.assertEqual(data.get("messages")[0].get("content"), "keep me")


if __name__ == "__main__":
    unittest.main()
