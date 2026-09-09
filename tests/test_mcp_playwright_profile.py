"""Playwright MCP persistent profile injection."""

from __future__ import annotations

import os

from opensquad.tools.mcp_adapter import ensure_playwright_persistent_profile, playwright_mcp_profile_dir


def test_playwright_profile_dir_under_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENSQUAD_WORKSPACE", str(tmp_path))
    from opensquad._syscfg import _workspace as ws

    ws._WORKSPACE_ROOT = str(tmp_path)

    expected = os.path.join(tmp_path, "data", "mcp_browser_profiles", "playwright")
    assert playwright_mcp_profile_dir() == expected


def test_injects_user_data_dir_for_playwright_server(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENSQUAD_WORKSPACE", str(tmp_path))
    from opensquad._syscfg import _workspace as ws

    ws._WORKSPACE_ROOT = str(tmp_path)

    cfg = {"command": "npx", "args": ["-y", "@playwright/mcp"]}
    args, env = ensure_playwright_persistent_profile("playwright", cfg)

    profile = os.path.join(tmp_path, "data", "mcp_browser_profiles", "playwright")
    assert args == ["-y", "@playwright/mcp", "--user-data-dir", profile]
    assert env is None
    assert os.path.isdir(profile)


def test_does_not_override_explicit_user_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENSQUAD_WORKSPACE", str(tmp_path))
    from opensquad._syscfg import _workspace as ws

    ws._WORKSPACE_ROOT = str(tmp_path)

    custom = str(tmp_path / "custom-profile")
    cfg = {"args": ["-y", "@playwright/mcp", "--user-data-dir", custom]}
    args, _ = ensure_playwright_persistent_profile("playwright", cfg)
    assert args == ["-y", "@playwright/mcp", "--user-data-dir", custom]


def test_does_not_inject_when_isolated():
    cfg = {"args": ["-y", "@playwright/mcp", "--isolated"]}
    args, _ = ensure_playwright_persistent_profile("playwright", cfg)
    assert args == ["-y", "@playwright/mcp", "--isolated"]


def test_does_not_inject_when_env_user_data_dir(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_MCP_USER_DATA_DIR", "/tmp/custom")
    cfg = {"args": ["-y", "@playwright/mcp"]}
    args, _ = ensure_playwright_persistent_profile("playwright", cfg)
    assert args == ["-y", "@playwright/mcp"]


def test_non_playwright_server_unchanged():
    cfg = {"args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}
    args, env = ensure_playwright_persistent_profile("filesystem", cfg)
    assert args == ["-y", "@modelcontextprotocol/server-filesystem", "."]
    assert env is None


def test_detects_playwright_by_package_name():
    cfg = {"args": ["-y", "@playwright/mcp@latest"]}
    args, _ = ensure_playwright_persistent_profile("browser", cfg)
    assert "--user-data-dir" in args
