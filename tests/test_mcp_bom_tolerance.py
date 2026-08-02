"""Regression tests for UTF-8 BOM tolerance in MCP / JSON config readers.

Background
----------
A deployment tester reported that the MCP web-UI failed with::

    Failed to read central mcp_config.json: Unexpected UTF-8 BOM
    (decode using utf-8-sig): line 1 column 1 (char 0)

after calling ``add_server``. The root cause was the agent's filesystem
tool writing JSON with ``encoding="utf-8-sig"`` (which adds a BOM),
combined with downstream readers that opened the file with the plain
``"utf-8"`` codec (which rejects BOM as a stray byte).

The fix is two-sided:

1. All read sites for ``mcp_config.json`` / ``mcp_global.json`` (and the
   generic ``json_cache.load_json_cached``) now use ``encoding="utf-8-sig"``,
   which transparently strips a leading BOM if present.
2. The ``filesystem.write_file`` / ``filesystem.replace_in_file`` tools
   no longer write with ``utf-8-sig``, so freshly produced files stay
   BOM-free and round-trip cleanly.

These tests pin the contract so future regressions are caught at
``pytest`` time rather than at deploy time.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from opensquad.json_cache import load_json_cached
from opensquad.registry import ToolRegistry

# ── json_cache.load_json_cached ──────────────────────────────────────────


def test_load_json_cached_reads_bom_file(tmp_path):
    """A JSON file prefixed with a UTF-8 BOM must load successfully."""
    path = tmp_path / "mcp_config.json"
    # Write the file with a literal BOM at the start, emulating what
    # filesystem.write_file used to produce (or what Notepad writes).
    path.write_bytes(b"\xef\xbb\xbf" + b'{"mcpServers": {"playwright": {"enabled": true}}}')

    data = load_json_cached(str(path))
    assert "mcpServers" in data
    assert data["mcpServers"]["playwright"]["enabled"] is True


def test_load_json_cached_reads_plain_utf8_file(tmp_path):
    """A plain UTF-8 file (no BOM) must still load — utf-8-sig is a superset."""
    path = tmp_path / "mcp_global.json"
    path.write_text('{"servers": {"playwright": {"enabled": true}}}', encoding="utf-8")

    data = load_json_cached(str(path))
    assert data["servers"]["playwright"]["enabled"] is True


def test_load_json_cached_caches_after_first_read(tmp_path):
    """A second call with unchanged mtime must return the cached dict."""
    path = tmp_path / "mcp_config.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"mcpServers": {}}')

    first = load_json_cached(str(path))
    second = load_json_cached(str(path))
    assert first is second  # same object — confirms caching path is exercised


def test_register_mcp_adapter_binds_registry():
    registry = ToolRegistry()
    adapter = SimpleNamespace(_registry=None)
    registry.register_mcp_adapter(adapter)
    assert adapter._registry is registry


def test_registry_routes_mcp_tools_without_namespace_registration():
    registry = ToolRegistry()
    adapter = SimpleNamespace(_registry=None, called=[])

    async def call_tool_async(tool_name, arguments):
        adapter.called.append((tool_name, arguments))
        return "ok"

    adapter.call_tool_async = call_tool_async
    registry.register_mcp_adapter(adapter)

    result = asyncio.run(registry.call("mcp__playwright__browser_navigate", {"url": "http://www.weather.com.cn"}))

    assert result == "ok"
    assert adapter.called == [("mcp__playwright__browser_navigate", {"url": "http://www.weather.com.cn"})]


def test_invalidate_mcp_tools_clears_prompt_caches():
    registry = ToolRegistry()
    registry.generate_openai_tools("all")
    registry.generate_tool_descriptions()
    assert registry._openai_tools_cache
    assert registry._desc_cache is not None

    registry.invalidate_mcp_tools()

    assert not registry._openai_tools_cache
    assert registry._desc_cache is None


def test_init_mcp_adapter_registers_registry_before_connect(monkeypatch):
    import opensquad.tools.mcp_adapter as mcp_module

    registry = ToolRegistry()

    class FakeAdapter:
        def __init__(self, config_path=None, agent_dir=None, global_disabled_servers=None):
            self.config_path = config_path
            self.agent_dir = agent_dir
            self._connected = False
            self._registry = None

        async def connect(self):
            self._connected = True

    monkeypatch.setattr(mcp_module, "MCPAdapter", FakeAdapter)
    monkeypatch.setattr(mcp_module, "_mcp_adapter", None)

    async def run():
        return await mcp_module.init_mcp_adapter(agent_dir="agent-x", registry=registry)

    adapter = asyncio.run(run())

    assert registry._mcp_adapter is adapter
    assert adapter._registry is registry
    assert adapter._connected is True


# ── filesystem.write_file / replace_in_file ──────────────────────────────


def test_filesystem_write_file_does_not_add_bom(tmp_path, monkeypatch):
    """filesystem.write_file must produce files WITHOUT a leading BOM.

    The previous behavior (utf-8-sig on write) is what caused the
    production regression; this test guards against re-introducing it.
    """
    from opensquad.tools import filesystem

    # Bypass the project-relative path safety check so we can write into tmp_path.
    monkeypatch.setattr(filesystem, "_is_path_safe", lambda path: True)

    target = str(tmp_path / "no_bom.json")
    payload = '{"mcpServers": {"playwright": {"enabled": true}}}'
    result = filesystem.write_file(target, payload)

    assert result["status"] == "success", result
    raw = open(target, "rb").read()
    assert not raw.startswith(b"\xef\xbb\xbf"), f"filesystem.write_file wrote a BOM; first bytes: {raw[:6]!r}"
    # And of course the file must still be valid JSON without it.
    assert json.loads(raw.decode("utf-8"))["mcpServers"]["playwright"]["enabled"] is True


def test_filesystem_replace_in_file_does_not_add_bom(tmp_path, monkeypatch):
    """filesystem.replace_in_file must also produce BOM-free output."""
    from opensquad.tools import filesystem

    # Bypass the project-relative path safety check so we can write into tmp_path.
    monkeypatch.setattr(filesystem, "_is_path_safe", lambda path: True)

    target = str(tmp_path / "replace.json")
    # Seed with plain utf-8 content
    open(target, "w", encoding="utf-8").write('{"k": "old"}')

    result = filesystem.replace_in_file(target, '"old"', '"new"')
    assert result["status"] == "success", result

    raw = open(target, "rb").read()
    assert not raw.startswith(b"\xef\xbb\xbf"), f"filesystem.replace_in_file wrote a BOM; first bytes: {raw[:6]!r}"
    assert json.loads(raw.decode("utf-8")) == {"k": "new"}


# ── Round-trip: BOM-tolerant readers consume both kinds of files ─────────


def test_readers_consume_both_bom_and_plain_files(tmp_path):
    """Sanity-check that the BOM-tolerant reader accepts both shapes.

    This mirrors the real-world mix that exists in user workspaces:
    some files are produced by our new utf-8 writer (no BOM), others
    by the older utf-8-sig writer or by Windows Notepad (BOM). Both
    must read cleanly through ``json.load(open(..., encoding='utf-8-sig'))``.
    """
    bom_path = tmp_path / "with_bom.json"
    bom_path.write_bytes(b"\xef\xbb\xbf" + b'{"v": 1}')

    plain_path = tmp_path / "no_bom.json"
    plain_path.write_text('{"v": 2}', encoding="utf-8")

    # The exact pattern used in the patched read sites.
    for path, expected in [(bom_path, 1), (plain_path, 2)]:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        assert data["v"] == expected, f"failed for {path}"


# ── Reproduce the original error against a plain reader ──────────────────


def test_plain_utf8_reader_still_fails_on_bom(tmp_path):
    """Pin the original bug: a plain 'utf-8' reader CANNOT read a BOM file.

    This documents *why* we had to change every read site to utf-8-sig.
    If this test ever stops raising, the documentation is stale and the
    broader fix is no longer needed (unlikely).
    """
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"x": 1}')

    with pytest.raises(json.JSONDecodeError) as excinfo, open(path, encoding="utf-8") as f:
        json.load(f)
    assert "BOM" in str(excinfo.value) or "utf-8-sig" in str(excinfo.value).lower()
