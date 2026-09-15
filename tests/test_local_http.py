"""Regression tests for ``opensquad.utils.local_http.open_local``.

An ambient ``HTTP_PROXY`` must never intercept loopback traffic.  On Windows
``proxy_bypass("127.0.0.1")`` returns ``False``, so plain
``urllib.request.urlopen`` silently routes a local request through the proxy --
turning a healthy launcher / agent / plugin service into an apparent failure.

The contract these tests pin down:

* ``open_local`` succeeds even when every proxy variable points at a black hole;
* plain ``urlopen`` in the same environment would *not* (proving the tests are
  not vacuous);
* method, body and headers are forwarded unchanged.
"""

from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from opensquad.utils.local_http import open_local

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_NO_PROXY_VARS = ("NO_PROXY", "no_proxy")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        body = b'{"workspace": "/tmp/ws"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        """Silence per-request logging so pytest output stays readable."""


@pytest.fixture
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def hijacked_proxy(monkeypatch):
    """Point every proxy variable at a closed port and remove any bypass list."""
    for var in _PROXY_VARS:
        monkeypatch.setenv(var, "http://127.0.0.1:1")
    for var in _NO_PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    # Some CPython versions cache the environment lookup.
    cache_clear = getattr(urllib.request.getproxies_environment, "cache_clear", None)
    if cache_clear is not None:
        cache_clear()
    yield
    if cache_clear is not None:
        cache_clear()


def _base_url(server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def test_local_request_bypasses_ambient_proxy(local_server, hijacked_proxy):
    """The core regression: a hijacking proxy must not break a loopback probe."""
    with open_local(f"{_base_url(local_server)}/api/workspace", timeout=2.0) as resp:
        assert resp.status == 200
        assert b"workspace" in resp.read()


def test_plain_urlopen_would_have_been_hijacked(local_server, hijacked_proxy):
    """Guard against the premise becoming vacuous on a future platform."""
    if "http" not in urllib.request.getproxies():
        pytest.skip("proxy environment is not visible to urllib on this platform")
    with pytest.raises((urllib.error.URLError, TimeoutError, OSError)):
        urllib.request.urlopen(f"{_base_url(local_server)}/api/workspace", timeout=1.0)


def test_post_forwards_method_body_and_headers(local_server, hijacked_proxy):
    """``opensquad stop`` sends a real POST body -- it must survive untranslated."""
    with open_local(
        f"{_base_url(local_server)}/api/shutdown",
        timeout=2.0,
        method="POST",
        data=b"bye",
        headers={"Content-Type": "text/plain"},
    ) as resp:
        assert resp.status == 200
        assert resp.read() == b"bye"


def test_connection_refused_surfaces_as_error(hijacked_proxy):
    """A genuinely dead port must raise, not silently succeed through a proxy."""
    # Bind-and-release to obtain a port nothing is listening on.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    with pytest.raises((urllib.error.URLError, TimeoutError, OSError)):
        open_local(f"http://127.0.0.1:{dead_port}/api/workspace", timeout=0.5)
