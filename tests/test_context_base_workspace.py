"""AGENT_WORKSPACE must distinguish session project cwd from OpenSquad data root."""

from __future__ import annotations

import os
from pathlib import Path

import opensquad.context_base as context_base


def test_agent_workspace_does_not_treat_data_root_as_project(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "opensquad_runtime_deploy"
    agent_dir = data_root / "agents" / "coder"
    agent_dir.mkdir(parents=True)
    agent_md = agent_dir / "agent.md"
    agent_md.write_text("# mem\n", encoding="utf-8")
    (data_root / "workspace").mkdir()

    monkeypatch.setattr(context_base, "_agent_dir", str(agent_dir))
    monkeypatch.setattr(context_base, "_agent_md_path", str(agent_md))
    monkeypatch.setattr(context_base, "_project_root", str(data_root))
    monkeypatch.setattr(context_base, "_data_root", str(data_root))
    monkeypatch.setattr(context_base, "_agent_config", {})
    monkeypatch.setattr(context_base, "_resolve_session_project_cwd", lambda: "")

    system_vars, _ = context_base.inject_standard(
        {"query": "hi", "source": "cli", "chat_api": None, "recent_messages": []}
    )
    text = system_vars["AGENT_WORKSPACE"]

    assert "Currently Active Workspace Root" not in text
    assert "Shared Working Directory" not in text
    assert "OpenSquad Data Root" in text
    assert str(data_root) in text
    assert "NOT the user project" in text
    assert "Not set for this session yet" in text
    # Must not claim data_root/workspace is where project files go
    assert f"`{os.path.join(str(data_root), 'workspace')}`" not in text


def test_agent_workspace_injects_session_project_cwd(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "opensquad_runtime_deploy"
    project = tmp_path / "my_project"
    agent_dir = data_root / "agents" / "coder"
    agent_dir.mkdir(parents=True)
    project.mkdir()
    agent_md = agent_dir / "agent.md"
    agent_md.write_text("# mem\n", encoding="utf-8")

    monkeypatch.setattr(context_base, "_agent_dir", str(agent_dir))
    monkeypatch.setattr(context_base, "_agent_md_path", str(agent_md))
    monkeypatch.setattr(context_base, "_project_root", str(data_root))
    monkeypatch.setattr(context_base, "_data_root", str(data_root))
    monkeypatch.setattr(context_base, "_agent_config", {})
    monkeypatch.setattr(
        context_base,
        "_resolve_session_project_cwd",
        lambda: os.path.normcase(os.path.abspath(str(project))),
    )

    system_vars, _ = context_base.inject_standard(
        {"query": "hi", "source": "cli", "chat_api": None, "recent_messages": []}
    )
    text = system_vars["AGENT_WORKSPACE"]

    assert "Current Project Working Directory" in text
    assert os.path.normcase(os.path.abspath(str(project))) in text
    assert "OpenSquad Data Root" in text
    assert str(data_root) in text
    assert "Not set for this session yet" not in text


def test_workspace_get_current_separates_data_root(tmp_path: Path, monkeypatch):
    from opensquad.tools import workspace as ws_tool

    data_root = tmp_path / "opensquad_runtime_deploy"
    project = tmp_path / "quant_backtest_v2"
    data_root.mkdir()
    project.mkdir()

    class _Sys:
        @staticmethod
        def get_workspace():
            return str(data_root)

    monkeypatch.setattr("opensquad.system_config.syscfg", _Sys, raising=False)
    monkeypatch.setattr(ws_tool, "syscfg", _Sys, raising=False)

    # Patch import path used inside get_current
    import opensquad.system_config as sc

    monkeypatch.setattr(sc, "syscfg", _Sys)

    from opensquad.utils import path_utils

    path_utils.set_session_cwd_override(None)
    empty = ws_tool.get_current()
    assert empty["status"] == "ok"
    assert empty["workspace_root"] == ""
    assert empty["session_cwd"] == ""
    assert empty["data_root"] == str(data_root)
    assert empty["exists"] is False

    path_utils.set_session_cwd_override(str(project))
    try:
        filled = ws_tool.get_current()
        assert filled["workspace_root"] == os.path.normcase(os.path.abspath(str(project)))
        assert filled["session_cwd"] == os.path.normcase(os.path.abspath(str(project)))
        assert filled["data_root"] == str(data_root)
        assert filled["exists"] is True
    finally:
        path_utils.set_session_cwd_override(None)
