"""The relay's delivery queue: an undelivered push is written down, retried, and
flushed the moment the subscriber comes back.

Before this, a push that failed was counted and dropped. The group lives on the *owner*
machine, so a subscriber that never heard a message cannot fetch it — its history is this
machine's history, and a task event is in no history at all. A peer that was simply off
lost the message, silently, and nothing ever went back for it.
"""

from __future__ import annotations

import asyncio
import json
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
    rl.clear_outbox()
    return tmp_path / "relay"


def _request(headers: dict | None = None):
    return SimpleNamespace(headers=headers or {})


def _peer_ok(monkeypatch):
    import opensquad.node_peers as np

    monkeypatch.setattr(np, "verify", lambda token: {"id": "peer_1", "name": "machine-b", "scopes": ("group:join",)})


class _Body:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _ScriptedClient:
    """A peer whose answers are scripted — down first, healthy later.

    Each entry is a status code, or ``(status, body)`` to answer with a JSON body.
    """

    def __init__(self, *answers):
        self._answers = list(answers)
        self.posts: list[dict] = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        answer = self._answers.pop(0) if self._answers else (200, {"ok": True, "delivered": True})
        if isinstance(answer, tuple):
            return _Body(answer[1], status=answer[0])
        return _Body({"ok": True, "delivered": True}, status=answer)


def _use_client(monkeypatch, client):
    import app.http_clients as hc

    monkeypatch.setattr(hc, "get_local_http_client", lambda: client)


# ── the queue itself ────────────────────────────────────────────────────────


def test_a_failed_push_is_queued_exactly_as_it_was_attempted(store):
    envelope = rl.build_envelope("g-7f3a", {"id": "m_1", "content": "hi"}, "machine-a", user_id="u-1")

    res = rl.enqueue_outbox(
        group_id="g-7f3a",
        envelope=envelope,
        callback_url="http://home-a:9555",
        user_id="u-1",
        error="HTTP 500",
        now=1000.0,
    )

    assert res["ok"] is True and res["queued"] == 1
    entry = rl.outbox_entries()[0]
    # the frame is kept verbatim: a retry has to send the identical envelope, or the
    # receiving gateway cannot tell it is the same message and would deliver twice
    assert entry["envelope"] == envelope
    assert entry["group_id"] == "g-7f3a" and entry["user_id"] == "u-1"
    assert entry["callback_url"] == "http://home-a:9555"
    assert entry["last_error"] == "HTTP 500" and entry["attempts"] == 0
    assert entry["first_at"] == 1000.0


def test_the_same_message_to_the_same_subscriber_does_not_stack(store):
    for _ in range(3):
        rl.enqueue_outbox(
            group_id="g-7f3a",
            envelope=rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-a", user_id="u-1"),
            callback_url="http://home-a:9555",
            user_id="u-1",
            now=1000.0,
        )

    assert rl.outbox_size() == 1
    assert rl.outbox_entries()[0]["first_at"] == 1000.0  # the original age is kept


def test_a_frame_without_a_message_id_is_not_queued(store):
    """A retry is recognised by its message id. Without one, a retry could deliver
    twice, so the frame is dropped loudly instead of being guessed at."""
    res = rl.enqueue_outbox(
        group_id="g-7f3a",
        envelope=rl.build_envelope("g-7f3a", {"content": "no id"}, "machine-a", user_id="u-1"),
        callback_url="http://home-a:9555",
        user_id="u-1",
    )

    assert res == {"ok": False, "error": "no_message_id"}
    assert rl.outbox_size() == 0


def test_a_retry_waits_for_its_backoff(store):
    rl.enqueue_outbox(
        group_id="g-7f3a",
        envelope=rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-a", user_id="u-1"),
        callback_url="http://home-a:9555",
        user_id="u-1",
        now=1000.0,
    )
    key = rl.outbox_entries()[0]["key"]
    assert [e["key"] for e in rl.due_outbox(now=1000.0)] == [key]

    rl.record_outbox_attempt(key, "HTTP 500", now=1000.0)

    assert rl.due_outbox(now=1000.5) == []  # backing off
    assert [e["key"] for e in rl.due_outbox(now=1000.0 + rl.OUTBOX_BASE_DELAY_S + 0.1)] == [key]


