"""plugin.json is rewritten on every plugin boot; skip the write when unchanged."""

from __future__ import annotations

import json
from pathlib import Path

from plugins.plugin_manager import merge_plugin_manifest, write_plugin_manifest_if_changed


def test_merge_preserves_runtime_keys():
    existing = {
        "name": "demo",
        "version": "0.1",
        "enabled": False,
        "service": {"port": 9},
        "extra": "keep-me",
    }
    generated = {"name": "demo", "version": "0.2", "enabled": True}
    merged = merge_plugin_manifest(existing, generated)
    assert merged["version"] == "0.2"
    assert merged["enabled"] is False
    assert merged["service"] == {"port": 9}
    assert merged["extra"] == "keep-me"


def test_write_skipped_when_unchanged(tmp_path: Path):
    path = tmp_path / "plugin.json"
    payload = {"name": "demo", "version": "1", "enabled": True}
    path.write_text(json.dumps(payload), encoding="utf-8")
    mtime = path.stat().st_mtime_ns
    assert write_plugin_manifest_if_changed(str(path), dict(payload), payload) == "unchanged"
    assert path.stat().st_mtime_ns == mtime


def test_write_updates_when_generated_changes(tmp_path: Path):
    path = tmp_path / "plugin.json"
    existing = {"name": "demo", "version": "1", "enabled": True}
    path.write_text(json.dumps(existing), encoding="utf-8")
    status = write_plugin_manifest_if_changed(
        str(path),
        {"name": "demo", "version": "2", "enabled": True},
        existing,
    )
    assert status == "updated"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["version"] == "2"
