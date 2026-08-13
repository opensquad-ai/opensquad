"""
WebSearch plugin: tools are registered here; the search HTTP service is
owned by the launcher (plugin.json `service`), not by in-process subprocesses.
"""

import os

from opensquad.plugin_api import Context

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PLUGIN_DIR = os.path.join(_ROOT, "src", "plugins", "websearch")


def _ctx(auto_start: bool = False) -> Context:
    return Context(
        agent_id="test_agent",
        project_root=_ROOT,
        event_bus=None,
        config={"port": 9001, "host": "0.0.0.0", "auto_start": auto_start},
        data_dir="/tmp/test_websearch",
        plugin_dir=_PLUGIN_DIR,
    )


def test_websearch_plugin_structure():
    from plugins.websearch.plugin import WebSearchPlugin

    plugin = WebSearchPlugin(_ctx())
    assert hasattr(plugin, "on_load")
    assert hasattr(plugin, "on_unload")
    assert hasattr(plugin, "get_tool_modules")
    assert not hasattr(plugin, "_service_process")


def test_websearch_plugin_on_load_disabled():
    from plugins.websearch.plugin import WebSearchPlugin

    plugin = WebSearchPlugin(_ctx(auto_start=False))
    plugin.on_load()


def test_websearch_service_script_exists():
    service_path = os.path.join(_PLUGIN_DIR, "service", "main.py")
    assert os.path.isfile(service_path), f"Service script not found: {service_path}"


def test_get_tool_modules():
    from plugins.websearch.plugin import WebSearchPlugin

    plugin = WebSearchPlugin(_ctx())
    tools = plugin.get_tool_modules()
    assert isinstance(tools, list)
    assert len(tools) == 1
    assert tools[0]["name"] == "websearch"
    assert tools[0]["level"] == "core"
