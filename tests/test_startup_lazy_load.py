"""Startup-speed helpers: plugin discovery filtering + lazy Google SDK."""

from __future__ import annotations

from plugins.plugin_manager import PluginManager


def test_plugin_wanted_by_manifest_auto_register():
    manifest = {
        "name": "websearch",
        "tools": [{"name": "websearch", "auto_register": True}],
        "hooks": [],
    }
    assert PluginManager._plugin_wanted_by_manifest(manifest, set(), "websearch") is True


def test_plugin_wanted_by_manifest_explicit_tool():
    manifest = {
        "name": "whisper",
        "tools": [{"name": "whisper_transcribe", "auto_register": False}],
        "hooks": [],
    }
    assert PluginManager._plugin_wanted_by_manifest(manifest, set(), "whisper") is False
    assert PluginManager._plugin_wanted_by_manifest(manifest, {"whisper_transcribe"}, "whisper") is True
    assert PluginManager._plugin_wanted_by_manifest(manifest, {"whisper"}, "whisper") is True


def test_plugin_wanted_by_manifest_hooks():
    manifest = {"name": "hooky", "tools": [], "hooks": ["on_message_received"]}
    assert PluginManager._plugin_wanted_by_manifest(manifest, set(), "hooky") is True


def test_google_api_module_import_does_not_load_sdk():
    import importlib
    import sys

    # Fresh-ish check: google_api should not force generativeai at import.
    sys.modules.pop("opensquad.google_api", None)
    mod = importlib.import_module("opensquad.google_api")
    assert mod._GENAI_AVAILABLE is None
    assert "google.generativeai" not in sys.modules or mod._genai_mod is None
