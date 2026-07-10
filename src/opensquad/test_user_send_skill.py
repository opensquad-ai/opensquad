"""Tests for <user_send_skill> expansion."""

from opensquad.skill_loader import Skill, expand_user_send_skill, init_skill_runtime


def test_expand_user_send_skill_injects_content():
    skill = Skill(name="babysit", directory="/tmp/babysit")
    skill.display_name = "babysit"
    skill.description = "Keep a PR merge-ready"
    skill.content = "# Babysit PR\nDo the babysit workflow."
    init_skill_runtime([skill], registry=None)
    out = expand_user_send_skill("<user_send_skill>babysit</user_send_skill>\n\nfix the open PR")
    assert "BEGIN SKILL" in out
    assert "Do the babysit workflow." in out
    assert "fix the open PR" in out
    assert "<user_send_skill>" not in out


def test_expand_user_send_skill_missing_keeps_hint():
    init_skill_runtime([], registry=None)
    out = expand_user_send_skill("<user_send_skill>missing-skill</user_send_skill>\n\nhello")
    assert "missing-skill" in out
    assert "read_skill" in out
    assert "hello" in out
