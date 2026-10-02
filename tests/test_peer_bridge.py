"""Peers: bridges to other machines that never take over the agent's own gateway.

Pairing used to repoint the agent's only bridge at the machine it paired with, so
joining elsewhere meant leaving home. These cover the replacement: peers live in
``group_chat.peers``, a call aimed at one gets its own bridge, and registration
there uses the peer token instead of the host's node_secret.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import opensquad.bridge as bridge_mod  # noqa: E402
import opensquad.input_hub as input_hub_mod  # noqa: E402
import opensquad.peer_bridge as peer_bridge_mod  # noqa: E402
from opensquad.input_hub import input_hub  # noqa: E402
from opensquad.tools import im as im_tool  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """An agent that already has its own gateway and one group at home."""
    agent_dir = tmp_path / "agents" / "coder"
    agent_dir.mkdir(parents=True)
    config = {
        "agent_name": "coder",
        "group_chat": {
            "base_url": "http://127.0.0.1:9555",
            "email": "pm@ai",
            "password": "home-pw",
            "groups": ["g-home"],
        },
    }
    (agent_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(input_hub, "agent_dir", str(agent_dir))
    monkeypatch.setattr(input_hub_mod.input_hub, "agent_dir", str(agent_dir))
    peer_bridge_mod._BRIDGES.clear()
    return agent_dir


def _config(agent_dir) -> dict:
    return json.loads((agent_dir / "config.json").read_text(encoding="utf-8"))


def test_a_stale_twin_entry_cannot_shadow_the_fresh_token(env):
    """Regression: an old entry keyed by ``host:port`` could sit next to the new one
    keyed by ``host``, and find_peer returned the first match — so subscribe used the
    old token and the peer answered 401."""
    cfg = _config(env)
    cfg["group_chat"]["peers"] = {
        "192.168.5.4:9555": {
            "host": "192.168.5.4:9555",
            "base_url": "http://192.168.5.4:9555",
            "token": "old-token",
            "paired_at": 100.0,
        },
        "192.168.5.4": {
            "host": "192.168.5.4",
            "base_url": "http://192.168.5.4:9555",
            "token": "fresh-token",
            "paired_at": 200.0,
        },
    }
    (env / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    assert peer_bridge_mod.find_peer("192.168.5.4")["token"] == "fresh-token"


def test_remembering_a_peer_collapses_the_twin(env):
    """One machine, one entry: re-pairing must not leave a stale twin behind."""
    cfg = _config(env)
    cfg["group_chat"]["peers"] = {
        "192.168.5.4:9555": {
            "host": "192.168.5.4:9555",
            "base_url": "http://192.168.5.4:9555",
            "token": "old-token",
            "groups": ["g-kept"],
        },
    }
    (env / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    peer_bridge_mod.remember_peer("192.168.5.4", "http://192.168.5.4:9555", "fresh-token", "machine-b")

    peers = _config(env)["group_chat"]["peers"]
    assert list(peers) == ["192.168.5.4"]
    assert peers["192.168.5.4"]["token"] == "fresh-token"
    assert peers["192.168.5.4"]["groups"] == ["g-kept"]  # merged, not dropped


def test_remembering_a_peer_leaves_the_home_binding_exactly_as_it_was(env):
    peer_bridge_mod.remember_peer("192.168.5.4", "http://192.168.5.4:9555", "peer-tok", "machine-b")

    cfg = _config(env)
    assert cfg["group_chat"]["base_url"] == "http://127.0.0.1:9555"
    assert cfg["group_chat"]["email"] == "pm@ai"
    assert cfg["group_chat"]["groups"] == ["g-home"]
    assert "gateway" not in cfg
    assert cfg["group_chat"]["peers"]["192.168.5.4"]["base_url"] == "http://192.168.5.4:9555"


def test_a_peer_is_found_by_host_or_by_gateway_address(env):
    peer_bridge_mod.remember_peer("192.168.5.4", "http://192.168.5.4:9555", "peer-tok")

    assert peer_bridge_mod.find_peer("192.168.5.4")["token"] == "peer-tok"
    assert peer_bridge_mod.find_peer("http://192.168.5.4:9555")["token"] == "peer-tok"
    assert peer_bridge_mod.find_peer("10.0.0.9") is None


def test_a_peer_without_an_account_says_what_to_run(env):
    peer_bridge_mod.remember_peer("192.168.5.4", "http://192.168.5.4:9555", "peer-tok")

    bridge, why = peer_bridge_mod.peer_bridge("192.168.5.4")

    assert bridge is None
    assert "register_account" in why and "host=" in why


def test_a_group_joined_on_a_peer_is_remembered_against_it(env):
    peer_bridge_mod.remember_peer("192.168.5.4", "http://192.168.5.4:9555", "peer-tok")

    assert peer_bridge_mod.remember_peer_group("192.168.5.4", "g-7f3a") is True

    entry = peer_bridge_mod.peer_for_group("g-7f3a")
    assert entry is not None
    assert entry["host"] == "192.168.5.4"
    assert peer_bridge_mod.peer_for_group("g-home") is None  # a local group has no owner
    # recorded next to the peer, home binding untouched
    assert _config(env)["group_chat"]["base_url"] == "http://127.0.0.1:9555"


def test_remembering_a_peer_group_is_idempotent(env):
    peer_bridge_mod.remember_peer("192.168.5.4", "http://192.168.5.4:9555", "peer-tok")

    peer_bridge_mod.remember_peer_group("192.168.5.4", "g-7f3a")
    peer_bridge_mod.remember_peer_group("192.168.5.4", "g-7f3a")

    assert _config(env)["group_chat"]["peers"]["192.168.5.4"]["groups"] == ["g-7f3a"]


def test_remembering_a_group_for_an_unpaired_host_changes_nothing(env):
    assert peer_bridge_mod.remember_peer_group("10.0.0.9", "g-7f3a") is False
    assert peer_bridge_mod.peer_for_group("g-7f3a") is None


def test_an_unpaired_host_says_to_pair_first(env):
    bridge, why = peer_bridge_mod.peer_bridge("10.1.2.3")

    assert bridge is None
    assert "pair_with_node" in why


def test_a_peer_bridge_logs_in_and_is_reused(env, monkeypatch):
    peer_bridge_mod.remember_peer(
        "192.168.5.4",
        "http://192.168.5.4:9555",
        "peer-tok",
        account={"email": "b@ai", "password": "pw"},
    )
    built: list[str] = []

    class _FakeBridge:
        def __init__(self, base_url=None, email="", password="", **kwargs):
            built.append(f"{base_url}|{email}")
            self.base_url = base_url
            self.token = "user-tok"

        def login(self):
            return True

    monkeypatch.setattr(bridge_mod, "ChatProBridge", _FakeBridge)

    first, why = peer_bridge_mod.peer_bridge("192.168.5.4")
    second, _ = peer_bridge_mod.peer_bridge("192.168.5.4")

    assert why == ""
    assert first is second  # one bridge per peer
    assert built == ["http://192.168.5.4:9555|b@ai"]


def test_registering_on_a_peer_uses_the_peer_token_and_keeps_home_credentials(env, monkeypatch):
    peer_bridge_mod.remember_peer("192.168.5.4", "http://192.168.5.4:9555", "peer-tok")
    seen: list[dict] = []

    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {"user": {"id": "u1"}}

    def _post(url, **kwargs):
        seen.append({"url": url, "headers": kwargs.get("headers") or {}, "json": kwargs.get("json") or {}})
        return _Response()

    import requests as real_requests

    monkeypatch.setattr(real_requests, "post", _post)

    res = im_tool.register_account("machine-b-agent@ai", "pw", host="192.168.5.4")

    assert res["status"] == "success"
    assert seen[0]["url"] == "http://192.168.5.4:9555/api/auth/register"
    assert seen[0]["headers"]["X-Node-Token"] == "peer-tok"  # never the host's node_secret
    cfg = _config(env)
    assert cfg["group_chat"]["email"] == "pm@ai"  # home account untouched
    assert cfg["group_chat"]["peers"]["192.168.5.4"]["account"] == {
        "email": "machine-b-agent@ai",
        "password": "pw",
    }


def test_an_existing_peer_account_with_a_wrong_password_is_reported_not_taken_over(env, monkeypatch):
    """A peer cannot reset passwords on that machine, so silently adopting the
    address would lock the other agent out."""
    peer_bridge_mod.remember_peer("192.168.5.4", "http://192.168.5.4:9555", "peer-tok")

    class _Response:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self.text = ""
            self._payload = payload or {}

        def json(self):
            return self._payload

    import requests as real_requests

    def _post(url, **kwargs):
        if url.endswith("/api/auth/register"):
            return _Response(400, {"detail": "Email already registered"})
        return _Response(401, {"detail": "Invalid credentials"})

    monkeypatch.setattr(real_requests, "post", _post)

    res = im_tool.register_account("clash@ai", "pw", host="192.168.5.4")

    assert res["status"] == "error"
    assert res["code"] == "email_in_use"
    assert "cannot reset passwords" in res["message"]


def _peer_with_account(monkeypatch, *, sent: list):
    peer_bridge_mod.remember_peer(
        "192.168.5.4",
        "http://192.168.5.4:9555",
        "peer-tok",
        account={"email": "b@ai", "password": "pw"},
    )

    class _FakeBridge:
        base_url = "http://192.168.5.4:9555"
        token = "user-tok"
        _group_cache = {"g-7f3a": True}

        def __init__(self, **kwargs):
            pass

        def login(self):
            return True

        def list_groups_api(self):
            return [{"id": "g-7f3a", "name": "远端群"}]

        def send_message(self, content, target_id, target_type="group", file_paths=None, **kwargs):
            sent.append({"content": content, "target_id": target_id, "target_type": target_type})
            return True

    monkeypatch.setattr(bridge_mod, "ChatProBridge", _FakeBridge)


def test_sending_to_a_peer_host_uses_that_peers_bridge(env, monkeypatch):
    sent: list = []
    _peer_with_account(monkeypatch, sent=sent)

    res = im_tool.send_message("你好", target_id="g-7f3a", host="192.168.5.4")

    assert res["status"] == "success"
    assert sent == [{"content": "你好", "target_id": "g-7f3a", "target_type": "group"}]


def test_listing_groups_on_a_peer_host_reads_that_machine(env, monkeypatch):
    sent: list = []
    _peer_with_account(monkeypatch, sent=sent)

    res = im_tool.list_groups(host="192.168.5.4")

    assert res["status"] == "success"
    assert res["groups"][0]["id"] == "g-7f3a"


def test_a_send_to_an_unpaired_host_says_to_pair_first(env):
    res = im_tool.send_message("你好", target_id="g-7f3a", host="10.1.2.3")

    assert res["status"] == "error"
    assert res["code"] == "peer_not_ready"
    assert "pair_with_node" in res["message"]


def test_a_send_without_a_host_still_uses_the_home_bridge(env, monkeypatch):
    sent: list = []

    class _HomeBridge:
        base_url = "http://127.0.0.1:9555"
        token = "home-tok"
        _group_cache = {"g-home": True}

        def send_message(self, content, target_id, target_type="group", file_paths=None, **kwargs):
            sent.append(target_id)
            return True

    monkeypatch.setattr(bridge_mod, "bridge", _HomeBridge())

    res = im_tool.send_message("hi", target_id="g-home")

    assert res["status"] == "success"
    assert sent == ["g-home"]


# ── owner_bridge: which gateway a group's chat/notifications belong to ──────


def test_owner_bridge_is_home_for_a_group_with_no_peer_owner(env, monkeypatch):
    import opensquad.collab_board as cb

    class _Home:
        token = "home-tok"

    home = _Home()
    monkeypatch.setattr(bridge_mod, "bridge", home)
    monkeypatch.setattr(cb, "board_owner", lambda **kwargs: "")

    got, why = peer_bridge_mod.owner_bridge(group_id="g-home")

    assert got is home
    assert why == ""


def test_owner_bridge_returns_the_peer_for_a_group_joined_there(env, monkeypatch):
    import opensquad.collab_board as cb

    peer = object()
    monkeypatch.setattr(cb, "board_owner", lambda **kwargs: "192.168.5.4")
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: (peer, ""))

    got, why = peer_bridge_mod.owner_bridge(group_id="g-7f3a")

    assert got is peer
    assert why == ""


def test_owner_bridge_reports_why_a_peer_bridge_is_unusable(env, monkeypatch):
    import opensquad.collab_board as cb

    monkeypatch.setattr(cb, "board_owner", lambda **kwargs: "192.168.5.4")
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: (None, "not paired"))

    got, why = peer_bridge_mod.owner_bridge(collab_id="AB12CD")

    assert got is None
    assert why == "not paired"


def test_owner_bridge_is_home_for_a_task_with_no_recorded_owner(env, monkeypatch):
    """A task not recorded as a peer's stays on this machine's bridge."""
    import opensquad.collab_board as cb

    class _Home:
        token = "home-tok"

    home = _Home()
    monkeypatch.setattr(bridge_mod, "bridge", home)
    monkeypatch.setattr(cb, "board_owner", lambda **kwargs: "")

    got, why = peer_bridge_mod.owner_bridge(collab_id="LOCAL1")

    assert got is home
    assert why == ""


# ── the remaining im methods take host= too ────────────────────────────────


def test_joining_a_group_on_a_peer_records_its_owner(env, monkeypatch):
    peer_bridge_mod.remember_peer(
        "192.168.5.4",
        "http://192.168.5.4:9555",
        "peer-tok",
        account={"email": "b@ai", "password": "pw"},
    )
    joined: list = []

    class _FakeBridge:
        base_url = "http://192.168.5.4:9555"
        token = "user-tok"
        user_id = "u-1"

        def __init__(self, **kwargs):
            pass

        def login(self):
            return True

        def join_group_api(self, group_id):
            joined.append(group_id)
            return {"ok": True}

    monkeypatch.setattr(bridge_mod, "ChatProBridge", _FakeBridge)
    monkeypatch.setattr(peer_bridge_mod, "subscribe_group", lambda host, gid: {"ok": True})

    res = im_tool.join_group("g-7f3a", host="192.168.5.4")

    assert res["status"] == "success"
    assert joined == ["g-7f3a"]
    assert peer_bridge_mod.peer_for_group("g-7f3a")["host"] == "192.168.5.4"
    assert res["relay"] == "subscribed"


def test_joining_a_group_on_a_peer_subscribes_for_its_messages(env, monkeypatch):
    """The peer owns the group; it must be told where this agent's socket lives."""
    peer_bridge_mod.remember_peer(
        "192.168.5.4",
        "http://192.168.5.4:9555",
        "peer-tok",
        account={"email": "b@ai", "password": "pw"},
    )
    posted: list = []

    class _Resp:
        status_code = 200

    class _FakeRequests:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            posted.append({"url": url, "headers": headers, "json": json})
            return _Resp()

    class _HomeBridge:
        base_url = "http://127.0.0.1:9555"
        token = "home-tok"
        user_id = "u-home"

    monkeypatch.setattr(bridge_mod, "bridge", _HomeBridge())
    monkeypatch.setitem(sys.modules, "requests", _FakeRequests)
    import opensquad.relay_link as relay_link

    monkeypatch.setattr(relay_link, "store_dir", lambda: str(env / "relay"))

    res = peer_bridge_mod.subscribe_group("192.168.5.4", "g-7f3a")

    assert res["ok"] is True
    assert posted[0]["url"] == "http://192.168.5.4:9555/api/relay/subscribe"
    assert posted[0]["headers"]["X-Node-Token"] == "peer-tok"
    assert posted[0]["json"]["callback_url"] == "http://127.0.0.1:9555"
    assert posted[0]["json"]["user_id"] == "u-home"
    assert posted[0]["json"]["secret"]
    assert relay_link.verify_inbound("g-7f3a", posted[0]["json"]["secret"])
    # The secret is bound to this agent's own user, so the peer cannot aim a push
    # at another user on this gateway.
    assert relay_link.verify_inbound_user("g-7f3a", posted[0]["json"]["secret"]) == "u-home"


def test_subscribing_to_an_unpaired_host_says_to_pair_first(env):
    res = peer_bridge_mod.subscribe_group("10.0.0.9", "g-7f3a")

    assert res["ok"] is False
    assert "Not paired" in res["error"]


def test_reading_history_on_a_peer_host_uses_that_machine(env, monkeypatch):
    peer_bridge_mod.remember_peer(
        "192.168.5.4",
        "http://192.168.5.4:9555",
        "peer-tok",
        account={"email": "b@ai", "password": "pw"},
    )

    class _FakeBridge:
        base_url = "http://192.168.5.4:9555"
        token = "user-tok"

        def __init__(self, **kwargs):
            pass

        def login(self):
            return True

        def get_group_history(self, group_id, limit):
            return [{"sender_id": "u1", "content": "hello", "timestamp": 1}]

    monkeypatch.setattr(bridge_mod, "ChatProBridge", _FakeBridge)

    res = im_tool.get_history("g-7f3a", limit=5, host="192.168.5.4")

    assert res["status"] == "success"
    assert res["history"][0]["content"] == "hello"
