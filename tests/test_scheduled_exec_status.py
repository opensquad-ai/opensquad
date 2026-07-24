"""Scheduled execution status reconciliation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opensquad import scheduled_tasks as st


class TestScheduledExecStatus(unittest.TestCase):
    def setUp(self) -> None:
        st._managers.clear()
        st._seen_busy_exec_sessions.clear()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        st._managers.clear()
        st._seen_busy_exec_sessions.clear()
        self._tmpdir.cleanup()

    def _mgr(self) -> st.ScheduledTaskManager:
        path = self.data_dir / "tasks.json"
        path.write_text(json.dumps({"tasks": {}, "executions": []}), encoding="utf-8")
        return st.ScheduledTaskManager("agent1", str(self.data_dir))

    def test_reconcile_does_not_auto_complete_on_busy_leave(self) -> None:
        """busy_sessions flicker must not flip running → success mid-turn."""
        mgr = self._mgr()
        st._managers["agent1"] = mgr
        with mgr._lock:
            mgr._executions.append(
                {
                    "id": "exec1",
                    "task_id": "t1",
                    "status": "running",
                    "session_id": "sess_a",
                    "started_at": 1.0,
                }
            )
        st._seen_busy_exec_sessions.add("sess_a")
        done = st.reconcile_executions_for_busy_sessions("agent1", [])
        self.assertEqual(done, 0)
        rec = mgr.get_execution("exec1")
        assert rec is not None
        self.assertEqual(rec["status"], "running")

    def test_reconcile_records_busy_observations(self) -> None:
        mgr = self._mgr()
        st._managers["agent1"] = mgr
        with mgr._lock:
            mgr._executions.append(
                {
                    "id": "exec2",
                    "task_id": "t1",
                    "status": "running",
                    "session_id": "sess_b",
                    "started_at": 1.0,
                }
            )
        done = st.reconcile_executions_for_busy_sessions("agent1", ["sess_b"])
        self.assertEqual(done, 0)
        self.assertIn("sess_b", st._seen_busy_exec_sessions)
        rec = mgr.get_execution("exec2")
        assert rec is not None
        self.assertEqual(rec["status"], "running")

    def test_mark_execution_done_by_exec_id(self) -> None:
        mgr = self._mgr()
        st._managers["agent1"] = mgr
        with mgr._lock:
            mgr._executions.append(
                {
                    "id": "exec3",
                    "task_id": "t1",
                    "status": "running",
                    "session_id": "sess_c",
                    "started_at": 1.0,
                }
            )
        with mock.patch.object(mgr, "_notify_execution_changed"):
            rec = st.mark_execution_done_by_exec_id("exec3", status="stopped")
        assert rec is not None
        self.assertEqual(rec["status"], "stopped")

    def test_turn_done_marks_success(self) -> None:
        mgr = self._mgr()
        st._managers["agent1"] = mgr
        with mgr._lock:
            mgr._executions.append(
                {
                    "id": "exec4",
                    "task_id": "t1",
                    "status": "running",
                    "session_id": "sess_d",
                    "started_at": 1.0,
                }
            )
        with mock.patch.object(mgr, "_notify_execution_changed") as notify:
            rec = st.mark_execution_done_by_exec_id("exec4", status="success")
        assert rec is not None
        self.assertEqual(rec["status"], "success")
        notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
