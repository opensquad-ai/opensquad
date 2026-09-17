"""One group message must produce exactly one agent turn.

Two independent duplication paths are locked here, because either one alone
still produced repeated agent replies in the group:

1. ``ChatProBridge`` — the gateway delivers every group message once per live
   WebSocket connection, and ``connect_ws()`` reconnects forever. Stacking a
   second loop (boot retry while the first was still running or mid-cancel), or
   a subscribe replay on a stale connection, therefore handed the same message
   to the pipeline several times.
2. ``MessageRouter.route_group_message`` — ``MessageQueue.put`` already drops a
   duplicate id, but the router ignored that return value and still ran the
   state machine, firing ``trigger_process_queue`` a second time.

Measured 2026-09-17 (agent305): a single group message produced repeated
identical replies.
"""

import asyncio
from collections import deque

import pytest

from opensquad.bridge import ChatProBridge


class _FakeState:
    def __init__(self, state: str, wake_mode: str):
        self._state = state
        self._wake_mode = wake_mode

    async def get_state(self):
        return self._state

    async def get_wake_mode(self):
        return self._wake_mode


def _bare_bridge() -> ChatProBridge:
    """A bridge with the attributes ``__init__`` would have set, no I/O."""
    bridge = ChatProBridge.__new__(ChatProBridge)
    bridge.base_url = "http://127.0.0.1:1"
    bridge.ws_url = "ws://127.0.0.1:1/ws"
    bridge.email = "x@x"
    bridge.password = "x"
    bridge.agent_name = "x"
    bridge.token = "tok"  # truthy -> connect_ws() enters its reconnect loop
    bridge.user_id = None
    bridge.ws = None
    bridge._connected = False
    bridge._subscriptions = set()
    bridge._group_cache = {}
    bridge._group_cache_ts = 0.0
    bridge._ws_task = None
    bridge._seen_msg_ids = deque(maxlen=200)
    return bridge


def test_connect_ws_is_single_owner_and_does_not_stack_loops():
    """A second connect_ws() must wait for the first instead of stacking."""
    bridge = _bare_bridge()

    async def scenario():
        first = asyncio.create_task(bridge.connect_ws())
        # Wait until the first loop has claimed ownership.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if bridge._ws_task is first:
                break
        assert bridge._ws_task is first, "first connect_ws() never claimed ownership"

        second = asyncio.create_task(bridge.connect_ws())
        await asyncio.sleep(0.2)
        assert not second.done(), "second connect_ws() must wait, not start a second loop"
        assert bridge._ws_task is first

        # Cancelling the owner lets the waiter take over.
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        for _ in range(100):
            await asyncio.sleep(0.01)
            if bridge._ws_task is second:
                break
        assert bridge._ws_task is second, "waiter never took over ownership"

        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second

    asyncio.run(asyncio.wait_for(scenario(), timeout=15))


def test_bridge_ignores_a_replayed_group_message(monkeypatch):
    """The same message id must only reach the router once."""
    import opensquad.message_router as mr_mod

    routed: list[dict] = []

    class _Router:
        async def route_group_message(self, payload):
            routed.append(payload)
            return {"action": "queued"}

    async def _resolve(*_a, **_k):
        return "x"

    async def _no_attachments(*_a, **_k):
        return []

    monkeypatch.setattr(mr_mod, "message_router", _Router())

    bridge = _bare_bridge()
    bridge._resolve_group_name = _resolve
    bridge._resolve_sender_name = _resolve
    bridge._download_attachments = _no_attachments

    message = {
        "type": "new_message",
        "data": {"id": "m_1", "sender_id": "user1", "group_id": "g1", "content": "hi"},
    }
    for _ in range(3):
        asyncio.run(bridge._handle_message(dict(message)))

    assert len(routed) == 1, f"replayed message reached the router {len(routed)} times"


@pytest.mark.asyncio
async def test_router_does_not_wake_again_for_a_duplicate(monkeypatch):
    """A duplicate dropped by the queue must not fire a second wake-up."""
    from opensquad import message_router as mr_mod
    from opensquad.message_queue import MessageQueue

    queue = MessageQueue()
    triggers: list[str] = []

    monkeypatch.setattr(mr_mod, "get_message_queue", lambda *_a, **_k: queue)
    monkeypatch.setattr(mr_mod, "get_state_manager", lambda: _FakeState("idle", "normal"))
    monkeypatch.setattr(
        mr_mod,
        "get_sleep_controller",
        lambda: type("SC", (), {"wake_up": staticmethod(lambda *_: None)})(),
    )

    def _trigger(**kwargs):
        triggers.append(kwargs.get("source", ""))
        return "primary-sid"

    monkeypatch.setattr(mr_mod, "trigger_process_queue", _trigger)

    router = mr_mod.MessageRouter()
    message = {
        "id": "g_msg_1",
        "group_id": "g1",
        "source_name": "grp",
        "sender_id": "u1",
        "sender_name": "u",
        "content": "hi",
    }

    first = await router.route_group_message(dict(message))
    second = await router.route_group_message(dict(message))

    assert first["queued"] is True and first["pushed"] is True
    assert second["action"] == "duplicate_dropped"
    assert second["queued"] is False and second["pushed"] is False
    assert len(triggers) == 1, "duplicate message woke the agent a second time"
