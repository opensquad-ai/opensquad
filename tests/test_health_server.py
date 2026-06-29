# -*- coding: utf-8 -*-
"""Tests for the lightweight health-check server (P0-2).

Validates:
1. Health server starts on a free port
2. GET /health returns 200 JSON
3. Other paths return 404
4. Server can be stopped gracefully
"""
import json
import urllib.request
import pytest

from opensquad.health_server import (
    start_health_server,
    stop_health_server,
    get_health_port,
)


@pytest.fixture(autouse=True)
def _cleanup_health_server():
    """Ensure health server is stopped after each test."""
    yield
    stop_health_server()


def test_health_server_starts_and_returns_port():
    port = start_health_server()
    assert port > 0
    assert get_health_port() == port


def test_health_endpoint_returns_ok():
    port = start_health_server()
    url = f"http://127.0.0.1:{port}/health"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=3) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert "pid" in data
        assert data["pid"] > 0


def test_unknown_path_returns_404():
    port = start_health_server()
    url = f"http://127.0.0.1:{port}/unknown"
    req = urllib.request.Request(url, method="GET")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=3)
    assert exc_info.value.code == 404


def test_stop_health_server():
    port = start_health_server()
    assert get_health_port() == port
    stop_health_server()
    assert get_health_port() == 0
    # After stop, requests should fail (connection refused)
    url = f"http://127.0.0.1:{port}/health"
    req = urllib.request.Request(url, method="GET")
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(req, timeout=1)


def test_idempotent_start():
    """Calling start_health_server() twice should return the same port."""
    port1 = start_health_server()
    port2 = start_health_server()
    assert port1 == port2
