"""pair_with_node: the answers a machine gets when it asks to be paired.

Covers the paths the operator will actually hit — an invite without a code, a
refusal, a rejection, and the approval that ends with this machine holding its
own scoped token and a config that points at the host.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import opensquad.input_hub as input_hub_mod  # noqa: E402
from opensquad import node_peers  # noqa: E402
from opensquad.input_hub import input_hub  # noqa: E402
from opensquad.system_config import syscfg  # noqa: E402
from opensquad.tools import invite as invite_tool  # noqa: E402


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = b"{}" if payload is not None else b""

    def json(self):
        return self._payload


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolate the pairing store and give the agent a config to be written."""
    monkeypatch.setattr(syscfg, "workspace_data_dir", lambda *parts: str(tmp_path / "_".join(parts)))
    agent_dir = tmp_path / "agents" / "coder"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.json").write_text(json.dumps({"agent_name": "coder"}), encoding="utf-8")
    monkeypatch.setattr(input_hub, "agent_dir", str(agent_dir))
    monkeypatch.setattr(input_hub_mod.input_hub, "agent_dir", str(agent_dir))
    return {"agent_dir": agent_dir, "tmp": tmp_path}


def _fake_http(monkeypatch, *, request_status=200, poll=None):
    calls: list[str] = []

    def _post(url, **kwargs):
        calls.append(f"POST {url}")
        if url.endswith("/node/pair/request"):
            if request_status != 200:
                return _Response(request_status, {"detail": "Invalid or expired pairing code"})
            return _Response(200, {"ok": True, "status": "pending", "request_id": "pair_1"})
        return _Response(404, {"detail": "unexpected"})

    def _get(url, **kwargs):
        calls.append(f"GET {url}")
        body = poll or {"status": "pending"}
        return _Response(200, body)

    import requests as real_requests

    monkeypatch.setattr(real_requests, "post", _post)
    monkeypatch.setattr(real_requests, "get", _get)
    return calls


def test_an_invite_without_a_code_says_what_is_missing(env, monkeypatch):
    calls = _fake_http(monkeypatch)

    res = invite_tool.pair_with_node("192.168.5.4#g-7f3a")

    assert res["status"] == "error"
    assert res["code"] == "no_pairing_code"
    assert calls == []  # nothing was attempted


def test_a_refused_code_is_reported_with_the_hosts_reason(env, monkeypatch):
    _fake_http(monkeypatch, request_status=403)

    res = invite_tool.pair_with_node("192.168.5.4#g-7f3a?code=000000")

    assert res["status"] == "error"
    assert res["code"] == "pairing_refused"
    assert "Invalid or expired" in res["message"]


def test_a_rejection_is_not_reported_as_success(env, monkeypatch):
    _fake_http(monkeypatch, poll={"status": "rejected"})

    res = invite_tool.pair_with_node("192.168.5.4#g-7f3a?code=123456")

    assert res["status"] == "error"
    assert res["code"] == "pairing_rejected"


def test_still_waiting_says_so_instead_of_failing(env, monkeypatch):
    _fake_http(monkeypatch, poll={"status": "pending"})

    res = invite_tool.pair_with_node("192.168.5.4#g-7f3a?code=123456", wait_seconds=0)

    assert res["status"] == "pending"
    assert res["request_id"] == "pair_1"


def test_approval_records_the_peer_without_taking_the_agent_off_its_own_gateway(env, monkeypatch):
    """The whole point of the fix: pairing a second machine used to repoint the
    agent's only bridge (``group_chat.base_url`` + ``gateway.url``), which silently
    dropped it out of its own groups and forced a switch-back restart. The peer is
    now written NEXT TO home, and nothing about home moves."""
    _fake_http(
        monkeypatch,
        poll={"status": "approved", "token": "peer-tok", "scopes": list(node_peers.AGENT_SCOPES)},
    )

    res = invite_tool.pair_with_node("192.168.5.4:9600#g-7f3a?code=123456", name="machine-b")

    assert res["status"] == "success"
    assert res["config_updated"] is True
    # the token is kept for this gateway, and spent by registration/board calls
    assert node_peers.load_local_peer("http://192.168.5.4:9600")["token"] == "peer-tok"

    cfg = json.loads((env["agent_dir"] / "config.json").read_text(encoding="utf-8"))
    # the peer is remembered…
    assert cfg["group_chat"]["peers"]["192.168.5.4"]["base_url"] == "http://192.168.5.4:9600"
    assert cfg["group_chat"]["peers"]["192.168.5.4"]["token"] == "peer-tok"
    # …and home was not repointed: no gateway/base_url of its own was written
    assert "base_url" not in cfg["group_chat"]
    assert "gateway" not in cfg
    assert "no restart" in res["message"]
