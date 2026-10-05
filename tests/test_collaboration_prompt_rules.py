"""The collaboration rules about who gets @-mentioned, and where talk happens.

Four rules the field asked for, and each one is pinned where an agent will actually read it — the
skill it loads with the card, and the numbered instructions the tools return on every call:

  * a worker @-mentions the user only when it is genuinely needed;
  * the PM holds the same rule and leans on the task window instead of the group;
  * the delivery is one or two sentences, then the acceptance approval request — no monologue;
  * everything that is not an @message stays inside the task window.

Those are instructions, so they are checked as text, in both places.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SKILL = _ROOT / "src" / "skills" / "collaboration_management" / "SKILL.md"
TOOLS = _ROOT / "src" / "opensquad" / "tools" / "collaboration.py"


def test_the_skill_states_all_four_rules():
    text = SKILL.read_text(encoding="utf-8")

    assert "Quiet by default" in text
    assert "a worker @-mentions the user only when it is genuinely needed" in text
    assert "The PM holds the same rule" in text
    assert "Talk inside the collaboration" in text
    assert "belongs in the task window" in text
    assert "Deliver in one or two sentences" in text
    assert "submit the acceptance approval request" in text


def test_the_skill_says_where_a_progress_update_goes():
    text = SKILL.read_text(encoding="utf-8")

    assert "Report in the task window, and keep it short" in text
    assert "@-mention the user only when it is genuinely needed" in text
    assert "The group chat carries the @mention that wakes someone" in text


def test_the_tool_instructions_carry_the_same_rules():
    text = TOOLS.read_text(encoding="utf-8")

    # assignment talk moved into the task window; the group keeps only the wake-up
    assert "Discuss the assignment with each worker IN THE TASK WINDOW" in text
    assert "Discuss assignment with team via @mention in group chat" not in text
    # @the user sparingly
    assert "@用户只在确有必要时" in text
    assert "不要把进度播报给用户" in text
    # delivery: one or two sentences, then the acceptance gate
    assert "1–2 句话汇报完成" in text
    assert "提交「任务验收」请用户通过" in text
    # and everything else stays in the task window
    assert "协作期间的一切沟通都在任务窗口" in text
    assert "不要发到群里直接暴露" in text
