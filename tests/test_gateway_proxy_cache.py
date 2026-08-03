"""Gateway proxy TTL cache tests (P1-1 web latency fix).

_readonly launcher endpoints (runtime/list, workspace/list, agent fs/*) are
cached at the gateway for 5s so polling / re-opening them does not fan out to
launcher/agent on every click.
"""

from __future__ import annotations

import os

import pytest

# Direct source import (bypasses the eager __init__ chain like
# test_gateway_session.py). The gateway backend package needs its own root on
# sys.path for ``app.*`` imports.
_BACKEND_ROOT = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "opensquad",
        "gateway",
        "backend",
    )
)
if _BACKEND_ROOT not in os.sys.path:
    os.sys.path.insert(0, _BACKEND_ROOT)

from app.ai_web.routes import _admin as admin  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    admin._PROXY_GET_CACHE.clear()
    yield
    admin._PROXY_GET_CACHE.clear()


async def test_cacheable_endpoint_hits_cache(monkeypatch):
    calls = []

    async def fake_rpc(node_id, method, path, timeout=20.0):
        calls.append(path)
        return {"ok": True}

    monkeypatch.setattr(admin.launcher_handler, "has_connections", lambda: True)
    monkeypatch.setattr(admin.launcher_handler, "get_any_node_id", lambda: "node1")
    monkeypatch.setattr(admin.launcher_handler, "rpc", fake_rpc)

    r1 = await admin._proxy_get("/api/runtime/list")
    r2 = await admin._proxy_get("/api/runtime/list")
    assert r1 == {"ok": True}
    assert r2 == {"ok": True}
    assert len(calls) == 1  # second call served from gateway cache


async def test_cache_key_includes_params(monkeypatch):
    calls = []

    async def fake_rpc(node_id, method, path, timeout=20.0):
        calls.append(path)
        return {"path": path}

    monkeypatch.setattr(admin.launcher_handler, "has_connections", lambda: True)
    monkeypatch.setattr(admin.launcher_handler, "get_any_node_id", lambda: "node1")
    monkeypatch.setattr(admin.launcher_handler, "rpc", fake_rpc)

    await admin._proxy_get("/api/agents/a1/fs/tree", {"root": "C:/x", "max": "100"})
    await admin._proxy_get("/api/agents/a1/fs/tree", {"root": "C:/y", "max": "100"})
    assert len(calls) == 2  # different query -> different cache key


async def test_non_cacheable_endpoint_not_cached(monkeypatch):
    calls = []

    async def fake_rpc(node_id, method, path, timeout=20.0):
        calls.append(path)
        return {"ok": True}

    monkeypatch.setattr(admin.launcher_handler, "has_connections", lambda: True)
    monkeypatch.setattr(admin.launcher_handler, "get_any_node_id", lambda: "node1")
    monkeypatch.setattr(admin.launcher_handler, "rpc", fake_rpc)

    await admin._proxy_get("/api/sessions/x/list")
    await admin._proxy_get("/api/sessions/x/list")
    assert len(calls) == 2  # live session data must never be cached


async def test_ttl_expiry_revalidates(monkeypatch):
    calls = []

    async def fake_rpc(node_id, method, path, timeout=20.0):
        calls.append(path)
        return {"ok": True}

    monkeypatch.setattr(admin.launcher_handler, "has_connections", lambda: True)
    monkeypatch.setattr(admin.launcher_handler, "get_any_node_id", lambda: "node1")
    monkeypatch.setattr(admin.launcher_handler, "rpc", fake_rpc)

    await admin._proxy_get("/api/runtime/list")
    # Force expiry
    for key in list(admin._PROXY_GET_CACHE):
        exp, _ = admin._PROXY_GET_CACHE[key]
        admin._PROXY_GET_CACHE[key] = (exp - 100, admin._PROXY_GET_CACHE[key][1])
    await admin._proxy_get("/api/runtime/list")
    assert len(calls) == 2
