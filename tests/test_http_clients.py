"""Shared gateway httpx clients + JSON fan-out helpers."""

from __future__ import annotations

import os
from datetime import datetime, timezone

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

from app.http_clients import (  # noqa: E402
    close_shared_http_clients,
    get_local_http_client,
    get_tls_http_client,
)
from app.json_utils import dumps_json_safe  # noqa: E402
from app.websocket import ConnectionManager  # noqa: E402


@pytest.fixture(autouse=True)
async def _close_clients():
    yield
    await close_shared_http_clients()


async def test_local_and_tls_clients_are_reused():
    a = get_local_http_client()
    b = get_local_http_client()
    assert a is b
    c = get_tls_http_client()
    d = get_tls_http_client()
    assert c is d
    assert a is not c


async def test_dumps_json_safe_datetime():
    ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    text = dumps_json_safe({"t": ts, "n": 1})
    assert '"n": 1' in text
    assert "2026" not in text  # converted to ms timestamp, not iso string


class _DummyWS:
    def __init__(self):
        self.texts: list[str] = []

    async def send_text(self, payload: str):
        self.texts.append(payload)


async def test_broadcast_serializes_once():
    mgr = ConnectionManager()
    w1, w2 = _DummyWS(), _DummyWS()
    mgr.active_connections["u1"] = [w1]
    mgr.active_connections["u2"] = [w2]
    mgr.group_subscriptions["g1"] = {"u1", "u2"}
    msg = {"type": "x", "data": {"n": 1}}
    await mgr.broadcast_to_group("g1", msg)
    assert w1.texts == w2.texts == [dumps_json_safe(msg)]
