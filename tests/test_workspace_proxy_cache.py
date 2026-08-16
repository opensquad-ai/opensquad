"""Workspace launcher proxy GET cache (list / detect-legacy)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

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

from app import workspace_api as ws  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    ws._GET_CACHE.clear()
    yield
    ws._GET_CACHE.clear()


async def test_workspace_list_hits_cache(monkeypatch):
    calls = []

    async def fake_get(url, timeout=10.0):
        calls.append(url)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"workspaces": []}
        return resp

    monkeypatch.setattr(ws.syscfg, "launcher_url", lambda: "http://127.0.0.1:9600")
    client = MagicMock()
    client.get = fake_get
    monkeypatch.setattr(ws, "get_local_http_client", lambda: client)

    r1 = await ws._proxy_get("/api/workspace/list")
    r2 = await ws._proxy_get("/api/workspace/list")
    assert r1 == r2 == {"workspaces": []}
    assert len(calls) == 1


async def test_workspace_current_not_cached(monkeypatch):
    calls = []

    async def fake_get(url, timeout=10.0):
        calls.append(url)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"path": "/w"}
        return resp

    monkeypatch.setattr(ws.syscfg, "launcher_url", lambda: "http://127.0.0.1:9600")
    client = MagicMock()
    client.get = fake_get
    monkeypatch.setattr(ws, "get_local_http_client", lambda: client)

    await ws._proxy_get("/api/workspace")
    await ws._proxy_get("/api/workspace")
    assert len(calls) == 2


async def test_workspace_post_clears_list_cache(monkeypatch):
    async def fake_get(url, timeout=10.0):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"workspaces": [1]}
        return resp

    async def fake_post(url, json=None, timeout=10.0):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True}
        return resp

    monkeypatch.setattr(ws.syscfg, "launcher_url", lambda: "http://127.0.0.1:9600")
    client = MagicMock()
    client.get = fake_get
    client.post = fake_post
    monkeypatch.setattr(ws, "get_local_http_client", lambda: client)

    await ws._proxy_get("/api/workspace/list")
    assert ws._GET_CACHE
    await ws._proxy_post("/api/workspace/switch", {"path": "/x"})
    assert not ws._GET_CACHE
