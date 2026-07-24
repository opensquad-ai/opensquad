"""Scheduled-task fire must inject skill tags like Agent Web skill selection."""

import unittest

from opensquad.scheduled_tasks import ScheduledTaskManager
from opensquad.skill_loader import Skill, expand_user_send_skill, init_skill_runtime


class TestScheduledTaskSkills(unittest.TestCase):
    def test_build_fire_content_without_skills(self):
        out = ScheduledTaskManager._build_fire_content("Daily report", "Summarize inbox")
        self.assertTrue(out.startswith("[Scheduled Task: Daily report]\n"))
        self.assertIn("task_watch.start", out)
        self.assertIn("Summarize inbox", out)
        self.assertNotIn("<user_send_skill>", out)

    def test_build_fire_content_with_skills_mirrors_agent_web(self):
        out = ScheduledTaskManager._build_fire_content(
            "Daily report",
            "Summarize inbox",
            skills=["babysit", "websearch"],
        )
        self.assertTrue(out.startswith("<user_send_skill>babysit</user_send_skill>"))
        self.assertIn("<user_send_skill>websearch</user_send_skill>", out)
        self.assertIn("[Scheduled Task: Daily report]", out)
        self.assertIn("task_watch.start", out)
        self.assertIn("Summarize inbox", out)

    def test_expand_multiple_user_send_skills(self):
        a = Skill(name="babysit", directory="/tmp/babysit")
        a.display_name = "babysit"
        a.content = "# Babysit\nKeep PR green."
        b = Skill(name="websearch", directory="/tmp/websearch")
        b.display_name = "Web Search"
        b.content = "# Web Search\nSearch then cite."
        init_skill_runtime([a, b], registry=None)

        raw = ScheduledTaskManager._build_fire_content(
            "Ops",
            "Do the morning checklist",
            skills=["babysit", "websearch"],
        )
        out = expand_user_send_skill(raw)
        self.assertIn("Keep PR green.", out)
        self.assertIn("Search then cite.", out)
        self.assertIn("Do the morning checklist", out)
        self.assertNotIn("<user_send_skill>", out)
        self.assertIn("BEGIN SKILL", out)


if __name__ == "__main__":
    unittest.main()