def test_attempts_are_capped_and_the_entry_is_given_up_on(store):
    rl.enqueue_outbox(
        group_id="g-7f3a",
        envelope=rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-a", user_id="u-1"),
        callback_url="http://home-a:9555",
        user_id="u-1",
        now=1000.0,
    )
    key = rl.outbox_entries()[0]["key"]

    for attempt in range(rl.OUTBOX_MAX_ATTEMPTS):
        res = rl.record_outbox_attempt(key, "HTTP 500", now=1000.0 + attempt)

    assert res["dropped"] is True
    assert rl.outbox_size() == 0


def test_an_entry_past_its_ttl_is_pruned_from_the_file(store):
    rl.enqueue_outbox(
        group_id="g-7f3a",
        envelope=rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-a", user_id="u-1"),
        callback_url="http://home-a:9555",
        user_id="u-1",
        now=1000.0,
    )

    assert rl.due_outbox(now=1000.0 + rl.OUTBOX_TTL_S + 1) == []  # not retried…
    assert rl.prune_outbox(now=1000.0 + rl.OUTBOX_TTL_S + 1) == 1  # …and removed
    assert rl.outbox_size() == 0


def test_the_queue_is_capped_and_keeps_the_newest(store):
    for n in range(rl.OUTBOX_MAX_ENTRIES + 3):
        rl.enqueue_outbox(
            group_id="g-7f3a",
            envelope=rl.build_envelope("g-7f3a", {"id": f"m_{n}"}, "machine-a", user_id="u-1"),
            callback_url="http://home-a:9555",
            user_id="u-1",
            now=1000.0 + n,
        )

    entries = rl.outbox_entries()
    assert len(entries) == rl.OUTBOX_MAX_ENTRIES
    ids = {e["dedupe_id"] for e in entries}
    assert "m_0" not in ids and f"m_{rl.OUTBOX_MAX_ENTRIES + 2}" in ids


def test_the_dedupe_window_outlives_the_queue(store):
    """The invariant the retry safety rests on: a retry that arrives after the queue
    has been waiting must still be recognised as the same message. If the queue
    outlived the window, a late retry would be delivered twice."""
    assert rl.DEDUP_TTL_S > rl.OUTBOX_TTL_S


def test_every_retry_fits_inside_the_queue_lifetime(store):
    """The backoff must not push the last attempt past the TTL — an entry that expires
    before it runs out of attempts is a frame the queue pretended to keep."""
    waits = sum(rl._backoff_delay(n) for n in range(1, rl.OUTBOX_MAX_ATTEMPTS))
    assert waits < rl.OUTBOX_TTL_S
    # the waits grow and then hold at the ceiling
    assert rl._backoff_delay(1) == rl.OUTBOX_BASE_DELAY_S
    assert rl._backoff_delay(2) == 2 * rl.OUTBOX_BASE_DELAY_S
    assert rl._backoff_delay(50) == rl.OUTBOX_MAX_DELAY_S


def test_the_queue_is_on_disk_so_a_restart_resumes_it(store):
    rl.enqueue_outbox(
        group_id="g-7f3a",
        envelope=rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-a", user_id="u-1"),
        callback_url="http://home-a:9555",
        user_id="u-1",
    )

    on_disk = json.loads(Path(rl.outbox_file()).read_text(encoding="utf-8"))
    queued = on_disk["links"]["outbox"]
    assert len(queued) == 1
    assert next(iter(queued.values()))["envelope"]["data"]["id"] == "m_1"


def test_status_reports_what_is_waiting(store):
    rl.enqueue_outbox(
        group_id="g-7f3a",
        envelope=rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-a", user_id="u-1"),
        callback_url="http://home-a:9555",
        user_id="u-1",
        now=1000.0,
    )

    outbox = rl.status()["outbox"]

    assert outbox["queued"] == 1
    assert outbox["max_attempts"] == rl.OUTBOX_MAX_ATTEMPTS
    assert outbox["ttl_s"] == rl.OUTBOX_TTL_S


