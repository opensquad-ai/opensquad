"""Tests for the lightweight health-check server (P0-2).

Validates:
1. Health server starts on a free port
2. GET /health returns 200 JSON
3. Other paths return 404
4. Server can be stopped gracefully
5. Loopback probes ignore an ambient HTTP proxy

Note: every request below goes through ``opensquad.utils.local_http.open_local``
rather than plain ``urllib.request.urlopen``. ``urlopen`` resolves proxies from
the environment (and, on Windows, from the WinINET registry) and does *not*
treat a bare ``127.0.0.1`` as a proxy-bypass target on every platform, so a
configured proxy makes loopback probes hang until the socket timeout. Using the
same helper the production health watchdog uses keeps this file deterministic on
developer machines that sit behind a proxy.
"""

import json
import urllib.error

import pytest

from opensquad.health_server import (
    get_health_port,
    start_health_server,
    stop_health_server,
)
from opensquad.utils.local_http import open_local


@pytest.fixture(autouse=True)
def _cleanup_health_server():
    """Ensure health server is stopped after each test."""
    yield
    stop_health_server()


def _get(url: str, timeout: float = 3):
    return open_local(url, timeout=timeout)


def test_health_server_starts_and_returns_port():
    port = start_health_server()
    assert port > 0
    assert get_health_port() == port


def test_health_endpoint_returns_ok():
    port = start_health_server()
    with _get(f"http://127.0.0.1:{port}/health") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert "pid" in data
        assert data["pid"] > 0


def test_unknown_path_returns_404():
    port = start_health_server()
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _get(f"http://127.0.0.1:{port}/unknown")
    assert exc_info.value.code == 404


def test_stop_health_server():
    port = start_health_server()
    assert get_health_port() == port
    stop_health_server()
    assert get_health_port() == 0
    # After stop, requests must fail fast (connection refused) rather than hang.
    with pytest.raises((urllib.error.URLError, TimeoutError)):
        _get(f"http://127.0.0.1:{port}/health", timeout=1)


def test_idempotent_start():
    """Calling start_health_server() twice should return the same port."""
    port1 = start_health_server()
    port2 = start_health_server()
    assert port1 == port2


# ── Proxy-interference regression ──


def test_loopback_probe_bypasses_ambient_http_proxy(monkeypatch):
    """An HTTP_PROXY in the environment must not hijack loopback health probes.

    Regression for a real incident: on a machine with
    ``HTTP_PROXY=http://127.0.0.1:58791`` the launcher's health probe went
    *through the proxy*, so a healthy agent reported unhealthy and the watchdog
    restarted it.
    """
    port = start_health_server()
    # Point the proxy at a discard port: if the request were proxied it would
    # fail instead of reaching the local health server.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("all_proxy", "http://127.0.0.1:1")

    with _get(f"http://127.0.0.1:{port}/health") as resp:
        assert resp.status == 200
        assert json.loads(resp.read().decode("utf-8"))["status"] == "ok"
