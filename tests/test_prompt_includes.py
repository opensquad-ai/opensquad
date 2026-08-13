"""Tests for prompt {{include:...}} expansion."""

from __future__ import annotations

from pathlib import Path

import pytest

from opensquad.prompt_includes import expand_includes, read_prompt_with_includes

PROMPTS = Path(__file__).resolve().parents[1] / "src" / "prompts"
ENTRIES = ["base_fc.md", "base_xml.md", "thought_fc.md", "thought_xml.md"]


def test_expand_includes_simple(tmp_path: Path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "a.md").write_text("AAA\n", encoding="utf-8")
    (tmp_path / "parts" / "b.md").write_text("BBB\n", encoding="utf-8")
    entry = "{{include:parts/a.md}}\n{{include:parts/b.md}}\n"
    assert expand_includes(entry, str(tmp_path)) == "AAA\nBBB\n"


def test_expand_includes_nested(tmp_path: Path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "inner.md").write_text("INNER\n", encoding="utf-8")
    (tmp_path / "parts" / "outer.md").write_text("BEFORE\n{{include:parts/inner.md}}AFTER\n", encoding="utf-8")
    entry = "{{include:parts/outer.md}}\n"
    assert expand_includes(entry, str(tmp_path)) == "BEFORE\nINNER\nAFTER\n"


def test_expand_includes_rejects_escape(tmp_path: Path):
    with pytest.raises(ValueError, match="escapes"):
        expand_includes("{{include:../secret.md}}", str(tmp_path))


def test_expand_includes_rejects_cycle(tmp_path: Path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "a.md").write_text("{{include:parts/b.md}}\n", encoding="utf-8")
    (tmp_path / "parts" / "b.md").write_text("{{include:parts/a.md}}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Circular"):
        expand_includes("{{include:parts/a.md}}", str(tmp_path))


@pytest.mark.parametrize("entry", ENTRIES)
def test_real_prompt_entries_expand(entry: str):
    path = PROMPTS / entry
    assert path.is_file(), f"missing {path}"
    text = read_prompt_with_includes(str(path), str(PROMPTS))
    assert "{{include:" not in text
    assert "## 1. Role Definition" in text
    assert "{{EXPERT_ROLE_CARD}}" in text
    assert "## 7. Memory System" in text
    # Size sanity: shared parts should still yield a full prompt
    assert len(text) > 20_000
    # Heavy mode rulebooks stay out of the cached system prefix
    assert "verifiable completion contract" not in text
    assert "Cursor-style design before coding" not in text
    assert "HARD RULE — you MUST call real tools" not in text
    assert "mcp__{server_name}__{tool_name}" not in text
    assert "goal__mark_achieved" in text


def test_load_prompt_part_and_scheduled_helper():
    from opensquad.prompt_includes import is_scheduled_task_turn, load_prompt_part

    stub = load_prompt_part("parts/common_2.20_mode_stubs.md")
    assert "goal__mark_achieved" in stub
    with pytest.raises(ValueError):
        load_prompt_part("../secret.md")
    assert is_scheduled_task_turn("[Scheduled Task: daily]")
    assert not is_scheduled_task_turn("hello")
