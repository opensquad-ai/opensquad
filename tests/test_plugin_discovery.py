"""Plugin discovery must see builtin + workspace trees."""

from plugins.plugin_manager import collect_plugin_dirs


def test_collect_plugin_dirs_includes_websearch():
    found = collect_plugin_dirs()
    assert "websearch" in found
    assert "whisper" in found
    assert len(found) >= 10