# ── fan-out: a failure is queued, not dropped ───────────────────────────────


def test_a_failed_fan_out_is_queued_and_reported(store, monkeypatch):
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    _use_client(monkeypatch, _ScriptedClient(500))

    res = asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1", "content": "hi"}, origin_host="machine-a"))

    assert res == {"ok": False, "delivered": 0, "failed": 1, "queued": 1}
    entry = gw_relay.outbox_entries()[0]
    assert entry["group_id"] == "g-7f3a" and entry["user_id"] == "u-1"
    assert entry["envelope"]["target_user_id"] == "u-1"
    assert entry["envelope"]["data"]["id"] == "m_1"
    assert entry["last_error"] == "HTTP 500"


def test_a_push_that_reached_nobody_is_queued_not_lost(store, monkeypatch):
    """The agent behind the peer is offline: the peer answers 200 with delivered:false.
    That is the case the queue exists for — it must not be counted as done."""
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1", agent_id="pm")
    _use_client(monkeypatch, _ScriptedClient((200, {"ok": True, "delivered": False, "reason": "no_agent"})))

    res = asyncio.run(gw_relay.fan_out_task("g-7f3a", {"event_id": "e1", "content": "hi"}, origin_host="machine-a"))

    assert res["queued"] == 1 and res["failed"] == 1
    entry = gw_relay.outbox_entries()[0]
    assert entry["kind"] == "task:relay"
    # the agent the retry must reach is preserved in the stored frame
    assert entry["envelope"]["target_agent_id"] == "pm"
    assert entry["last_error"] == "no_agent"


def test_a_successful_push_is_not_queued(store, monkeypatch):
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    _use_client(monkeypatch, _ScriptedClient())

    res = asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1"}, origin_host="machine-a"))

    assert res == {"ok": True, "delivered": 1, "failed": 0, "queued": 0}
    assert gw_relay.outbox_size() == 0


# ── retry ───────────────────────────────────────────────────────────────────


def test_a_queued_push_is_redelivered_when_the_peer_returns(store, monkeypatch):
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    _use_client(monkeypatch, _ScriptedClient(500))
    asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1", "content": "hi"}, origin_host="machine-a"))

    healthy = _ScriptedClient()
    _use_client(monkeypatch, healthy)
    res = asyncio.run(gw_relay.flush_outbox())

    assert res == {"ok": True, "delivered": 1, "failed": 0, "dropped": 0}
    assert gw_relay.outbox_size() == 0
    assert healthy.posts[0]["url"] == "http://home-a:9555/api/relay/deliver"
    # the retry sends the frame it stored, and the secret from the live subscription
    assert healthy.posts[0]["json"]["data"]["content"] == "hi"
    assert healthy.posts[0]["headers"]["X-Relay-Secret"] == "s1"


def test_a_retry_that_fails_again_backs_off(store, monkeypatch):
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    _use_client(monkeypatch, _ScriptedClient(500))
    asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1"}, origin_host="machine-a"))

    still_down = _ScriptedClient(500)
    _use_client(monkeypatch, still_down)
    first = asyncio.run(gw_relay.flush_outbox())

    assert first["failed"] == 1 and len(still_down.posts) == 1
    assert gw_relay.outbox_entries()[0]["attempts"] == 1

    # the very next pass must not hammer a peer that just failed
    healthy = _ScriptedClient()
    _use_client(monkeypatch, healthy)
    second = asyncio.run(gw_relay.flush_outbox())

    assert second["delivered"] == 0 and healthy.posts == []
    assert gw_relay.outbox_size() == 1

    # …and once the backoff has passed, it is retried and delivered
    next_at = gw_relay.outbox_entries()[0]["next_at"]
    third = asyncio.run(gw_relay.flush_outbox(now=next_at + 0.1))

    assert third["delivered"] == 1
    assert gw_relay.outbox_size() == 0


