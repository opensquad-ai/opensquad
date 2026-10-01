"""A paired machine authenticates with its own token, not with node_secret.

Pairing is only useful if the calls a paired machine actually makes carry the
token: the collaboration board (the cross-machine surface) and agent registration.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from opensquad import collab_board as cb  # noqa: E402
from opensquad import node_peers  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture()
def captured(monkeypatch):
    seen: list[urllib.request.Request] = []

    def _urlopen(request, timeout=None):
        seen.append(request)
        return _FakeResponse({"ok": True, "result": {"from": "gateway"}})

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return seen


def _board_base(monkeypatch, base="http://192.168.5.4:9555"):
    monkeypatch.setattr(cb, "board_base_url", lambda: base)
    return base


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Isolate this machine's pairing store so save/load are the real thing."""
    from opensquad.system_config import syscfg

    monkeypatch.setattr(syscfg, "workspace_data_dir", lambda *parts: str(tmp_path / "_".join(parts)))
    return node_peers


def test_a_paired_machine_sends_its_token(monkeypatch, captured, store):
    base = _board_base(monkeypatch)
    store.save_local_peer(base, "peer-token-123", "machine-b")

    assert cb.list_tasks() == {"from": "gateway"}

    headers = {k.lower(): v for k, v in captured[0].headers.items()}
    assert headers.get("x-node-token") == "peer-token-123"


def test_without_a_pairing_the_old_path_is_used(monkeypatch, captured, store):
    _board_base(monkeypatch)
    monkeypatch.setattr("opensquad.system_config.syscfg.node_secret", lambda: "local-secret", raising=False)

    cb.list_tasks()

    headers = {k.lower(): v for k, v in captured[0].headers.items()}
    assert "x-node-token" not in headers
    assert headers.get("x-node-secret") == "local-secret"


def test_registration_also_uses_the_peer_token():
    """Source lock: the auto-register call must offer the token as well."""
    bridge_source = (_BACKEND / "opensquad" / "bridge.py").read_text(encoding="utf-8")
    assert "X-Node-Token" in bridge_source
    assert "load_local_peer(self.base_url)" in bridge_source
