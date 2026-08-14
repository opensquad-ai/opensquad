"""Proxy plugins must not swallow import failures as an empty tool list."""

from __future__ import annotations

import pytest

from opensquad.plugin_api import PluginToolAttachError, proxy_tool_module
from plugins.plugin_manager import collect_proxy_tool_modules
from plugins.proxy_tools import PluginToolAttachError as DiskAttachError
from plugins.proxy_tools import proxy_tool_module as disk_proxy_tool_module


def test_proxy_tool_module_raises_on_missing_import():
    with pytest.raises(PluginToolAttachError) as ei:
        proxy_tool_module("plugins.no_such_mod.tools", name="demo")
    assert "demo" in str(ei.value)
    assert "no_such_mod" in str(ei.value)


def test_collect_records_attach_error_instead_of_empty_success():
    class Broken:
        def get_tool_modules(self):
            return [proxy_tool_module("plugins.no_such_mod.tools", name="demo")]

    descs, err = collect_proxy_tool_modules(Broken())
    assert descs == []
    assert err
    assert "demo" in err


def test_intentional_empty_proxy_list_is_not_an_error():
    class ServiceOnly:
        def get_tool_modules(self):
            return []

    descs, err = collect_proxy_tool_modules(ServiceOnly())
    assert descs == []
    assert err is None


def test_disk_helper_matches_plugin_api_reexport():
    assert PluginToolAttachError is DiskAttachError
    assert proxy_tool_module is disk_proxy_tool_module


def test_missing_get_tool_modules_is_not_an_error():
    descs, err = collect_proxy_tool_modules(object())
    assert descs == []
    assert err is None
