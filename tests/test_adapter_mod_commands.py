"""The adapter's two mod commands — the live path a UI action takes.

`mod_action` (a mod's button press) and `mod_command` (a mod's slash command)
arrive as ordinary gateway commands and are handed to the bus, where `mods_host`
runs them. The adapter cannot reach the plugin directly (it holds no plugin
manager reference), so the topic name and payload shape *are* the contract.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


def _adapter():
    from opensquad.gateway_adapter import GatewayAdapter

    adapter = object.__new__(GatewayAdapter)
    adapter._user_id_by_sid = {}
    return adapter


@pytest.fixture
def bus_spy(monkeypatch):
    import opensquad.events as events_mod

    sent: list[tuple[str, dict]] = []

    async def fake_emit(topic, payload):
        sent.append((topic, payload))

    monkeypatch.setattr(events_mod.bus, "emit_async", fake_emit)
    return sent


async def test_mod_command_reaches_the_bus(bus_spy):
    await _adapter()._handle_command(
        {
            "command": "mod_command",
            "user_id": "u1",
            "data": {"name": "/replay", "args": ["a", "b"], "session_id": "sid-1"},
        }
    )
    assert bus_spy == [("mod_command", {"name": "replay", "args": ["a", "b"], "sid": "sid-1", "user_id": "u1"})], (
        "the leading slash must be stripped and the session carried"
    )


async def test_mod_command_without_a_name_emits_nothing(bus_spy):
    await _adapter()._handle_command({"command": "mod_command", "user_id": "u1", "data": {}})
    assert bus_spy == []


async def test_mod_action_reaches_the_bus(bus_spy):
    await _adapter()._handle_command(
        {
            "command": "mod_action",
            "user_id": "u1",
            "data": {"action": "AbovePrompt:1", "session_id": "sid-2"},
        }
    )
    assert bus_spy == [("mod_action", {"action": "AbovePrompt:1", "sid": "sid-2", "user_id": "u1"})], (
        "the action id travels verbatim — the host minted it"
    )


async def test_mod_action_without_an_id_emits_nothing(bus_spy):
    await _adapter()._handle_command({"command": "mod_action", "user_id": "u1", "data": {}})
    assert bus_spy == []


async def test_a_topic_the_plugin_never_subscribes_to_is_not_invented(bus_spy):
    """Guard against a rename on one side: these are the exact topics mods_host
    subscribes to (`plugin.on_load`)."""
    import os

    src = open(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "plugins", "mods_host", "plugin.py"
        ),
        encoding="utf-8",
    ).read()
    assert 'bus.subscribe("mod_action"' in src
    assert 'bus.subscribe("mod_command"' in src


# ── the downlink: what key the tree actually arrives under ──────────────────


def _sending_adapter():
    """An adapter wired to a spy sender, so we can read the wire frame.

    `send_response`/`send_response_to_user` are the last Python stop before the
    gateway; `broadcast_to_agent` then forwards this frame **verbatim**
    (`ws.send_json(message)`), so the key asserted here is the key the browser
    sees. Every other frontend handler reads `content ?? data` — a reader that
    looks at only one of them silently renders nothing.
    """
    from opensquad.gateway_adapter import GatewayAdapter

    adapter = GatewayAdapter.__new__(GatewayAdapter)
    frames: list[dict] = []

    async def _fake_send_response(content, msg_type="message", sid="", **meta):
        frames.append({"type": msg_type, "sid": sid, "content": content})

    async def _fake_send_response_to_user(uid, content, msg_type="message", sid="", **meta):
        frames.append({"type": msg_type, "sid": sid, "content": content})

    adapter._user_id_by_sid = {}
    adapter.current_user_id = None
    adapter.connected = True
    adapter.send_response = _fake_send_response
    adapter.send_response_to_user = _fake_send_response_to_user
    return adapter, frames


async def test_a_slot_tree_rides_in_content_not_data():
    """`{sid, data}` on the bus is unwrapped, and the tree lands in `content`.

    This is the frame shape the browser gets: the frontend must read
    `msg.content ?? msg.data`, like every other handler in `useAgentWebSocket`.
    Reading `msg.data` alone yields `undefined` → zero nodes → an empty band,
    with nothing in any log to say why.
    """
    adapter, frames = _sending_adapter()
    tree = {"slot": "AbovePrompt", "nodes": [{"type": "Text", "props": {"text": "hi"}}]}
    await adapter.on_generic_event("mod_slot")({"sid": "sid-1", "data": tree})

    assert len(frames) == 1
    frame = frames[0]
    assert frame["type"] == "mod_slot"
    assert frame["sid"] == "sid-1", "the sid is what routes the band to a pane"
    assert frame["content"] == tree, "the tree must ride in `content`"
    assert "data" not in frame, "the wrapper is consumed — it is not echoed twice"


async def test_the_command_list_is_unwrapped_too():
    """The command announcement uses the same envelope, and needs to.

    It has no session (the list belongs to the agent), so it carries `sid: ""`.
    `_unwrap` only strips the wrapper when **both** keys are present — a bare
    `{"agent_id", "data"}` reaches the browser as `content.data.commands`, one
    level deeper than the handler reads, and the composer's mod menu stays empty
    with no error anywhere.
    """
    adapter, frames = _sending_adapter()
    commands = [{"name": "warm", "help": "warm the cache"}]
    await adapter.on_generic_event("mod_commands")({"sid": "", "data": {"commands": commands}, "agent_id": "a1"})

    assert len(frames) == 1
    frame = frames[0]
    assert frame["type"] == "mod_commands"
    assert frame["sid"] == "", "an agent-scoped frame has no session"
    assert frame["content"] == {"commands": commands}, "unwrapped, so `content.commands` reads"
