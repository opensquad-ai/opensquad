from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from opensquad.runner_bootstrap import build_full_prompt, create_memory_manager, resolve_prompt_path


@pytest.fixture
def runner_bootstrap_module(monkeypatch):
    module_name = "opensquad.tools.agent_memory_tool.memory"
    original_module = sys.modules.get(module_name)
    sys.modules.pop(module_name, None)
    spec = importlib.util.find_spec(module_name)
    yield spec
    if original_module is not None:
        sys.modules[module_name] = original_module
    else:
        sys.modules.pop(module_name, None)


def test_resolve_prompt_path_prefers_agent_relative(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    prompt_path = agent_dir / "role.md"
    prompt_path.write_text("agent role", encoding="utf-8")

    resolved = resolve_prompt_path("role.md", str(agent_dir))

    assert resolved == str(prompt_path)


def test_build_full_prompt_falls_back_to_default_when_missing(tmp_path: Path):
    config = {
        "prompt": {
            "base": "missing-base.md",
            "role": "missing-role.md",
        }
    }

    prompt = build_full_prompt(config, str(tmp_path))

    assert prompt == "You are a helpful assistant."


def test_build_full_prompt_reads_agent_relative_files(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "base.md").write_text("Base prompt", encoding="utf-8")
    (agent_dir / "role.md").write_text("Role prompt", encoding="utf-8")
    config = {"prompt": {"base": "base.md", "role": "role.md"}}

    prompt = build_full_prompt(config, str(agent_dir))

    assert prompt == "Base prompt\n\nRole prompt"


def test_create_memory_manager_returns_none_without_tool(tmp_path: Path):
    config = {"tools": [], "agent_name": "demo"}

    memory_manager = create_memory_manager(config, str(tmp_path))

    assert memory_manager is None


def test_create_memory_manager_gracefully_skips_missing_dependency(
    tmp_path: Path, monkeypatch, runner_bootstrap_module
):
    config = {"tools": ["long_memory"], "agent_name": "demo"}

    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name, package=None):
        if name == "opensquad.tools.agent_memory_tool.memory":
            return None
        return original_find_spec(name, package)

    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "opensquad.tools.agent_memory_tool.memory":
            raise ImportError("missing dependency")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr("builtins.__import__", fake_import)

    memory_manager = create_memory_manager(config, str(tmp_path))

    assert memory_manager is None
