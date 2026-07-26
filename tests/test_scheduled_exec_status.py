"""Scheduled execution status reconciliation."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
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
        for m in list(st._managers.values()):
            m.stop()
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

    def test_load_persisted_fails_orphan_running(self) -> None:
        """Restart must not leave zombie running executions without a turn."""
        data_file = self.data_dir / "agent1_scheduled_tasks.json"
        data_file.write_text(
            json.dumps(
                {
                    "tasks": {
                        "t1": {
                            "id": "t1",
                            "name": "x",
                            "enabled": False,
                            "schedule": {"type": "once"},
                            "last_status": "running",
                        }
                    },
                    "executions": [
                        {
                            "id": "orphan1",
                            "task_id": "t1",
                            "status": "running",
                            "session_id": None,
                            "started_at": 1.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        mgr = st.ScheduledTaskManager("agent1", str(self.data_dir))
        mgr.start(None)
        rec = mgr.get_execution("orphan1")
        assert rec is not None
        self.assertEqual(rec["status"], "failed")
        self.assertIn("interrupted", rec.get("error") or "")
        self.assertEqual(mgr.get_task("t1")["last_status"], "failed")

    def test_spawn_watchdog_fails_without_session(self) -> None:
        mgr = self._mgr()
        st._managers["agent1"] = mgr
        with mgr._lock:
            mgr._executions.append(
                {
                    "id": "exec_wd",
                    "task_id": "t1",
                    "status": "running",
                    "session_id": None,
                    "started_at": time.time(),
                }
            )
            mgr._tasks["t1"] = {"id": "t1", "last_status": "running", "enabled": False}

        done = threading.Event()
        original_notify = mgr._notify_execution_changed

        def _notify(rec):
            original_notify(rec)
            if rec and rec.get("status") == "failed":
                done.set()

        with (
            mock.patch.object(st, "_SESSION_SPAWN_TIMEOUT_S", 0.15),
            mock.patch.object(st, "_MAX_REDELIVERIES", 0),
            mock.patch.object(st, "_REDELIVER_GAP_S", 0.0),
            mock.patch.object(mgr, "_notify_execution_changed", side_effect=_notify),
        ):
            mgr._arm_spawn_watchdog(
                "exec_wd",
                {"delegate_agent": "agent1", "content": "hi", "model_card": "", "attempts": 0},
            )
            self.assertTrue(done.wait(timeout=3.0), "watchdog did not fail execution")
        rec = mgr.get_execution("exec_wd")
        assert rec is not None
        self.assertEqual(rec["status"], "failed")
        self.assertIn("spawn session", (rec.get("error") or "").lower())

    def test_set_session_cancels_watchdog(self) -> None:
        mgr = self._mgr()
        st._managers["agent1"] = mgr
        with mgr._lock:
            mgr._executions.append(
                {
                    "id": "exec_ok",
                    "task_id": "t1",
                    "status": "running",
                    "session_id": None,
                    "started_at": time.time(),
                }
            )
        with (
            mock.patch.object(st, "_SESSION_SPAWN_TIMEOUT_S", 0.2),
            mock.patch.object(st, "_MAX_REDELIVERIES", 0),
            mock.patch.object(mgr, "_notify_execution_changed"),
        ):
            mgr._arm_spawn_watchdog(
                "exec_ok",
                {"delegate_agent": "agent1", "content": "hi", "model_card": "", "attempts": 0},
            )
            mgr.set_execution_session("exec_ok", "sess_bound")
            time.sleep(0.45)
        rec = mgr.get_execution("exec_ok")
        assert rec is not None
        self.assertEqual(rec["status"], "running")
        self.assertEqual(rec["session_id"], "sess_bound")


class TestAgentRegistryLiveness(unittest.IsolatedAsyncioTestCase):
    def _registry_mod(self):
        """Load registry the same way the gateway does (`app.ai_web.registry`)."""
        import sys
        from pathlib import Path

        backend = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
        backend_s = str(backend)
        if backend_s not in sys.path:
            sys.path.insert(0, backend_s)
        from app.ai_web.registry import AgentInfo, AgentRegistry

        return AgentInfo, AgentRegistry

    async def test_probe_resolves_on_pong(self) -> None:
        AgentInfo, AgentRegistry = self._registry_mod()
        reg = AgentRegistry()

        class _FakeWS:
            def __init__(self):
                self.sent = []

            async def send_text(self, text: str) -> None:
                self.sent.append(text)

        ws = _FakeWS()
        info = AgentInfo(
            agent_id="a1",
            agent_name="A",
            agent_type="general",
            capabilities=[],
            description="",
        )
        reg.register(info, ws)
        task = asyncio.create_task(reg.probe_agent("a1", timeout=2.0))
        await asyncio.sleep(0.05)
        self.assertTrue(ws.sent)
        reg.note_pong("a1")
        ok = await task
        self.assertTrue(ok)

    async def test_probe_timeout(self) -> None:
        AgentInfo, AgentRegistry = self._registry_mod()
        reg = AgentRegistry()

        class _FakeWS:
            async def send_text(self, text: str) -> None:
                return None

        info = AgentInfo(
            agent_id="a2",
            agent_name="A",
            agent_type="general",
            capabilities=[],
            description="",
        )
        reg.register(info, _FakeWS())
        ok = await reg.probe_agent("a2", timeout=0.1)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
