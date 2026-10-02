"""The relay's HTTP surface: subscribe on the owner, deliver on the home gateway.

Subscribe is authenticated with the scoped peer token (no node_secret crosses the
link); deliver is authenticated with the per-subscription secret the home gateway
minted. Delivery reaches the subscribing *user* directly, because the group itself
lives on the other machine.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.relay_link as rl  # noqa: E402
from opensquad.gateway.backend.app import relay as gw_relay  # noqa: E402
from opensquad.gateway.backend.app import relay_api  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "store_dir", lambda: str(tmp_path / "relay"))
    rl.reset_seen()
    return tmp_path / "relay"


def _request(headers: dict | None = None):
    return SimpleNamespace(headers=headers or {})


def _peer_ok(monkeypatch, scopes=("agent:register", "group:join", "board:read", "board:write")):
    import opensquad.node_peers as np

    monkeypatch.setattr(np, "verify", lambda token: {"id": "peer_1", "name": "machine-b", "scopes": tuple(scopes)})


# ── subscribe (owner side) ──────────────────────────────────────────────────


def test_a_paired_token_can_subscribe(store, monkeypatch):
    _peer_ok(monkeypatch)

    res = asyncio.run(
        relay_api.relay_subscribe(
            _request({"X-Node-Token": "good"}),
            body={"group_id": "g-7f3a", "callback_url": "http://home-a:9555", "secret": "s1", "user_id": "u-1"},
        )
    )

    assert res["ok"] is True
    assert rl.subscribers("g-7f3a")[0]["user_id"] == "u-1"


def test_subscribing_without_a_token_is_refused(store, monkeypatch):
    import opensquad.node_peers as np

    monkeypatch.setattr(np, "verify", lambda token: None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            relay_api.relay_subscribe(
                _request({}),
                body={"group_id": "g-7f3a", "callback_url": "http://home-a:9555", "secret": "s1"},
            )
        )

    assert exc.value.status_code == 401
    assert rl.subscribers("g-7f3a") == []


def test_a_token_without_group_scope_cannot_subscribe(store, monkeypatch):
    _peer_ok(monkeypatch, scopes=("agent:register",))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            relay_api.relay_subscribe(
                _request({"X-Node-Token": "weak"}),
                body={"group_id": "g-7f3a", "callback_url": "http://home-a:9555", "secret": "s1"},
            )
        )

    assert exc.value.status_code == 401


def test_subscribing_needs_a_group_and_a_callback(store, monkeypatch):
    _peer_ok(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(relay_api.relay_subscribe(_request({"X-Node-Token": "good"}), body={"group_id": "g-7f3a"}))

    assert exc.value.status_code == 400


def test_unsubscribing_removes_the_subscription(store, monkeypatch):
    _peer_ok(monkeypatch)
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")

    res = asyncio.run(
        relay_api.relay_unsubscribe(
            _request({"X-Node-Token": "good"}),
            body={"group_id": "g-7f3a", "callback_url": "http://home-a:9555", "user_id": "u-1"},
        )
    )

    assert res["removed"] == 1
    assert rl.subscribers("g-7f3a") == []


# ── deliver (home side) ─────────────────────────────────────────────────────


class _Manager:
    def __init__(self):
        self.personal: list[tuple[str, dict]] = []
        self.broadcast: list[tuple[str, dict]] = []
        self.active_connections = {"u-1": [object()]}

    async def send_personal_message(self, user_id, message):
        self.personal.append((user_id, message))

    async def broadcast_to_group(self, group_id, message, exclude_user=None):
        self.broadcast.append((group_id, message))


def _deliver(monkeypatch, *, group_id="g-7f3a", secret, body=None, manager=None):
    mgr = manager or _Manager()
    monkeypatch.setattr(gw_relay, "subscribers", gw_relay.subscribers)
    import app.websocket as gw_ws

    monkeypatch.setattr(gw_ws, "manager", mgr)
    res = asyncio.run(relay_api.relay_deliver(_request({"X-Relay-Secret": secret}), body=body or {}))
    return res, mgr


def test_a_relayed_message_reaches_the_subscribing_user(store, monkeypatch):
    secret = rl.new_secret()
    rl.remember_outbound("g-7f3a", "machine-b", secret)
    envelope = rl.build_envelope("g-7f3a", {"id": "m_1", "content": "你好"}, "machine-b", user_id="u-1")

    res, mgr = _deliver(monkeypatch, secret=secret, body=envelope)

    assert res["delivered"] is True
    assert mgr.personal and mgr.personal[0][0] == "u-1"
    assert mgr.personal[0][1]["data"]["content"] == "你好"
    assert mgr.personal[0][1]["relayed"] is True


def test_delivery_with_an_unknown_secret_is_refused(store, monkeypatch):
    envelope = rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-b", user_id="u-1")

    with pytest.raises(HTTPException) as exc:
        _deliver(monkeypatch, secret="forged", body=envelope)

    assert exc.value.status_code == 401


def test_a_message_already_relayed_is_not_delivered_twice(store, monkeypatch):
    secret = rl.new_secret()
    rl.remember_outbound("g-7f3a", "machine-b", secret)
    envelope = rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-b", user_id="u-1")

    first, mgr1 = _deliver(monkeypatch, secret=secret, body=envelope)
    second, mgr2 = _deliver(monkeypatch, secret=secret, body=envelope)

    assert first["delivered"] is True
    assert second["delivered"] is False and second["reason"] == "duplicate"
    assert mgr1.personal and not mgr2.personal


def test_a_message_beyond_the_hop_limit_is_not_forwarded(store, monkeypatch):
    secret = rl.new_secret()
    rl.remember_outbound("g-7f3a", "machine-b", secret)
    envelope = rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-b", user_id="u-1", hops=2)

    res, mgr = _deliver(monkeypatch, secret=secret, body=envelope)

    assert res["delivered"] is False and res["reason"] == "hop_limit"
    assert not mgr.personal and not mgr.broadcast


def test_delivery_without_a_target_user_falls_back_to_the_group(store, monkeypatch):
    secret = rl.new_secret()
    rl.remember_outbound("g-7f3a", "machine-b", secret)
    envelope = rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-b", user_id="")

    res, mgr = _deliver(monkeypatch, secret=secret, body=envelope)

    assert res["delivered"] is True
    assert mgr.broadcast and mgr.broadcast[0][0] == "g-7f3a"


# ── fan-out (owner side) ────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _Client:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.posts: list[dict] = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return _Resp(self.status_code)


def test_fan_out_posts_the_envelope_to_each_subscriber(store, monkeypatch):
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    client = _Client()
    monkeypatch.setattr(gw_relay, "get_local_http_client", lambda: client, raising=False)
    import app.http_clients as hc

    monkeypatch.setattr(hc, "get_local_http_client", lambda: client)

    res = asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1", "content": "hi"}, origin_host="machine-b"))

    assert res["delivered"] == 1 and res["failed"] == 0
    assert client.posts[0]["url"] == "http://home-a:9555/api/relay/deliver"
    assert client.posts[0]["json"]["target_user_id"] == "u-1"
    assert client.posts[0]["headers"]["X-Relay-Secret"] == "s1"


def test_fan_out_to_nobody_is_a_no_op(store, monkeypatch):
    res = asyncio.run(gw_relay.fan_out("g-nobody", {"id": "m_1"}))

    assert res == {"ok": True, "delivered": 0, "failed": 0}


def test_fan_out_reports_a_failure_instead_of_raising(store, monkeypatch):
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    client = _Client(status_code=500)
    import app.http_clients as hc

    monkeypatch.setattr(hc, "get_local_http_client", lambda: client)

    res = asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1"}, origin_host="machine-b"))

    assert res["ok"] is False and res["failed"] == 1


def test_fan_out_does_not_send_the_same_message_twice(store, monkeypatch):
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    client = _Client()
    import app.http_clients as hc

    monkeypatch.setattr(hc, "get_local_http_client", lambda: client)

    asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1"}, origin_host="machine-b"))
    again = asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1"}, origin_host="machine-b"))

    assert again.get("skipped") == "duplicate"
    assert len(client.posts) == 1


class _SlowClient:
    """A peer that never answers: this is what used to stall the sender."""

    def __init__(self):
        self.posts = 0

    async def post(self, url, json=None, headers=None, timeout=None):
        self.posts += 1
        await asyncio.sleep(5)
        return _Resp(200)


def test_fan_out_is_bounded_by_its_budget(store, monkeypatch):
    """Regression: a dead peer used to add its whole timeout to the sender's
    request, one subscriber at a time. The fan-out now runs concurrently under one
    budget, so the request cannot be held longer than that."""
    for user in ("u-1", "u-2", "u-3"):
        rl.subscribe("g-7f3a", "http://home-a:9555", f"s-{user}", user_id=user)
    client = _SlowClient()
    import app.http_clients as hc

    monkeypatch.setattr(hc, "get_local_http_client", lambda: client)

    import time

    started = time.monotonic()
    res = asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1"}, origin_host="machine-b", budget=0.1))
    elapsed = time.monotonic() - started

    assert res["ok"] is False
    assert res["failed"] == 3
    assert client.posts == 3  # all three were attempted together, not in sequence
    assert elapsed < 3.0  # far below 3 × 5s (the old serial worst case)


def test_the_agent_push_route_uses_the_canonical_broadcast(store):
    """Regression: `POST /agent-push/group` (the server/agent-side push, and the
    file-push path behind it) broadcast a {type: new_message, message: ...} frame by
    itself. Both the web UI (App.tsx) and the agent bridge read `data`, so the frame
    reached no live subscriber, and the relay — which lives in notify_new_message —
    never ran, so a paired machine's agent never saw it either."""
    route = (_BACKEND_DIR / "app" / "ai_web" / "routes" / "_main.py").read_text(encoding="utf-8")
    at = route.index("async def agent_push_to_group")
    body = route[at : at + 6000]

    assert "await notify_new_message(group_id, response_data" in body
    assert '"message": response_data' not in body
    # the identity every consumer keys on (the UI's message list, the bridge's dedupe)
    assert '"id": msg_id' in body