def test_a_retry_is_skipped_when_the_subscription_is_gone(store, monkeypatch):
    """Unsubscribed, or the peer was revoked: nobody is listening, so the entry goes
    instead of being retried forever."""
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    _use_client(monkeypatch, _ScriptedClient(500))
    asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1"}, origin_host="machine-a"))
    rl.unsubscribe("g-7f3a", "http://home-a:9555", "u-1")

    client = _ScriptedClient()
    _use_client(monkeypatch, client)
    res = asyncio.run(gw_relay.flush_outbox())

    assert res == {"ok": True, "delivered": 0, "failed": 0, "dropped": 1}
    assert client.posts == []
    assert gw_relay.outbox_size() == 0


def test_a_flush_can_be_limited_to_one_subscriber(store, monkeypatch):
    rl.subscribe("g-a", "http://home-a:9555", "s-a", user_id="u-1")
    rl.subscribe("g-b", "http://home-b:9555", "s-b", user_id="u-2")
    _use_client(monkeypatch, _ScriptedClient(500, 500))
    asyncio.run(gw_relay.fan_out("g-a", {"id": "m_a"}, origin_host="machine-a"))
    asyncio.run(gw_relay.fan_out("g-b", {"id": "m_b"}, origin_host="machine-a"))
    assert gw_relay.outbox_size() == 2

    client = _ScriptedClient()
    _use_client(monkeypatch, client)
    res = asyncio.run(gw_relay.flush_outbox(group_id="g-a", callback_url="http://home-a:9555", user_id="u-1"))

    assert res["delivered"] == 1
    assert [p["json"]["group_id"] for p in client.posts] == ["g-a"]
    remaining = gw_relay.outbox_entries()
    assert [e["group_id"] for e in remaining] == ["g-b"]


# ── backfill when the subscriber (re)subscribes ─────────────────────────────


def test_subscribing_flushes_the_backlog_for_that_subscriber(store, monkeypatch):
    """The other half of "offline": a machine that comes back re-subscribes, and the
    owner hands it everything it could not deliver while it was away — immediately,
    rather than at the next retry tick."""
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    _use_client(monkeypatch, _ScriptedClient(500))
    asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1", "content": "hi"}, origin_host="machine-a"))

    _peer_ok(monkeypatch)
    client = _ScriptedClient()
    _use_client(monkeypatch, client)
    res = asyncio.run(
        relay_api.relay_subscribe(
            _request({"X-Node-Token": "good"}),
            body={
                "group_id": "g-7f3a",
                "callback_url": "http://home-a:9555",
                "secret": "s1",
                "user_id": "u-1",
            },
        )
    )

    assert res["ok"] is True
    assert res["backfilled"] == 1 and res["backfill_failed"] == 0
    assert gw_relay.outbox_size() == 0
    assert client.posts[0]["json"]["data"]["content"] == "hi"


def test_a_failed_backfill_does_not_fail_the_subscription(store, monkeypatch):
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    _use_client(monkeypatch, _ScriptedClient(500))
    asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1"}, origin_host="machine-a"))

    _peer_ok(monkeypatch)
    _use_client(monkeypatch, _ScriptedClient(500))
    res = asyncio.run(
        relay_api.relay_subscribe(
            _request({"X-Node-Token": "good"}),
            body={
                "group_id": "g-7f3a",
                "callback_url": "http://home-a:9555",
                "secret": "s1",
                "user_id": "u-1",
            },
        )
    )

    assert res["ok"] is True  # the subscription is recorded regardless
    assert res["backfill_failed"] == 1
    assert gw_relay.outbox_size() == 1  # still queued, one attempt closer to the cap


def test_subscribing_without_a_backlog_says_so(store, monkeypatch):
    _peer_ok(monkeypatch)

    res = asyncio.run(
        relay_api.relay_subscribe(
            _request({"X-Node-Token": "good"}),
            body={"group_id": "g-clean", "callback_url": "http://home-a:9555", "secret": "s1", "user_id": "u-1"},
        )
    )

    assert res["backfilled"] == 0 and res["backfill_failed"] == 0


# ── the loop that makes retries happen without a human ──────────────────────


