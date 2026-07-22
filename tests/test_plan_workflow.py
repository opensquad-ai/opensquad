"""Tests for Cursor-style /plan workflow helpers."""

from __future__ import annotations

from opensquad.plan_workflow import expand_user_plan, suggested_plan_path


def test_suggested_plan_path():
    p = suggested_plan_path("二级分屏 feature")
    assert p.startswith(".opensquad/plans/")
    assert p.endswith(".md")
    assert "opensquad" in p


def test_expand_user_plan():
    out = expand_user_plan("<user_plan>Add split panes</user_plan>")
    assert "Add split panes" in out
    assert ".opensquad/plans/" in out
    assert "request_switch" in out
    assert "<plan>" in out
    assert "Investigate" in out or "investigate" in out.lower()
