"""Scheduled-task sessions are hidden from interactive session lists."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opensquad import scheduled_tasks as st
from opensquad.session_manager import SessionManager


class TestScheduledSessionOrigin(unittest.TestCase):
    def setUp(self) -> None:
        st._managers.clear()
        st._seen_busy_exec_sessions.clear()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.save_dir = self.root / "sessions"
        self.history_dir = self.root / "history"
        self.save_dir.mkdir()
        self.history_dir.mkdir()

    def tearDown(self) -> None:
        st._managers.clear()
        st._seen_busy_exec_sessions.clear()
        self._tmpdir.cleanup()

    def _sm(self) -> SessionManager:
        return SessionManager(str(self.save_dir), str(self.history_dir))

    def test_create_parallel_session_writes_origin(self) -> None:
        sm = self._sm()
        sid = sm.create_parallel_session(title="Daily report", origin="scheduled_task")
        path = self.history_dir / f"{sid}.json"
        self.assertTrue(path.is_file())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("origin"), "scheduled_task")
        self.assertEqual(data.get("title"), "Daily report")

    def test_get_session_list_hides_scheduled_origin(self) -> None:
        sm = self._sm()
        hidden = sm.create_parallel_session(title="Hidden", origin="scheduled_task")
        visible = sm.create_parallel_session(title="Visible", origin=None)
        # Also write a normal-looking focused session so list is non-empty.
        sm.session_data = {
            "id": "focused1",
            "title": "Focused",
            "messages": [{"role": "user", "content": "hi"}],
            "events": [],
            "created_at": "2026-01-01T00:00:00Z",
            "last_updated": "2026-01-01T00:00:00Z",
        }
        ids = {s["id"] for s in sm.get_session_list()}
        self.assertIn("focused1", ids)
        self.assertIn(visible, ids)
        self.assertNotIn(hidden, ids)

    def test_scheduled_execution_session_ids_fallback(self) -> None:
        mgr = st.ScheduledTaskManager("agent1", str(self.root / "sched"))
        st._managers["agent1"] = mgr
        with mgr._lock:
            mgr._executions.append(
                {
                    "id": "e1",
                    "task_id": "t1",
                    "status": "running",
                    "session_id": "legacy_sid",
                }
            )
        self.assertIn("legacy_sid", st.scheduled_execution_session_ids())

    def test_mark_execution_done_is_idempotent(self) -> None:
        mgr = st.ScheduledTaskManager("agent1", str(self.root / "sched"))
        with mgr._lock:
            mgr._executions.append(
                {
                    "id": "e2",
                    "task_id": "t1",
                    "status": "running",
                    "session_id": "s2",
                    "started_at": 1.0,
                }
            )
        with patch.object(mgr, "_notify_execution_changed"):
            first = mgr.mark_execution_done("e2", status="success")
            second = mgr.mark_execution_done("e2", status="failed")
        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")

    def test_set_execution_session_notifies_once(self) -> None:
        mgr = st.ScheduledTaskManager("agent1", str(self.root / "sched"))
        with mgr._lock:
            mgr._executions.append(
                {
                    "id": "e3",
                    "task_id": "t1",
                    "status": "running",
                    "session_id": None,
                    "started_at": 1.0,
                }
            )
        with patch.object(mgr, "_notify_execution_changed") as notify:
            self.assertTrue(mgr.set_execution_session("e3", "sess_x"))
            self.assertTrue(mgr.set_execution_session("e3", "sess_y"))
        self.assertEqual(notify.call_count, 1)
        rec = mgr.get_execution("e3")
        assert rec is not None
        self.assertEqual(rec["session_id"], "sess_x")


if __name__ == "__main__":
    unittest.main()
