"""Uninstall must resolve names, never rmtree bundled seeds, and hide them locally."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from opensquad import launcher_main as lm
from opensquad._syscfg import _workspace as ws
from plugins.plugin_manager import collect_plugin_dirs


def _write_plugin(root: Path, dir_name: str, declared_name: str) -> Path:
    plugin_dir = root / dir_name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": declared_name, "enabled": True}),
        encoding="utf-8",
    )
    return plugin_dir


def _write_skill(root: Path, dir_name: str, declared_name: str) -> Path:
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.json").write_text(
        json.dumps({"name": declared_name}),
        encoding="utf-8",
    )
    return skill_dir


def _patch_plugin_trees(monkeypatch: pytest.MonkeyPatch, workspace: Path, builtin: Path) -> None:
    monkeypatch.setattr(lm, "PLUGINS_DIR", str(workspace))
    monkeypatch.setattr(lm, "BUILTIN_PLUGINS_DIR", str(builtin))
    monkeypatch.setattr(lm, "_BUILTIN_PLUGINS", {})

    def _collect() -> dict[str, str]:
        out: dict[str, str] = {}
        for root in (builtin, workspace):
            if not root.is_dir():
                continue
            for entry in root.iterdir():
                if entry.is_dir():
                    out[entry.name] = str(entry)
        return out

    monkeypatch.setattr(lm, "_collect_plugin_dirs", _collect)


def _patch_skill_trees(monkeypatch: pytest.MonkeyPatch, workspace: Path, builtin: Path) -> None:
    monkeypatch.setattr(lm, "SKILLS_DIR", str(workspace))
    monkeypatch.setattr(lm, "BUILTIN_SKILLS_DIR", str(builtin))
    monkeypatch.setattr(lm, "_skill_search_dirs", lambda: [str(builtin), str(workspace)])


def _patch_uninstalled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "uninstalled_resources.json"
    monkeypatch.setattr(ws, "uninstalled_resources_path", lambda: str(path))
    return path


def test_resolve_plugin_dir_name_whisper_transcribe():
    from opensquad.resource_uninstall import resolve_plugin_dir_name

    assert resolve_plugin_dir_name("whisper_transcribe") == "whisper"
    assert resolve_plugin_dir_name("whisper") == "whisper"
    plugin_dir, dir_name = lm._resolve_plugin_dir("whisper_transcribe")
    assert dir_name == "whisper"
    assert plugin_dir is not None
    assert os.path.basename(plugin_dir) == "whisper"


def test_bundled_plugin_uninstall_is_local_hide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "ws_plugins"
    builtin = tmp_path / "builtin_plugins"
    workspace.mkdir()
    seed = _write_plugin(builtin, "whisper", "whisper_transcribe")
    _patch_plugin_trees(monkeypatch, workspace, builtin)

    overlay, dir_name, status, err = lm.plan_resource_uninstall("plugins", "whisper_transcribe")
    assert status == 200
    assert err == ""
    assert dir_name == "whisper"
    assert overlay is None
    assert seed.is_dir()


def test_system_plugin_cannot_be_uninstalled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "ws_plugins"
    builtin = tmp_path / "builtin_plugins"
    workspace.mkdir()
    _write_plugin(builtin, "websearch", "websearch")
    _patch_plugin_trees(monkeypatch, workspace, builtin)
    monkeypatch.setattr(lm, "_BUILTIN_PLUGINS", {"websearch": {"default_enabled": True}})

    overlay, dir_name, status, err = lm.plan_resource_uninstall("plugins", "websearch")
    assert overlay is None
    assert dir_name is None
    assert status == 400
    assert "system plugin" in err


def test_workspace_overlay_is_deletable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "ws_plugins"
    builtin = tmp_path / "builtin_plugins"
    _write_plugin(builtin, "whisper", "whisper_transcribe")
    overlay_dir = _write_plugin(workspace, "whisper", "whisper_transcribe")
    _patch_plugin_trees(monkeypatch, workspace, builtin)

    overlay, dir_name, status, err = lm.plan_resource_uninstall("plugins", "whisper_transcribe")
    assert status == 200
    assert err == ""
    assert dir_name == "whisper"
    assert os.path.abspath(overlay) == os.path.abspath(str(overlay_dir))


def test_same_tree_workspace_does_not_rmtree_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    shared = tmp_path / "plugins"
    seed = _write_plugin(shared, "whisper", "whisper_transcribe")
    _patch_plugin_trees(monkeypatch, shared, shared)

    overlay, dir_name, status, err = lm.plan_resource_uninstall("plugins", "whisper_transcribe")
    assert status == 200
    assert overlay is None
    assert dir_name == "whisper"
    assert err == ""
    assert seed.is_dir()


def test_unknown_plugin_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "ws_plugins"
    builtin = tmp_path / "builtin_plugins"
    workspace.mkdir()
    builtin.mkdir()
    _patch_plugin_trees(monkeypatch, workspace, builtin)

    _overlay, _dir_name, status, err = lm.plan_resource_uninstall("plugins", "does_not_exist")
    assert status == 404
    assert "not found" in err


def test_bundled_skill_uninstall_is_local_hide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "ws_skills"
    builtin = tmp_path / "builtin_skills"
    workspace.mkdir()
    seed = _write_skill(builtin, "my-skill", "my_skill_pkg")
    _patch_skill_trees(monkeypatch, workspace, builtin)

    overlay, dir_name, status, err = lm.plan_resource_uninstall("skills", "my_skill_pkg")
    assert status == 200
    assert overlay is None
    assert dir_name == "my-skill"
    assert seed.is_dir()


def test_workspace_skill_overlay_is_deletable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "ws_skills"
    builtin = tmp_path / "builtin_skills"
    _write_skill(builtin, "my-skill", "my_skill_pkg")
    overlay_dir = _write_skill(workspace, "my-skill", "my_skill_pkg")
    _patch_skill_trees(monkeypatch, workspace, builtin)

    overlay, dir_name, status, err = lm.plan_resource_uninstall("skills", "my_skill_pkg")
    assert status == 200
    assert err == ""
    assert dir_name == "my-skill"
    assert os.path.abspath(overlay) == os.path.abspath(str(overlay_dir))


def test_is_bundled_resource_same_tree():
    shared = os.path.abspath("/tmp/opensquad-plugins")
    plugin = os.path.join(shared, "whisper")
    assert lm._is_bundled_resource(plugin, shared, shared) is True


def test_mark_locally_uninstalled_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _patch_uninstalled(monkeypatch, tmp_path)
    assert ws.is_locally_uninstalled("plugins", "whisper") is False
    ws.mark_locally_uninstalled("plugins", "whisper")
    assert ws.is_locally_uninstalled("plugins", "whisper") is True
    ws.clear_locally_uninstalled("plugins", "whisper")
    assert ws.is_locally_uninstalled("plugins", "whisper") is False


def test_alias_uninstall_succeeds_without_on_disk_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "ws_plugins"
    builtin = tmp_path / "builtin_plugins"
    workspace.mkdir()
    builtin.mkdir()
    _patch_plugin_trees(monkeypatch, workspace, builtin)

    overlay, dir_name, status, err = lm.plan_resource_uninstall("plugins", "whisper_transcribe")
    assert status == 200
    assert err == ""
    assert dir_name == "whisper"
    assert overlay is None


def test_hide_plugin_tombstones_json_name_and_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _patch_uninstalled(monkeypatch, tmp_path)
    from opensquad.resource_uninstall import hide_plugin

    assert hide_plugin("whisper_transcribe") == "whisper"
    assert ws.is_locally_uninstalled("plugins", "whisper")
    assert ws.is_locally_uninstalled("plugins", "whisper_transcribe")


def test_collect_plugin_dirs_skips_locally_uninstalled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _patch_uninstalled(monkeypatch, tmp_path)
    found = collect_plugin_dirs()
    assert "whisper" in found
    ws.mark_locally_uninstalled("plugins", "whisper")
    found = collect_plugin_dirs()
    assert "whisper" not in found
    ws.clear_locally_uninstalled("plugins", "whisper")
    found = collect_plugin_dirs()
    assert "whisper" in found