def test_the_retry_loop_starts_once_and_reports_itself(store):
    async def _run():
        first = gw_relay.ensure_retry_loop()
        second = gw_relay.ensure_retry_loop()
        running = gw_relay.retry_loop_running()
        await asyncio.sleep(0)
        return first, second, running

    first, second, running = asyncio.run(_run())

    assert first == {"started": True, "running": True}
    assert second == {"started": False, "running": True}  # idempotent
    assert running is True


def test_without_a_running_loop_it_reports_that_instead_of_raising(store):
    # called from a plain sync context (as tests and scripts do): no loop to attach to
    assert gw_relay.ensure_retry_loop() == {"started": False, "running": False}


def test_the_gateway_starts_the_retry_loop(store):
    """Wiring lock: the loop is useless if nothing starts it at boot."""
    main = (_BACKEND_DIR / "app" / "main.py").read_text(encoding="utf-8")

    assert "_relay.ensure_retry_loop(" in main


def test_the_loop_survives_a_failing_pass(store, monkeypatch):
    """One bad pass must not end the loop: the queue would then never drain."""
    calls = {"n": 0}

    async def _boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("peer exploded")

    monkeypatch.setattr(gw_relay, "flush_outbox", _boom)

    async def _run():
        task = asyncio.get_running_loop().create_task(gw_relay._retry_loop(interval=0.01, first_delay=0.01))
        await asyncio.sleep(0.08)
        alive = not task.done()
        task.cancel()
        return alive

    assert asyncio.run(_run()) is True
    assert calls["n"] >= 2  # it kept going after the failure


def test_the_loop_drains_the_queue_without_a_human(store, monkeypatch):
    """The point of the whole thing: nobody has to re-send, restart or click. The
    message is queued while the peer is down and delivered by the loop once it is up."""
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    _use_client(monkeypatch, _ScriptedClient(500))
    asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1", "content": "hi"}, origin_host="machine-a"))
    assert gw_relay.outbox_size() == 1

    healthy = _ScriptedClient()
    _use_client(monkeypatch, healthy)

    async def _run():
        task = asyncio.get_running_loop().create_task(gw_relay._retry_loop(interval=0.01, first_delay=0.01))
        await asyncio.sleep(0.06)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())

    assert gw_relay.outbox_size() == 0
    assert len(healthy.posts) == 1
    assert healthy.posts[0]["json"]["data"]["content"] == "hi"


def test_subscribe_group_reports_what_the_owner_handed_back(store, monkeypatch):
    """The agent side of the backfill: it is told how much it missed, instead of
    catching up silently (or not at all)."""
    import opensquad.peer_bridge as pb

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "backfilled": 3, "backfill_failed": 0}

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=lambda *a, **k: _Resp()))
    monkeypatch.setattr(pb, "find_peer", lambda host: {"base_url": "http://home-a:9555"})
    monkeypatch.setattr(pb, "peer_token", lambda host: "tok")
    monkeypatch.setattr(pb, "_home_gateway_url", lambda: "http://home-a:9555")

    res = pb.subscribe_group("192.168.5.4", "g-7f3a")

    assert res["ok"] is True and res["backfilled"] == 3


def test_subscribe_group_tolerates_an_owner_that_says_nothing(store, monkeypatch):
    """An owner from before the queue answers without the field: the subscription must
    still be recorded, just with nothing to report."""
    import opensquad.peer_bridge as pb

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True}

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=lambda *a, **k: _Resp()))
    monkeypatch.setattr(pb, "find_peer", lambda host: {"base_url": "http://home-a:9555"})
    monkeypatch.setattr(pb, "peer_token", lambda host: "tok")
    monkeypatch.setattr(pb, "_home_gateway_url", lambda: "http://home-a:9555")

    res = pb.subscribe_group("192.168.5.4", "g-7f3a")

    assert res["ok"] is True and res["backfilled"] == 0


