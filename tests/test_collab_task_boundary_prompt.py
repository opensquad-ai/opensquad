"""The task-window/group boundary has to reach the prompts (and the skill) agents read.

An agent that does not know the boundary posts task work into the group and group
chatter into the task's thread — both are visible in the UI, and both destroy a record
the other participants rely on. So the rule is asserted here, not left to review.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "src" / "prompts"
PART = PROMPTS / "parts" / "common_2.12_file_transfer_distribution.md"
REPLIES = PROMPTS / "parts" / "common_2.5_user_replies_system_tools.md"
SKILL = ROOT / "src" / "skills" / "collaboration-workflow" / "SKILL.md"
TEMPLATES = ("base_fc.md", "base_xml.md", "thought_fc.md", "thought_xml.md")
PART_REL = "parts/common_2.12_file_transfer_distribution.md"


def test_the_rule_names_both_destinations_and_the_switch_between_them():
    text = PART.read_text(encoding="utf-8")

    assert "task window" in text
    assert 'collab_id="<collab_id>"' in text  # the task's thread
    assert 'target_type="group"' in text  # ordinary group chat
    # the consequence of choosing wrongly, so the rule is actionable
    assert "not touched" in text


def test_every_prompt_template_ships_the_rule():
    for name in TEMPLATES:
        entry = (PROMPTS / name).read_text(encoding="utf-8")
        assert PART_REL in entry, f"{name} no longer includes {PART_REL}"


def test_the_reply_guidance_points_at_the_task_window():
    text = REPLIES.read_text(encoding="utf-8")

    assert "collab_id" in text
    assert "2.12b" in text


def test_the_collaboration_skill_says_where_to_talk():
    text = SKILL.read_text(encoding="utf-8")

    assert "Where to Talk" in text
    assert 'collab_id="<collab_id>"' in text
    assert 'target_type="group"' in text
