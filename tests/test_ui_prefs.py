"""Host UI prefs merge: packaged and dev origins must share theme/lang."""

from __future__ import annotations

from opensquad.ui_prefs import merge_ui_prefs, snapshot_has_workspaces


def test_merge_skips_stale_saved_at():
    existing = {"savedAt": 200, "theme": {"preset": "pure-white"}}
    incoming = {"savedAt": 100, "theme": {"preset": "ink-green"}}
    status, next_state = merge_ui_prefs(existing, incoming)
    assert status == "skipped"
    assert next_state["theme"]["preset"] == "pure-white"


def test_merge_keeps_existing_when_incoming_field_empty():
    existing = {"savedAt": 1, "theme": {"preset": "pure-white"}, "lang": "zh"}
    incoming = {"savedAt": 2, "theme": None, "lang": ""}
    status, next_state = merge_ui_prefs(existing, incoming)
    assert status == "ok"
    assert next_state["theme"]["preset"] == "pure-white"
    assert next_state["lang"] == "zh"


def test_merge_writes_newer_theme():
    existing = {"savedAt": 1, "theme": {"preset": "ink-green"}}
    incoming = {"savedAt": 2, "theme": {"preset": "pure-white"}, "lang": "en"}
    status, next_state = merge_ui_prefs(existing, incoming)
    assert status == "ok"
    assert next_state["theme"]["preset"] == "pure-white"
    assert next_state["lang"] == "en"


def test_snapshot_has_workspaces():
    assert snapshot_has_workspaces({"workspaces": [{"id": "a", "rootPath": "/x"}]})
    assert not snapshot_has_workspaces({"workspaces": []})
    assert not snapshot_has_workspaces(None)
    assert not snapshot_has_workspaces("nope")
