"""Gateway adoption of scheduled tasks created by the loop-less Agent process.

Regression: an Agent-side ``create_task`` persisted the file but also armed a
timer in a process with no event loop. That timer always died with
"delegate agent not connected", and because gateway warm-up only ran once at
startup, a task created afterwards was never adopted — its 07:30 fire failed
every day.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from opensquad.scheduled_tasks import ScheduledTaskManager


class _FakeLoop:
    """Minimal stand-in: only ``is_running`` is consulted by _can_schedule."""

    def __init__(self, running: bool = True) -> None:
        self._running = running

    def is_running(self) -> bool:
        return self._running


def _write_store(path: str, agent_id: str, task_id: str, *, task_name: str = "福州每日天气推送") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    task = {
        "id": task_id,
        "name": task_name,
        "prompt": "推送福州天气",
        "workspace": "",
        "delegate_agent": agent_id,
        "model_card": "",
        "skills": [],
        "schedule": {"type": "daily", "time": "07:30"},
        "enabled": True,
        "created_at": time.time(),
        "updated_at": time.time(),
        "last_run_ts": None,
        "last_status": None,
        "next_run_ts": None,
        "run_count": 0,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"tasks": {task_id: task}, "executions": []}, f, ensure_ascii=False)


class ScheduledTaskAdoptionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = self._tmp.name
        self.agent_id = "agent305-001"
        self.store = os.path.join(self.data_dir, f"{self.agent_id}_scheduled_tasks.json")
        _write_store(self.store, self.agent_id, "t1")
        self.managers: list[ScheduledTaskManager] = []

    def tearDown(self) -> None:
        for m in self.managers:
            m.stop()
        self._tmp.cleanup()

    def _manager(self) -> ScheduledTaskManager:
        m = ScheduledTaskManager(self.agent_id, self.data_dir)
        self.managers.append(m)
        return m

    def test_data_only_manager_arms_nothing(self):
        """No loop means no delivery, so no timer — the original bug."""
        m = self._manager()
        m.start(None)
        self.assertEqual(m._timers, {})
        self.assertFalse(m._can_schedule())
        self.assertEqual(len(m._tasks), 1, "tasks must still load for CRUD")

    def test_ensure_scheduled_arms_after_loop_arrives(self):
        m = self._manager()
        m.start(None)
        self.assertIn("t1", m._tasks)

        m._loop = _FakeLoop()  # gateway takes over
        self.assertEqual(m.ensure_scheduled(), 1)
        self.assertIn("t1", m._timers)

        # Idempotent: a second tick must not double-arm.
        self.assertEqual(m.ensure_scheduled(), 0)

    def test_run_now_refuses_without_loop_and_records_nothing(self):
        m = self._manager()
        m.start(None)
        before = len(m._executions)

        res = m.run_now("t1")

        self.assertEqual(res, {"not_deliverable": True})
        self.assertEqual(len(m._executions), before, "must not burn an execution row")

    def test_reload_if_changed_picks_up_an_external_write(self):
        m = self._manager()
        m.start(None)
        m._loop = _FakeLoop()
        m.ensure_scheduled()
        self.assertIn("t1", m._tasks)

        # The Agent process adds a second task and rewrites the file.
        _write_store(self.store, self.agent_id, "t2", task_name="第二个任务")
        future = time.time() + 60
        os.utime(self.store, (future, future))

        self.assertTrue(m.reload_if_changed())
        self.assertIn("t2", m._tasks)
        self.assertNotIn("t1", m._tasks, "reload replaces the whole store")
        # The old timer must be gone, and the new task armed.
        self.assertNotIn("t1", m._timers)
        self.assertIn("t2", m._timers)

    def test_own_save_does_not_look_like_a_foreign_edit(self):
        """Otherwise the reconcile tick would reload (and re-arm) forever."""
        m = self._manager()
        m.start(None)
        m._save_persisted()
        self.assertFalse(m.reload_if_changed(), "our own write must not re-trigger a reload")

    def test_reconcile_loop_is_idempotent_on_repeat_ticks(self):
        """A second tick with no file change must be a no-op (no reload storm)."""
        m = self._manager()
        m.start(None)
        m._loop = _FakeLoop()
        m.ensure_scheduled()

        future = time.time() + 60
        os.utime(self.store, (future, future))
        self.assertTrue(m.reload_if_changed())
        self.assertFalse(m.reload_if_changed(), "unchanged file must not reload twice")

    def test_a_gateway_save_keeps_tasks_added_by_the_agent(self):
        """Two processes share one JSON; a save must not erase a fresh task."""
        gateway = self._manager()
        gateway.start(None)
        self.assertIn("t1", gateway._tasks)

        # Meanwhile the Agent adds t2 and persists its own view.
        _write_store(self.store, self.agent_id, "t2", task_name="第二个任务")
        future = time.time() + 60
        os.utime(self.store, (future, future))

        # Gateway saves before its next reconcile tick.
        gateway._save_persisted()

        with open(self.store, encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertIn("t2", on_disk["tasks"], "the Agent's task must survive the save")
        self.assertIn("t1", gateway._tasks, "the gateway's own task must survive too")

    def test_reconcile_adopts_a_file_created_after_warm_up(self):
        """The actual fix: a task written later is picked up by the gateway."""
        from unittest.mock import patch

        from opensquad import scheduled_tasks as st

        # Start from a clean singleton table, as it is right after boot.
        st._managers.pop(self.agent_id, None)
        try:
            with patch("opensquad.system_config.syscfg") as fake_cfg:
                fake_cfg.workspace_data_dir.return_value = self.data_dir
                n = st.reconcile_task_managers(_FakeLoop())

            self.assertGreaterEqual(n, 1)
            adopted = st._managers[self.agent_id]
            self.managers.append(adopted)
            self.assertIn("t1", adopted._tasks)
            self.assertIn("t1", adopted._timers, "the gateway must arm what it adopts")
            self.assertTrue(adopted._can_schedule())
        finally:
            st._managers.pop(self.agent_id, None)


if __name__ == "__main__":
    unittest.main()
