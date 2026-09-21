"""scheduled_tasks 工具集 — agent 自助管理定时任务的薄包装层。"""

import unittest
from unittest.mock import MagicMock, patch

from opensquad.tools import scheduled_tasks as st


def _fake_mgr():
    m = MagicMock()
    m.create_task.return_value = {
        "id": "abc123",
        "name": "每日晨报",
        "schedule": {"type": "daily", "time": "09:00"},
        "enabled": True,
        "next_run_ts": 1_700_000_000.0,
    }
    m.update_task.return_value = dict(m.create_task.return_value, name="改名")
    m.delete_task.return_value = True
    m.set_enabled.return_value = dict(m.create_task.return_value, enabled=False)
    m.list_tasks.return_value = [m.create_task.return_value]
    return m


class TestScheduledTasksTools(unittest.TestCase):
    def setUp(self):
        st.set_agent_id("agent-x")

    def test_create_task_builds_daily_schedule(self):
        mgr = _fake_mgr()
        with patch.object(st, "get_task_manager", return_value=mgr):
            out = st.create_task(name="每日晨报", prompt="总结昨日事项", schedule_type="daily", time="09:00")
        self.assertEqual(out["status"], "success")
        payload = mgr.create_task.call_args[0][0]
        self.assertEqual(payload["schedule"], {"type": "daily", "time": "09:00"})
        self.assertEqual(payload["skills"], [])
        self.assertTrue(payload["enabled"])

    def test_create_task_rejects_empty_prompt(self):
        mgr = _fake_mgr()
        with patch.object(st, "get_task_manager", return_value=mgr):
            out = st.create_task(name="x", prompt="  ")
        self.assertEqual(out["status"], "error")
        mgr.create_task.assert_not_called()

    def test_create_task_parses_skills_and_schedule_variants(self):
        mgr = _fake_mgr()
        with patch.object(st, "get_task_manager", return_value=mgr):
            st.create_task(
                name="周报", prompt="p", schedule_type="weekly", time="08:30", weekdays="0,2,4", skills="a, b"
            )
            payload = mgr.create_task.call_args[0][0]
            self.assertEqual(payload["schedule"], {"type": "weekly", "time": "08:30", "weekdays": "0,2,4"})
            self.assertEqual(payload["skills"], ["a", "b"])

            st.create_task(name="轮询", prompt="p", schedule_type="interval", interval_seconds=3600)
            self.assertEqual(mgr.create_task.call_args[0][0]["schedule"], {"type": "interval", "total_seconds": 3600})

            st.create_task(name="一次性", prompt="p", schedule_type="once", run_at_ts=123.0)
            self.assertEqual(mgr.create_task.call_args[0][0]["schedule"], {"type": "once", "run_at_ts": 123.0})

    def test_update_only_touches_given_fields(self):
        mgr = _fake_mgr()
        with patch.object(st, "get_task_manager", return_value=mgr):
            out = st.update_task("abc123", prompt="新 prompt")
        self.assertEqual(out["status"], "success")
        tid, payload = mgr.update_task.call_args[0]
        self.assertEqual(tid, "abc123")
        self.assertEqual(payload, {"prompt": "新 prompt"})

    def test_update_missing_task(self):
        mgr = _fake_mgr()
        mgr.update_task.return_value = None
        with patch.object(st, "get_task_manager", return_value=mgr):
            self.assertEqual(st.update_task("nope", name="x")["status"], "error")

    def test_delete_and_enable(self):
        mgr = _fake_mgr()
        with patch.object(st, "get_task_manager", return_value=mgr):
            self.assertEqual(st.delete_task("abc123")["status"], "success")
            mgr.delete_task.assert_called_once_with("abc123")

            out = st.set_task_enabled("abc123", enabled=False)
            self.assertEqual(out["status"], "success")
            mgr.set_enabled.assert_called_once_with("abc123", False)

    def test_run_now_handles_already_running(self):
        mgr = _fake_mgr()
        mgr.run_now.return_value = {"already_running": True}
        with patch.object(st, "get_task_manager", return_value=mgr):
            out = st.run_task_now("abc123")
        self.assertEqual(out["status"], "success")
        self.assertIn("跳过", out["message"])

    def test_list_tasks(self):
        mgr = _fake_mgr()
        with patch.object(st, "get_task_manager", return_value=mgr):
            out = st.list_tasks()
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["tasks"][0]["id"], "abc123")


if __name__ == "__main__":
    unittest.main()
