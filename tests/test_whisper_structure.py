"""
Whisper plugin: transcription tools live here; the Whisper HTTP service is
owned by the launcher (plugin.json `service`).
"""

import os

from opensquad.plugin_api import Context

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PLUGIN_DIR = os.path.join(_ROOT, "src", "plugins", "whisper")


def _ctx(auto_start: bool = False) -> Context:
    return Context(
        agent_id="test_agent",
        project_root=_ROOT,
        event_bus=None,
        config={"port": 5001, "host": "0.0.0.0", "auto_start": auto_start},
        data_dir="/tmp/test_whisper",
        plugin_dir=_PLUGIN_DIR,
    )


def test_whisper_plugin_structure():
    from plugins.whisper.plugin import WhisperPlugin

    plugin = WhisperPlugin(_ctx())
    assert hasattr(plugin, "on_load")
    assert hasattr(plugin, "get_tool_modules")
    assert not hasattr(plugin, "_service_process")


def test_whisper_plugin_on_load_disabled():
    from plugins.whisper.plugin import WhisperPlugin

    plugin = WhisperPlugin(_ctx(auto_start=False))
    plugin.on_load()


def test_whisper_service_script_exists():
    service_path = os.path.join(_PLUGIN_DIR, "service", "service.py")
    assert os.path.isfile(service_path), f"Service script not found: {service_path}"


def test_get_tool_modules():
    from plugins.whisper.plugin import WhisperPlugin

    plugin = WhisperPlugin(_ctx())
    tools = plugin.get_tool_modules()
    assert isinstance(tools, list)
    assert len(tools) == 1
    assert tools[0]["name"] == "whisper_transcribe"
    assert tools[0]["level"] == "core"