def test_fan_out_task_targets_the_agent_on_the_other_machine(store, monkeypatch):
    """A task-window event must reach the agent's control channel, not its group chat
    socket — so the envelope names it, and the subscriber's agent id is what is used."""
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1", agent_id="pm")
    client = _Client()
    import app.http_clients as hc

    monkeypatch.setattr(hc, "get_local_http_client", lambda: client)

    res = asyncio.run(
        gw_relay.fan_out_task("g-7f3a", {"event_id": "e1", "type": "chat", "content": "hi"}, origin_host="machine-b")
    )

    assert res["delivered"] == 1
    assert client.posts[0]["json"]["type"] == "task:relay"
    assert client.posts[0]["json"]["target_agent_id"] == "pm"
    assert client.posts[0]["json"]["data"]["event_id"] == "e1"


def test_an_undelivered_push_is_reported_as_a_failure(store, monkeypatch):
    """The home gateway answers 200 with delivered:false when nobody was handed the
    frame (agent offline). Counting that as a success hides a silent black hole."""
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")

    class _Client:
        async def post(self, url, json=None, headers=None, timeout=None):
            return _Body({"ok": True, "delivered": False})

    import app.http_clients as hc

    monkeypatch.setattr(hc, "get_local_http_client", lambda: _Client())

    res = asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1"}, origin_host="machine-b"))

    assert res["ok"] is False and res["failed"] == 1


