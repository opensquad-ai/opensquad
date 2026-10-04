"""A document that names a collaboration card must name one that exists.

Reported from a review, and confirmed here: COLLABORATION.md said there was no centralized board
(there is one — `collab_board.py`), ARCHITECTURE.md had an unrelated module syncing state to
`workspace/collab/`, and a skill's README listed playbooks as built-in cards — `autonomous_vcs_dev`,
`quant_backtesting_dev`, `godot_roguelike_team` — while the guides listed a `research_task` card that
does not exist and omitted two that do. A card is exactly what `start_collaboration` loads, so a
document that invents one sends a PM to a card that cannot load.

The cards themselves are the evidence: `src/collab_cards/*.md`.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CARDS = _ROOT / "src" / "collab_cards"

README = _ROOT / "src/skills/collaboration_management/README.md"
GUIDE_EN = _ROOT / "doc_en/agent_management_skills_guide.md"
GUIDE_CN = _ROOT / "doc_cn/agent_management_skills_guide.md"
COLLAB_EN = _ROOT / "doc_en/COLLABORATION.md"
COLLAB_CN = _ROOT / "doc_cn/COLLABORATION.md"
ARCH_EN = _ROOT / "doc_en/ARCHITECTURE.md"
ARCH_CN = _ROOT / "doc_cn/ARCHITECTURE.md"
INTRO = _ROOT / "src/skills/opensquad_intro/SKILL.md"

_CARD_TABLES = [
    (README, "## Built-in Collaboration Cards"),
    (GUIDE_EN, "#### Built-in collaboration cards"),
    (GUIDE_CN, "#### 内置协作卡"),
]


def _names_under(path: Path, heading: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    assert heading in text, f"{path.name} lost the heading {heading!r}"
    body = re.split(r"\n#{2,4} ", text.split(heading, 1)[1], maxsplit=1)[0]
    return re.findall(r"\*\*([a-z0-9_]+)\*\*", body)


def _real_cards() -> list[str]:
    return sorted(p.stem for p in _CARDS.glob("*.md"))


def test_the_card_set_is_what_the_repository_holds():
    assert _real_cards() == [
        "code_review",
        "distributed_deep_research",
        "general_software_dev_collab",
        "software_dev_team",
    ]


def test_every_documented_card_exists_on_disk():
    for path, heading in _CARD_TABLES:
        names = _names_under(path, heading)
        assert names, f"{path.name} no longer lists cards under {heading!r}"
        for name in names:
            assert (_CARDS / f"{name}.md").exists(), f"{path.name} calls '{name}' a card; it is not one"


def test_each_table_lists_exactly_the_real_cards():
    real = _real_cards()
    for path, heading in _CARD_TABLES:
        assert sorted(_names_under(path, heading)) == real, path.name


def test_the_playbook_table_is_not_claimed_to_be_cards():
    """Its names come from this skill's own sections, and none of them may be presented as a card."""
    text = README.read_text(encoding="utf-8")
    assert "### Scenario playbooks" in text
    playbooks = re.findall(r"\*\*([a-z0-9_]+)\*\*", text.split("### Scenario playbooks", 1)[1])
    assert playbooks, "the playbook table disappeared"
    for name in playbooks:
        assert not (_CARDS / f"{name}.md").exists(), f"'{name}' is listed as a playbook and is a card"


def test_the_board_replaces_the_old_story():
    for path in (COLLAB_EN, COLLAB_CN, ARCH_EN, ARCH_CN, INTRO):
        text = path.read_text(encoding="utf-8")
        assert "workspace/collab/" not in text, f"{path.name} still points at the old workspace"
        assert "no centralized blackboard" not in text, path.name
        assert "没有集中式黑板" not in text, path.name
        assert "auto-syncs to" not in text and "自动同步到" not in text, path.name


def test_the_board_sections_name_the_real_tools():
    for path in (COLLAB_EN, COLLAB_CN):
        text = path.read_text(encoding="utf-8")
        for tool in ("board_view(collab_id)", "board_list_tasks(collab_id)", "update_task_progress("):
            assert tool in text, (path.name, tool)
        assert "create_board(" not in text, f"{path.name} invents a board API that is not the board"
