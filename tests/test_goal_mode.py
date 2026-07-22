"""Unit tests for Codex-style /goal mode."""

from __future__ import annotations

import opensquad.goal_mode as gm


def setup_function(_fn=None):
    gm.clear_goal_memory()
    gm.arm_continuation()


def test_set_pause_resume_clear():
    r = gm.set_goal("Make all unit tests pass")
    assert r["ok"]
    assert r["goal"]["status"] == gm.STATUS_PURSUING
    assert gm.is_pursuing()

    assert gm.pause_goal()["ok"]
    assert gm.get_goal()["status"] == gm.STATUS_PAUSED
    assert not gm.is_pursuing()

    assert gm.resume_goal()["ok"]
    assert gm.is_pursuing()

    assert gm.clear_goal()["ok"]
    assert gm.get_goal() is None


def test_mark_achieved_requires_evidence_and_pursuing():
    gm.set_goal("Fix flaky test")
    bad = gm.mark_achieved("")
    assert not bad["ok"]

    ok = gm.mark_achieved("pytest returned 0")
    assert ok["ok"]
    assert ok["goal"]["status"] == gm.STATUS_ACHIEVED
    assert not gm.is_pursuing()

    # cannot mark again
    assert not gm.mark_achieved("again")["ok"]


def test_prompt_section_and_continuation_gate():
    assert gm.goal_prompt_section() == ""
    assert gm.take_continuation() is None

    gm.set_goal("Ship feature X")
    section = gm.goal_prompt_section()
    assert "Active Goal" in section
    assert "Ship feature X" in section
    assert gm.STATUS_PURSUING in section

    msg = gm.take_continuation()
    assert msg and "Goal continuation" in msg
    # only once per arm
    assert gm.take_continuation() is None

    gm.arm_continuation()
    assert gm.take_continuation() is not None

    gm.pause_goal()
    gm.arm_continuation()
    assert gm.take_continuation() is None


def test_expand_user_goal_sets_state():
    text = "<user_goal>Reduce latency 20%</user_goal>\nplease"
    expanded = gm.expand_user_goal(text)
    assert "Reduce latency 20%" in expanded
    assert "goal-execute-verify" in expanded.lower() or "Goal" in expanded
    g = gm.get_goal()
    assert g and g["objective"] == "Reduce latency 20%"
    assert g["status"] == gm.STATUS_PURSUING


def test_parse_slash_goal_line():
    assert gm.parse_slash_goal_line("/goal") == {"action": "status"}
    assert gm.parse_slash_goal_line("/goal pause") == {"action": "pause"}
    assert gm.parse_slash_goal_line("/goal Fix CI") == {
        "action": "set",
        "objective": "Fix CI",
    }
    assert gm.parse_slash_goal_line("hello") is None


def test_update_progress_and_blocked():
    gm.set_goal("Migrate deps")
    assert gm.update_progress("updated lockfile")["ok"]
    assert "lockfile" in (gm.get_goal() or {}).get("last_progress", "")
    assert gm.report_blocked("need credentials")["ok"]
    assert "credentials" in (gm.get_goal() or {}).get("blocked_reason", "")