def test_a_backlog_is_delivered_in_the_order_it_was_queued(store, monkeypatch):
    """A machine that comes back should read what it missed in the order it was sent.
    The frames were queued oldest-first, so the flush keeps that order per subscriber."""
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    _use_client(monkeypatch, _ScriptedClient(500, 500, 500))
    for n in (1, 2, 3):
        asyncio.run(gw_relay.fan_out("g-7f3a", {"id": f"m_{n}"}, origin_host="machine-a"))
    assert gw_relay.outbox_size() == 3

    client = _ScriptedClient()
    _use_client(monkeypatch, client)
    res = asyncio.run(gw_relay.flush_outbox())

    assert res["delivered"] == 3
    assert [p["json"]["data"]["id"] for p in client.posts] == ["m_1", "m_2", "m_3"]


class _SelectiveClient:
    """Answers a healthy peer, and never answers a slow one."""

    def __init__(self):
        self.urls: list[str] = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.urls.append(url)
        if "slow" in url:
            await asyncio.sleep(5)
        return _Body({"ok": True, "delivered": True})


def test_one_slow_subscriber_does_not_delay_another(store, monkeypatch):
    """Ordering is per subscriber, not global: a peer that never answers must not hold
    up a healthy peer's backlog (which is why the fan-out is concurrent at all)."""
    rl.subscribe("g-7f3a", "http://slow:9555", "s-slow", user_id="u-slow")
    rl.subscribe("g-7f3a", "http://fast:9555", "s-fast", user_id="u-fast")
    _use_client(monkeypatch, _ScriptedClient(500, 500))
    asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_1"}, origin_host="machine-a"))
    asyncio.run(gw_relay.fan_out("g-7f3a", {"id": "m_2"}, origin_host="machine-a"))

    client = _SelectiveClient()
    _use_client(monkeypatch, client)
    res = asyncio.run(gw_relay.flush_outbox(budget=0.3))

    assert res["delivered"] == 1  # the healthy peer's frame went
    assert res["failed"] == 1  # the slow one is still queued for the next pass
    assert gw_relay.outbox_size() == 1
    assert gw_relay.outbox_entries()[0]["user_id"] == "u-slow"


def test_over_real_sockets_an_offline_agent_queues_and_is_served_on_retry(store):
    """End-to-end, no fakes: a real HTTP peer gateway that answers exactly what the home
    gateway answers — 200 with delivered:false while the agent is offline — and then
    starts delivering. This is the case that used to lose the message silently."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = {"deliver": False, "posts": []}

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server's name
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            state["posts"].append(
                {
                    "path": self.path,
                    "secret": self.headers.get("X-Relay-Secret"),
                    "group": body.get("group_id"),
                    "target": body.get("target_user_id"),
                    "content": (body.get("data") or {}).get("content"),
                }
            )
            answer = {"ok": True, "delivered": state["deliver"]}
            if not state["deliver"]:
                answer["reason"] = "no_agent"
            payload = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # keep the test output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        rl.subscribe("g-7f3a", base, "s-real", user_id="u-1", agent_id="pm")

        async def _scenario():
            import app.http_clients as hc

            await hc.close_shared_http_clients()  # a client for this loop, not another test's
            first = await gw_relay.fan_out(
                "g-7f3a", {"id": "m_real", "content": "你离线时我发的"}, origin_host="machine-a"
            )
            state["deliver"] = True  # the agent comes back
            second = await gw_relay.flush_outbox()
            await hc.close_shared_http_clients()
            return first, second

        first, second = asyncio.run(_scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert first == {"ok": False, "delivered": 0, "failed": 1, "queued": 1}
    assert second["delivered"] == 1
    assert gw_relay.outbox_size() == 0
    # the same frame, to the same subscription, twice: the first attempt (refused by the
    # offline agent) and the retry that landed — and the queue is empty afterwards
    assert [p["path"] for p in state["posts"]] == ["/api/relay/deliver", "/api/relay/deliver"]
    assert state["posts"][0]["secret"] == "s-real"
    assert state["posts"][0]["target"] == "u-1"
    assert state["posts"][1]["content"] == "你离线时我发的"


def test_a_relay_push_still_refuses_an_unknown_secret(store, monkeypatch):
    """Guard rail: the queue must not have loosened delivery authentication."""
    envelope = rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-a", user_id="u-1")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(relay_api.relay_deliver(_request({"X-Relay-Secret": "forged"}), body=envelope))

    assert exc.value.status_code == 401
