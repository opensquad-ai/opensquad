"""The invite panel names a reachable host: this machine's own LAN address.

A browser cannot read its host's interfaces, and the page is often served from 127.0.0.1 —
which is exactly the invite another machine cannot dial. The backend detects the address
instead, and only offers ones a peer could actually use.
"""

from __future__ import annotations

import asyncio
import socket as real_socket
import sys
from pathlib import Path
from types import SimpleNamespace

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
_BACKEND_DIR = _SRC / "opensquad" / "gateway" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import opensquad.net_addresses as na  # noqa: E402


class _Probe:
    def __init__(self, ip: str):
        self._ip = ip

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def connect(self, addr):
        return None

    def getsockname(self):
        return (self._ip, 54321)


def _patch(monkeypatch, probe_ip: str, host_ips: list[str]):
    monkeypatch.setattr(real_socket, "socket", lambda *a, **k: _Probe(probe_ip))
    monkeypatch.setattr(real_socket, "gethostbyname_ex", lambda host: ("h", [], host_ips))


def test_only_addresses_another_machine_could_use(monkeypatch):
    _patch(
        monkeypatch,
        "192.168.5.2",
        ["127.0.0.1", "169.254.10.5", "192.168.5.2", "10.0.0.7", "224.0.0.1", "0.0.0.0"],
    )

    # loopback, link-local, multicast and unspecified are dropped, duplicates collapse
    assert na.lan_addresses() == ["192.168.5.2", "10.0.0.7"]


def test_the_route_address_leads_over_a_virtual_bridge(monkeypatch):
    """On a host with Docker/VPN, the hostname also lists bridges no peer can dial — the
    interface the default route uses is the one to offer first."""
    _patch(monkeypatch, "192.168.5.2", ["172.17.0.1", "10.8.0.2", "192.168.5.2"])

    assert na.lan_addresses()[0] == "192.168.5.2"


def test_a_public_address_is_still_offered(monkeypatch):
    _patch(monkeypatch, "203.0.113.9", [])

    assert na.lan_addresses() == ["203.0.113.9"]


def test_detection_failure_is_not_an_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("no route")

    monkeypatch.setattr(real_socket, "socket", _boom)
    monkeypatch.setattr(real_socket, "gethostbyname_ex", _boom)

    assert na.lan_addresses() == []


def test_the_endpoint_hands_them_to_the_panel(monkeypatch):
    from app.ai_web.routes import _main as routes

    monkeypatch.setattr("opensquad.net_addresses.lan_addresses", lambda: ["192.168.5.2"])

    res = asyncio.run(routes.node_local_addresses(current_user=SimpleNamespace(id="u1")))

    assert res["ok"] is True
    assert res["addresses"] == ["192.168.5.2"]
    assert res["hostname"] == real_socket.gethostname()


def test_the_endpoint_stays_usable_when_detection_fails(monkeypatch):
    from app.ai_web.routes import _main as routes

    def _boom():
        raise RuntimeError("nope")

    monkeypatch.setattr("opensquad.net_addresses.lan_addresses", _boom)

    res = asyncio.run(routes.node_local_addresses(current_user=SimpleNamespace(id="u1")))

    assert res == {"ok": False, "addresses": [], "hostname": real_socket.gethostname()}
