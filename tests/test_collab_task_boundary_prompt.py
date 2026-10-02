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
ROLE_CARD = ROOT / "src" / "role_cards" / "product_manager.md"
COLLAB_CARD = ROOT / "src" / "collab_cards" / "software_dev_team.md"
ASSIGN_TOOL = ROOT / "src" / "opensquad" / "tools" / "collaboration.py"
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


def test_the_one_task_at_a_time_rule_is_stated():
    """An agent should know the rule before it hits the refusal."""
    text = PART.read_text(encoding="utf-8")
    assert "One collaboration at a time" in text
    assert "end_collaboration" in text

    skill = SKILL.read_text(encoding="utf-8")
    assert "One collaboration at a time" in skill


def test_the_window_is_the_teams_channel_and_the_user_gets_the_group():
    """Task talk is teammate-to-teammate; anything the user must see or decide goes to
    the group, where they actually read it."""
    text = PART.read_text(encoding="utf-8")
    assert "team's own channel" in text
    assert "Anything the **user** needs to see" in text
    # the rule of thumb leads the section instead of being buried in the bullets
    assert "Rule of thumb: everything about a collaboration task is communicated inside" in text
    assert "above all\nanything that needs their attention or decision" in text

    skill = SKILL.read_text(encoding="utf-8")
    assert "Anything the user must see or decide goes to the group" in skill
    assert "Rule: task-related communication stays in the task window" in skill


def test_the_end_of_a_task_is_announced_in_the_window():
    text = PART.read_text(encoding="utf-8")
    assert "The end of the task is announced in the window too" in text

    skill = SKILL.read_text(encoding="utf-8")
    assert "The end of the task is announced in the window" in skill


def test_the_team_must_have_accepted_before_assignment():
    text = PART.read_text(encoding="utf-8")
    assert "Everyone must be 已参与 before any work is handed out" in text
    assert "assign_task` refuses while any" in text

    skill = SKILL.read_text(encoding="utf-8")
    assert "Everyone must be 已参与 before work is handed out" in skill


def test_the_pm_is_told_to_split_by_boundary_not_by_volume():
    """The split decides whether the project decouples or collides: one owner per file /
    module / interface, and each piece verifiable on its own."""
    role = ROLE_CARD.read_text(encoding="utf-8")
    assert "by **boundary, not by volume**" in role
    assert "One unit, one owner" in role
    assert "exactly one owner" in role

    card = COLLAB_CARD.read_text(encoding="utf-8")
    assert "拆分原则" in card
    assert "一个单元只有一个负责人" in card
    assert "scope 重叠 = 拆分错了" in card

    skill = SKILL.read_text(encoding="utf-8")
    assert "Split by **boundary, not" in skill
    assert "never hand the same file to two agents" in skill

    # the rule is also where the assignment actually happens
    tool = ASSIGN_TOOL.read_text(encoding="utf-8")
    assert "Split by **boundary, not by volume**" in tool
    assert "one owner per file / module / API interface" in tool
