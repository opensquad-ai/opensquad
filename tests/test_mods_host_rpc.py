"""S0 — mod host NDJSON transport: framing, id correlation, budget, reaping.

Covers docs/mods-bridge-m0.md §2/§3. No mods are loaded here; that is S1.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from plugins.mods_host import _node  # noqa: E402
from plugins.mods_host._rpc import (  # noqa: E402
    NodeHostClient,
    NodeHostError,
    NodeHostTimeoutError,
)

pytestmark = pytest.mark.skipif(not _node.resolve_node_executable(), reason="node runtime not available on this box")


# ── node resolution ─────────────────────────────────────────────────────────


def test_resolve_node_finds_a_runtime():
    exe = _node.resolve_node_executable()
    assert exe, "expected a node executable"
    assert os.path.isabs(exe)


def test_resolve_prefers_explicit_env_over_path(monkeypatch, tmp_path):
    fake = tmp_path / ("node.exe" if sys.platform == "win32" else "node")
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENSQUAD_NODE", str(fake))
    assert _node.resolve_node_executable() == str(fake)


def test_resolve_returns_empty_when_nothing_matches(monkeypatch):
    monkeypatch.setenv("OPENSQUAD_NODE", str(uuid.uuid4()))
    monkeypatch.setenv("OPENSQUAD_NODE_HOME", str(uuid.uuid4()))
    monkeypatch.setattr(_node.shutil, "which", lambda _name: None)
    assert _node.resolve_node_executable() == ""


# ── transport ───────────────────────────────────────────────────────────────


async def test_ping_roundtrip():
    client = NodeHostClient()
    try:
        result = await client.request("ping")
        assert result["pong"] is True
        assert result["host"] == "mods_host"
        assert result["node"].startswith("v")
        assert result["pid"] == client.pid
        assert result["apiVersion"] == 1
    finally:
        client.close_sync()


async def test_concurrent_requests_are_id_correlated():
    client = NodeHostClient()
    try:
        results = await asyncio.gather(*(client.request("ping") for _ in range(8)))
        assert len(results) == 8
        assert all(r["pong"] is True for r in results)
        # Every reply came from the same host process.
        assert len({r["pid"] for r in results}) == 1
    finally:
        client.close_sync()


async def test_init_with_no_mods():
    client = NodeHostClient()
    try:
        result = await client.request("init", {"mods": []})
        assert result["loaded"] == []
        assert result["diagnostics"] == []
        assert result["registered"] == 0
    finally:
        client.close_sync()


async def test_unknown_method_reports_an_error():
    client = NodeHostClient()
    try:
        with pytest.raises(NodeHostError) as excinfo:
            await client.request("does.not.exist")
        assert "unknown method" in str(excinfo.value)
    finally:
        client.close_sync()


async def test_timeout_gives_up_and_drops_the_late_reply(caplog):
    client = NodeHostClient()
    try:
        with caplog.at_level(logging.WARNING, logger="plugins.mods_host.rpc"):
            with pytest.raises(NodeHostTimeoutError):
                await client.request("clock.sleep", {"ms": 400}, timeout=0.1)

            # The real invariant: the abandoned request must not corrupt the
            # pending table, so the next request still round-trips.
            result = await client.request("ping")
            assert result["pong"] is True

            # And the late reply is recognised as stale rather than mis-matched.
            deadline = time.time() + 2.0
            while time.time() < deadline and "dropping late/unmatched reply" not in caplog.text:
                await asyncio.sleep(0.05)
            assert "dropping late/unmatched reply" in caplog.text
    finally:
        client.close_sync()


async def test_inbound_request_is_served_not_mistaken_for_a_stale_reply():
    """An inbound request carries the *other* id space, so it is never pending.

    Checking "unknown id => stale reply" before "has a method => request" drops
    every reentrant call and makes the outer request time out. This locks the
    ordering (found by the real-mod smoke test, docs/mods-bridge-m0.md §9).
    """
    seen: list[str] = []

    async def handler(method: str, params: dict):
        seen.append(method)
        return {"ok": True}

    client = NodeHostClient(request_handler=handler)
    try:
        await client.start()
        client._on_frame(json.dumps({"id": "h1", "method": "gate.session.cwd", "params": {}}))
        for _ in range(50):
            if seen:
                break
            await asyncio.sleep(0.02)
        assert seen == ["gate.session.cwd"], "inbound request was dropped as a stale reply"
    finally:
        client.close_sync()


async def test_unmatched_reply_without_a_method_is_still_dropped(caplog):
    client = NodeHostClient()
    try:
        await client.start()
        with caplog.at_level(logging.WARNING, logger="plugins.mods_host.rpc"):
            client._on_frame(json.dumps({"id": "p999", "result": {}}))
            await asyncio.sleep(0.05)
        assert "dropping late/unmatched reply" in caplog.text
    finally:
        client.close_sync()


async def test_close_reaps_the_child_and_fails_pending():
    client = NodeHostClient()
    await client.start()
    pid = client.pid
    assert pid is not None

    pending = asyncio.ensure_future(client.request("clock.sleep", {"ms": 5000}, timeout=30))
    await asyncio.sleep(0.15)  # let the request reach the host
    client.close_sync()

    with pytest.raises(NodeHostError):
        await pending
    assert not client.is_running()
    assert client.pid is None

    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil ships with the launcher deps
        return
    assert not psutil.pid_exists(pid), f"node host pid {pid} survived close()"


async def test_start_is_idempotent():
    client = NodeHostClient()
    try:
        await client.start()
        first = client.pid
        await client.start()
        assert client.pid == first
    finally:
        client.close_sync()


# ── plugin wiring ───────────────────────────────────────────────────────────


async def test_plugin_is_inert_without_mods():
    from plugins.mods_host.plugin import ModsHostPlugin

    plugin = ModsHostPlugin(None)  # context is unused before mods are enabled
    ctx = {"tool_name": "read_file", "arguments": {"path": "x"}, "agent_id": "a1"}
    assert await plugin.on_before_tool(dict(ctx)) == ctx
    assert plugin._client is None, "no mod enabled must not start a node process"
    plugin.on_unload()  # must be safe with no host running


async def test_plugin_ping_starts_the_host_and_shuts_it_down():
    from plugins.mods_host.plugin import ModsHostPlugin

    plugin = ModsHostPlugin(None)
    try:
        result = await plugin.ping()
        assert result["pong"] is True
        assert plugin._client is not None
        assert plugin._client.is_running()
    finally:
        plugin.on_unload()
    assert plugin._client is None


async def test_host_log_notification_is_served(caplog):
    """The host's `$ .log` arrives as a `log` notification (no id → no reply)."""
    from plugins.mods_host.plugin import ModsHostPlugin

    plugin = ModsHostPlugin(None)
    with caplog.at_level(logging.INFO, logger="plugins.mods_host"):
        served = await plugin._on_host_request("log", {"mod": "demo", "level": "info", "message": "hi"})
    assert served == {}
    assert "[demo] hi" in caplog.text


async def test_unknown_host_request_is_refused():
    """Python owns the gates — anything not implemented is refused, not ignored.

    (`gate.fs.write` used to stand in here; it is implemented now, so this needs
    a capability the host genuinely never serves.)
    """
    from plugins.mods_host.plugin import ModsHostPlugin

    plugin = ModsHostPlugin(None)
    with pytest.raises(ValueError):
        await plugin._on_host_request("gate.session.authorize", {})
