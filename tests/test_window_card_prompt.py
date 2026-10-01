"""The window-card guidance has to reach the prompts agents actually receive."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "src" / "prompts"
PART_REL = "parts/common_2.26_agent_web_interactive_html.md"
TEMPLATES = ("base_fc.md", "base_xml.md", "thought_fc.md", "thought_xml.md")


def test_guidance_covers_the_tool_both_surfaces_and_the_view_kinds():
    text = (PROMPTS / PART_REL).read_text(encoding="utf-8")
    assert "window_card.send_window_card" in text
    # both delivery targets must stay documented
    assert "group_id" in text
    assert "recipient_name" in text
    # every view kind the window can render
    for kind in ("table", "flow", "metrics", "sections", "raw"):
        assert f'"kind": "{kind}"' in text
    # the interactive form and where the answer comes back
    assert "form=" in text
    assert "Window card answered" in text


def test_every_prompt_template_actually_includes_the_guidance():
    for name in TEMPLATES:
        entry = (PROMPTS / name).read_text(encoding="utf-8")
        assert PART_REL in entry, f"{name} no longer includes {PART_REL}"
