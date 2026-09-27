"""Unit tests for scripts/verify_release_artifacts.py.

The script is the last gate before a public PyPI upload, so its own logic is
worth pinning: a false negative would publish a private file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_release_artifacts.py"
_spec = importlib.util.spec_from_file_location("verify_release_artifacts", _SCRIPT)
vra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vra)


# ── leak detection ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "path",
    [
        "plugins/news2theme/plugin.json",
        "skills/news2theme/SKILL.md",
        "skills/zt2theme/SKILL.md",
        "skills/j-space/SKILL.md",
        "skills/cursor-api/SKILL.md",
        "skills/vcs_collaboration/x.md",
        "skills/playwright/SKILL.md",
        "opensquad/gateway/nexuschat-pro/node_modules/katex/a.py",
        "plugins/websearch/service/reranker/models/model.safetensors",
        "plugins/websearch/service/reranker/models/models--Qwen/refs/main",
        "plugins/foo/__pycache__/x.pyc",
        "plugins/foo/debug.log",
        "opensquad/gateway/nexuschat-pro/dist/.env.local",
        "src/opensquad/gateway/nexuschat-pro/resources/backend-win/run/x.dll",
    ],
)
def test_leaks_flags_private_and_junk(path):
    assert vra._leaks(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        "plugins/websearch/adapter.py",
        "plugins/websearch/plugin.json",
        "model_cards/deepseek-v4-flash.json",
        "skills/agent_management/SKILL.md",
        "agents/pm/config.json",
        "opensquad/gateway/nexuschat-pro/dist/index.html",
    ],
)
def test_leaks_allows_public_files(path):
    assert vra._leaks(path) is None


def test_playwright_plugin_dir_is_not_confused_with_private_skill():
    # The private one is a *skill*; a plugin named playwright would be fine.
    assert vra._leaks("skills/playwright/SKILL.md") is not None
    assert vra._leaks("plugins/playwright/adapter.py") is None


# ── tracked-files check ───────────────────────────────────────────
def test_wheel_entries_map_onto_src():
    tracked = {"src/plugins/websearch/adapter.py"}
    assert vra._check(["plugins/websearch/adapter.py"], "wheel", tracked, wheel=True) == []
    errors = vra._check(["plugins/websearch/secret_local.py"], "wheel", tracked, wheel=True)
    assert len(errors) == 1 and "not tracked" in errors[0]


def test_sdist_entries_map_directly():
    tracked = {"src/skills/agent_management/SKILL.md"}
    entries = ["src/skills/agent_management/SKILL.md"]
    assert vra._check(entries, "sdist", tracked, wheel=False) == []
    errors = vra._check(["src/skills/local-only/x.md"], "sdist", tracked, wheel=False)
    assert len(errors) == 1 and "not tracked" in errors[0]


@pytest.mark.parametrize(
    "path",
    [
        "opensquad-0.8.48.dist-info/RECORD",
        "PKG-INFO",
        "setup.cfg",
        "opensquad.egg-info/SOURCES.txt",
        "opensquad/gateway/nexuschat-pro/dist/index.html",
        "opensquad/gateway/nexuschat-pro/dist/assets/app.js",
    ],
)
def test_generated_files_are_exempt_from_the_tracked_check(path):
    assert vra._is_generated(path)
    assert vra._check([path], "wheel", set(), wheel=True) == []


def test_tracked_check_is_skipped_without_git():
    # tracked=None means "not a git checkout" — only the leak check applies.
    assert vra._check(["plugins/news2theme/x.py"], "wheel", None, wheel=True) != []
    assert vra._check(["plugins/websearch/adapter.py"], "wheel", None, wheel=True) == []


# ── completeness ─────────────────────────────────────────────────
def test_completeness_requires_defaults_and_ui():
    minimal = (
        [
            "opensquad/gateway/config.json",
            "opensquad/gateway/nexuschat-pro/dist/index.html",
            "model_cards/deepseek-v4-flash.json",
            "pymcp/config_basic.json",
            "agents/pm/config.json",
        ]
        + [f"plugins/p{i}/a.py" for i in range(100)]
        + [f"skills/s{i}/SKILL.md" for i in range(10)]
    )
    assert vra._check_wheel_completeness(minimal) == []


def test_completeness_flags_a_wheel_without_the_defaults():
    errors = vra._check_wheel_completeness(["opensquad/cli/main.py"])
    joined = "\n".join(errors)
    for expected in ("model_cards/deepseek-v4-flash.json", "plugins/", "gateway/config.json"):
        assert expected in joined
