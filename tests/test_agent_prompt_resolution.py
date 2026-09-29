"""A pip/npm install must be able to build an agent system prompt.

The 0.8.47/0.8.48 artifacts carried no prompt template at all — the published
wheel has zero `.md` files under any `prompts/` — so every agent boot raised
``FileNotFoundError: Base prompt not found:
...\\site-packages\\src\\opensquad\\prompts\\thought_fc.md``: the coder crash-looped
and the pm agent never reached its web port, while the UI reported "crashed" /
"reconnecting".

The wheel ships ``src/prompts`` as a *top-level sibling* of the ``opensquad``
package, the same layout PyInstaller uses in
``resources/backend-win/run/_internal/prompts``. These tests pin that layout
against the candidate chain in ``build_system_prompt`` so a future release
cannot drop the directory again without a red test.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from opensquad import agents_boot

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_SRC = REPO_ROOT / "src" / "prompts"
# `build_system_prompt` names the template as f"{prefix}_{tool_fmt}.md"
TEMPLATES = ("base_fc.md", "base_xml.md", "thought_fc.md", "thought_xml.md")


def _site_packages_shaped(tmp_path: Path, *, with_prompts: bool) -> Path:
    """Stand-in for site-packages: package siblings only, no `src/` level."""
    root = tmp_path / "site-packages"
    (root / "opensquad").mkdir(parents=True)
    if with_prompts:
        shutil.copytree(PROMPTS_SRC, root / "prompts")
    return root


def _point_builtin_root_at(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(agents_boot.syscfg, "get_builtin_root", lambda: str(root))
    monkeypatch.setattr(agents_boot, "_resolve_tool_format", lambda config: "fc")


@pytest.mark.parametrize("is_think", [False, True])
def test_prompt_resolves_from_top_level_prompts(tmp_path, monkeypatch, is_think):
    _point_builtin_root_at(monkeypatch, _site_packages_shaped(tmp_path, with_prompts=True))
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "role.md").write_text("ROLE-CARD-MARKER", encoding="utf-8")

    prompt = agents_boot.build_system_prompt({"prompt": {}, "model": {"is_think": is_think}}, str(agent_dir))

    assert "ROLE-CARD-MARKER" in prompt
    assert "{{EXPERT_ROLE_CARD}}" not in prompt
    # `parts/*.md` ship alongside the templates and are expanded at read time
    assert "{{include:" not in prompt
    assert len(prompt) > 5000


def test_missing_prompts_reproduce_the_release_failure(tmp_path, monkeypatch):
    """The 0.8.48 artifact exactly: no prompts dir anywhere, boot cannot start."""
    _point_builtin_root_at(monkeypatch, _site_packages_shaped(tmp_path, with_prompts=False))
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="Base prompt not found"):
        agents_boot.build_system_prompt({"prompt": {}, "model": {}}, str(agent_dir))


def test_every_template_the_boot_code_names_ships():
    for name in TEMPLATES:
        assert (PROMPTS_SRC / name).is_file(), f"missing template: {name}"
    parts = sorted((PROMPTS_SRC / "parts").glob("*.md"))
    assert len(parts) >= 40, f"only {len(parts)} prompt parts found"