class _Body:
    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def json(self):
        return self._body


def test_a_relayed_task_event_goes_to_the_agent_control_channel(store, monkeypatch):
    secret = rl.new_secret()
    rl.remember_outbound("g-7f3a", "machine-b", secret, user_id="u-1")
    sent: list[tuple] = []

    import app.ai_web.registry as registry_mod

    async def _send(agent_id, message):
        sent.append((agent_id, message))
        return True

    # `app.ai_web.registry` resolves to the AgentRegistry *instance* (the module name
    # is rebound by the package), which is exactly what relay_api calls into.
    monkeypatch.setattr(registry_mod, "send_to_agent", _send, raising=False)
    envelope = rl.build_envelope(
        "g-7f3a",
        {"event_id": "e1", "type": "chat", "content": "[System] the user spoke"},
        "machine-b",
        user_id="u-1",
        kind="task:relay",
        target_agent_id="pm",
    )

    res = asyncio.run(relay_api.relay_deliver(_request({"X-Relay-Secret": secret}), body=envelope))

    assert res["delivered"] is True
    assert sent[0][0] == "pm"
    assert sent[0][1]["content"] == "[System] the user spoke"
    assert sent[0][1]["relayed"] is True


def test_a_task_event_for_an_unknown_agent_is_not_claimed_as_delivered(store, monkeypatch):
    secret = rl.new_secret()
    rl.remember_outbound("g-7f3a", "machine-b", secret, user_id="u-1")
    envelope = rl.build_envelope(
        "g-7f3a", {"event_id": "e1"}, "machine-b", user_id="u-1", kind="task:relay", target_agent_id=""
    )

    res = asyncio.run(relay_api.relay_deliver(_request({"X-Relay-Secret": secret}), body=envelope))

    assert res == {"ok": True, "delivered": False, "reason": "no_agent"}


def test_the_task_endpoint_relays_what_it_dispatches(store):
    """Regression: the task window's message was given only to the agents connected
    here, so a participant on a paired machine never heard the user at all."""
    route = (_BACKEND_DIR / "app" / "ai_web" / "routes" / "_main.py").read_text(encoding="utf-8")
    at = route.index("async def post_collab_task_message")
    body = route[at : at + 8000]

    assert "await relay.fan_out_task(" in body
    assert '"event_id": uuid.uuid4().hex' in body


def test_a_bound_secret_only_delivers_to_its_own_user(store, monkeypatch):
    """A peer holding a valid secret cannot aim a push at another local agent."""
    secret = rl.new_secret()
    rl.remember_outbound("g-7f3a", "machine-b", secret, user_id="u-1")
    envelope = rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-b", user_id="u-2")

    with pytest.raises(HTTPException) as exc:
        _deliver(monkeypatch, secret=secret, body=envelope)

    assert exc.value.status_code == 403


def test_a_bound_secret_still_reaches_the_user_it_was_minted_for(store, monkeypatch):
    secret = rl.new_secret()
    rl.remember_outbound("g-7f3a", "machine-b", secret, user_id="u-1")
    envelope = rl.build_envelope("g-7f3a", {"id": "m_1", "content": "hi"}, "machine-b", user_id="u-1")

    res, mgr = _deliver(monkeypatch, secret=secret, body=envelope)

    assert res["delivered"] is True
    assert mgr.personal and mgr.personal[0][0] == "u-1"
