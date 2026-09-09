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


def test_shared_client_accepts_timeout_kwarg(monkeypatch):
    """http_only proxies pass timeout= into _get_shared_client; a mismatch 500s the UI."""

    class _FakeClient:
        pass

    monkeypatch.setattr(admin, "get_local_http_client", lambda: _FakeClient())
    client = admin._get_shared_client(timeout=60.0)
    assert isinstance(client, _FakeClient)


async def test_http_only_proxy_get_reaches_launcher(monkeypatch):
    class FakeResp:
        status_code = 200

        def json(self):
            return {"entries": [{"path": "a.py", "name": "a.py", "type": "file"}]}

    class FakeClient:
        def __init__(self):
            self.urls = []

        async def get(self, url, params=None, timeout=5.0):
            self.urls.append((url, params, timeout))
            return FakeResp()

    fake = FakeClient()
    monkeypatch.setattr(admin, "get_local_http_client", lambda: fake)
    monkeypatch.setattr(admin, "_launcher_url", lambda: "http://127.0.0.1:9600")
    monkeypatch.setattr(admin.launcher_handler, "has_connections", lambda: True)

    result = await admin._proxy_get("/api/agents/a1/fs/tree?max=100", http_only=True, timeout=60.0)
    assert result["entries"][0]["path"] == "a.py"
    assert fake.urls, "http_only must use HTTP even when a WS tunnel exists"
    assert fake.urls[0][2] == 60.0


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
