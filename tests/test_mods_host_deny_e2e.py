"""S1 — `tool.call` → `{deny}` across the process boundary.

The fixture (`tests/fixtures/mods/deny-demo`) is a well-formed mod in the
canonical shape; these tests are the M0 acceptance line: a mod's `{deny}` must
actually refuse the tool on the Python side, through the Node child process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

from opensquad import mods_compat  # noqa: E402
from plugins.mods_host import _node  # noqa: E402
from plugins.mods_host._rpc import NodeHostClient  # noqa: E402
from plugins.mods_host.plugin import ModsHostPlugin  # noqa: E402

FIXTURE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "mods")
MOD_NAME = "deny-demo"

pytestmark = pytest.mark.skipif(not _node.resolve_node_executable(), reason="node runtime not available on this box")


@pytest.fixture
def plugin(tmp_path):
    state_root = str(tmp_path / "state")
    mods_compat.write_mod_state(MOD_NAME, enabled=True, state_root=state_root)
    instance = ModsHostPlugin(None)  # context is unused by this plugin
    instance._mods = instance._load_mods(root=FIXTURE_ROOT, state_root=state_root)
    yield instance
    instance.on_unload()


def _ctx(tool_name: str, arguments: dict | None = None) -> dict:
    return {"tool_name": tool_name, "arguments": arguments or {}, "agent_id": "test-agent"}


# ── the fixture is a real mod by our own scanner ────────────────────────────


def test_fixture_is_a_wellformed_mod():
    info = mods_compat.scan_mod(os.path.join(FIXTURE_ROOT, MOD_NAME))
    # `$.ui.log` is degraded in the matrix, so the mod is `partial` — runnable.
    assert info["verdict"] == "partial", info
    assert info["blocked_by"] == []
    assert info["modules"] == ["hooks/guard.mjs"]
    assert "tool.call" in info["used_events"]


# ── discovery ──────────────────────────────────────────────────────────────


def test_enabled_mod_is_discovered(plugin):
    assert [m["name"] for m in plugin._mods] == [MOD_NAME]
    assert plugin._mods[0]["modules"] == ["hooks/guard.mjs"]
    assert os.path.isdir(plugin._mods[0]["root"])


def test_disabled_mod_is_not_loaded(tmp_path):
    state_root = str(tmp_path / "state")
    mods_compat.write_mod_state(MOD_NAME, enabled=False, state_root=state_root)
    instance = ModsHostPlugin(None)
    assert instance._load_mods(root=FIXTURE_ROOT, state_root=state_root) == []


def test_no_state_at_all_means_not_loaded(tmp_path):
    """Enablement is opt-in: a discovered mod with no state.json stays off."""
    instance = ModsHostPlugin(None)
    assert instance._load_mods(root=FIXTURE_ROOT, state_root=str(tmp_path / "unused")) == []


# ── the deny path ──────────────────────────────────────────────────────────


async def test_denied_tool_is_refused_on_the_python_side(plugin):
    ctx = await plugin.on_before_tool(_ctx("demo.echo"))
    assert ctx["skip"] is True
    assert ctx["result"] == "Error: deny-demo: demo.echo is blocked by the fixture mod"


async def test_catch_fallback_decides_when_the_handler_throws(plugin):
    ctx = await plugin.on_before_tool(_ctx("demo.boom"))
    assert ctx["skip"] is True
    assert ".catch" in ctx["result"]


async def test_tool_specific_fields_are_spread_onto_the_event(plugin):
    """A guard written against `e.command` (the reference shape) must work."""
    ctx = await plugin.on_before_tool(_ctx("demo.shell", {"command": "git push --force"}))
    assert ctx["skip"] is True
    assert "saw command git push --force" in ctx["result"]


async def test_reentrant_gate_call_inside_a_hook_does_not_deadlock(plugin):
    """S2's acceptance: host→Python→host inside a hook, with `tool.call` pending.

    The fixture handler awaits `$.session.cwd()`. If the frame dispatcher
    mistakes the inbound request for a stale reply (its id comes from the *other*
    id space, so it is never in our pending table), the outer call times out and
    the tool is let through — which is precisely what happened before
    `_on_frame` was reordered.
    """
    ctx = await plugin.on_before_tool(_ctx("demo.cwd"))
    assert ctx["skip"] is True
    assert "cwd=" in ctx["result"]
    assert plugin._workspace() in ctx["result"]
    # The gate was actually served, not swallowed by the mod.
    assert plugin._gate_calls.get("gate.session.cwd") == 1


async def test_gate_refuses_paths_outside_the_workspace(plugin, monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(plugin, "_workspace", lambda: str(ws))

    served = await plugin._on_host_request("gate.fs.read", {"path": os.path.join("..", "outside.txt")})
    assert "error" in served
    assert plugin._gate_calls.get("gate.fs.read") is None


async def test_gate_reads_a_workspace_file(plugin, monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "note.txt").write_text("hello from the workspace", encoding="utf-8")
    monkeypatch.setattr(plugin, "_workspace", lambda: str(ws))

    # Relative paths resolve against the workspace root, like a mod would expect.
    served = await plugin._on_host_request("gate.fs.read", {"path": "note.txt"})
    assert served["text"] == "hello from the workspace"
    assert plugin._gate_calls.get("gate.fs.read") == 1

    exists = await plugin._on_host_request("gate.fs.exists", {"path": "note.txt"})
    assert exists == {"exists": True}
    missing = await plugin._on_host_request("gate.fs.exists", {"path": "nope.txt"})
    assert missing == {"exists": False}


async def test_store_persists_per_mod_across_calls(plugin, monkeypatch, tmp_path):
    """`$.store` round-trips through the gate and is keyed by the mod's dir name."""
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))

    first = await plugin.on_before_tool(_ctx("demo.remember"))
    assert first["skip"] is True
    assert "runs=1" in first["result"]
    assert "keys=runs" in first["result"]

    second = await plugin.on_before_tool(_ctx("demo.remember"))
    assert "runs=2" in second["result"]
    assert plugin._gate_calls["gate.store.set"] == 2

    stored = mods_compat.read_mod_store(MOD_NAME)
    assert stored == {"runs": 2}


async def test_store_namespaces_are_isolated(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    await plugin._on_host_request("gate.store.set", {"_mod": "mod-a", "key": "k", "value": "A"})
    other = await plugin._on_host_request("gate.store.get", {"_mod": "mod-b", "key": "k"})
    assert other == {"value": None}
    assert mods_compat.read_mod_store("mod-a") == {"k": "A"}


async def test_store_is_refused_without_an_identity(plugin):
    served = await plugin._on_host_request("gate.store.set", {"key": "k", "value": 1})
    assert "error" in served


async def test_store_enforces_the_per_mod_cap(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(mods_compat, "MOD_STORE_LIMIT", 64)
    served = await plugin._on_host_request("gate.store.set", {"_mod": "big", "key": "k", "value": "x" * 200})
    assert "error" in served
    assert mods_compat.read_mod_store("big") == {}


async def test_render_slot_returns_a_validated_tree(plugin):
    """The mod draws with `$.ui.resolve(e)`; the host validates what comes back."""
    served = await plugin.render_slot("AbovePrompt")
    assert served["slot"] == "AbovePrompt"
    assert len(served["nodes"]) == 1
    root = served["nodes"][0]
    assert root["type"] == "Box"
    assert root["props"] == {"flexDirection": "row", "gap": 2}

    kinds = [child["type"] for child in root["children"]]
    assert kinds == ["Text", "Text", "Text", "Button"]  # the refused Client is gone
    assert "stray" in [c["children"][0] for c in root["children"] if c["type"] == "Text"]

    # The stray attribute and the refused element are named, not silently gone.
    assert any("notARealProp" in note for note in served["dropped"])
    assert any("Client" in note for note in served["dropped"])

    # `onPress` cannot cross the wire — it became an action id.
    button = next(c for c in root["children"] if c["type"] == "Button")
    assert button["props"]["label"] == "Ping"
    assert "onPress" not in button["props"]
    # Positional, not a global counter: an identical tree must produce an
    # identical id, or the plugin's "did the band change?" dedupe never fires and
    # the host's action registry grows on every turn.
    assert button["props"]["action"] == "AbovePrompt:1"

    again = await plugin.render_slot("AbovePrompt")
    again_button = next(c for c in again["nodes"][0]["children"] if c["type"] == "Button")
    assert again_button["props"]["action"] == button["props"]["action"]


async def test_a_render_handler_that_draws_nothing_is_not_an_error(plugin):
    """Cache-meter and friends register `ui.render` for other components."""
    served = await plugin.render_slot("Sidebar")
    assert served["nodes"] == []


async def test_turn_boundaries_push_the_slot_once_per_change(plugin, monkeypatch, tmp_path):
    """The band is pushed on turn boundaries, and only when the tree changed."""
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    frames: list[dict] = []

    class _Bus:
        def emit(self, topic, payload):
            frames.append({"topic": topic, **payload})

    plugin.context = type("C", (), {"agent_id": "a1", "event_bus": _Bus()})()

    turn = {"task_id": "t1", "source": "web", "agent_id": "a1", "sid": "s1"}
    await plugin.on_task_start(dict(turn))
    slots = [f for f in frames if f["topic"] == "mod_slot"]
    assert len(slots) == 1
    # `turn.complete` with an unchanged tree must not re-emit.
    await plugin.on_task_complete({**turn, "completion_status": "completed", "turns": 1})
    assert len([f for f in frames if f["topic"] == "mod_slot"]) == 1

    # Change the mod's state → the band changes → the next boundary re-emits.
    await plugin.on_before_tool(_ctx("demo.remember"))
    await plugin.on_task_complete({**turn, "completion_status": "completed", "turns": 1})
    slots = [f for f in frames if f["topic"] == "mod_slot"]
    assert len(slots) == 2

    assert slots[0]["data"]["slot"] == "AbovePrompt"
    assert slots[0]["agent_id"] == "a1"
    # The bus envelope is what the relay routes on — a bare payload loses the sid.
    # Live UI events must carry their session, or they land in the focused pane.
    assert {f["sid"] for f in slots} == {"s1"}
    assert slots[0]["data"]["nodes"][0]["type"] == "Box"
    assert "runs=0" in json.dumps(slots[0]["data"]["nodes"])
    assert "runs=1" in json.dumps(slots[1]["data"]["nodes"])


async def test_slot_is_not_pushed_without_a_session_id(plugin, monkeypatch, tmp_path):
    """No sid → no frame. Emitting one would route it to whichever pane is focused."""
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    frames: list[dict] = []
    plugin.context = type(
        "C",
        (),
        {
            "agent_id": "a1",
            "event_bus": type(
                "B", (), {"emit": lambda s, topic, payload: frames.append({"topic": topic, **payload})}
            )(),
        },
    )()
    await plugin.on_task_start({"task_id": "t1", "agent_id": "a1"})  # no sid
    assert [f for f in frames if f["topic"] == "mod_slot"] == []


async def test_slot_is_not_pushed_when_no_mod_is_enabled(tmp_path):
    """No enabled mods must mean no frames at all — the band stays invisible."""
    instance = ModsHostPlugin(None)
    frames: list[dict] = []
    instance.context = type(
        "C", (), {"agent_id": "a1", "event_bus": type("B", (), {"emit": lambda s, t, p: frames.append(p)})()}
    )()
    await instance.on_task_start({"task_id": "t1"})
    await instance.on_task_complete({"task_id": "t1"})
    assert frames == []


async def test_the_host_cwd_is_pinned(tmp_path):
    """A mod's relative write must not land in whatever cwd the agent had."""
    pinned = NodeHostClient(cwd=str(tmp_path))
    try:
        await pinned.start()
        assert pinned._cwd == str(tmp_path)
    finally:
        pinned.close_sync()

    unpinned = NodeHostClient()
    assert unpinned._cwd == "", "an unpinned client must be detectable, not silently inherited"


async def test_a_button_press_runs_the_mod_callback_and_redraws(plugin, monkeypatch, tmp_path):
    """The full press round-trip: UI id → adapter command → bus → host → mod."""
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    frames: list[dict] = []
    plugin.context = type(
        "C",
        (),
        {
            "agent_id": "a1",
            "event_bus": type(
                "B", (), {"emit": lambda s, topic, payload: frames.append({"topic": topic, **payload})}
            )(),
        },
    )()

    # The id the host minted for the fixture's only Button.
    await plugin.on_task_start({"task_id": "t1", "agent_id": "a1", "sid": "s1"})
    first_slots = [f for f in frames if f["topic"] == "mod_slot"]
    assert first_slots and first_slots[0]["data"]["nodes"][0]["children"][-1]["props"]["action"] == "AbovePrompt:1"

    await plugin._on_mod_action({"action": "AbovePrompt:1", "sid": "s1"})
    assert mods_compat.read_mod_store(MOD_NAME).get("pinged") == 1
    # The band draws `pinged`, so the press changed the tree → redrawn, same session.
    slots = [f for f in frames if f["topic"] == "mod_slot"]
    assert len(slots) == 2
    assert slots[1]["sid"] == "s1"
    assert "pinged=1" in json.dumps(slots[1]["data"]["nodes"])


async def test_an_unknown_action_id_does_not_raise(plugin):
    await plugin._on_mod_action({"action": "AbovePrompt:99", "sid": "s1"})


async def test_turn_step_fires_once_per_tool_round(plugin, monkeypatch, tmp_path):
    """`turn.step` is a tick per tool round — not Claude Code's streaming generator."""
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    sent: list[tuple[str, dict]] = []
    plugin._emit = lambda event, payload: sent.append((event, payload)) or asyncio.sleep(0)  # type: ignore[assignment]
    plugin._push_slot = lambda *a, **k: asyncio.sleep(0)  # type: ignore[assignment]

    await plugin.on_task_start({"task_id": "t1", "agent_id": "a1", "sid": "s1"})
    await plugin.on_after_tool({"tool_name": "read_file", "agent_id": "a1", "sid": "s1"})
    await plugin.on_after_tool({"tool_name": "write_file", "agent_id": "a1", "sid": "s1"})

    steps = [p for e, p in sent if e == "turn.step"]
    assert [s["step"] for s in steps] == [1, 2]
    assert [s["tool_name"] for s in steps] == ["read_file", "write_file"]
    assert {s["sid"] for s in steps} == {"s1"}


async def test_session_id_is_remembered_and_served(plugin):
    """`$.session.id` comes from the hook context, which is where sid lives."""
    assert plugin._session_id == ""
    await plugin.on_task_start({"task_id": "t1", "agent_id": "a1", "sid": "s9"})
    assert plugin._session_id == "s9"
    served = await plugin._on_host_request("gate.session.id", {})
    assert served == {"id": "s9"}
    assert plugin._gate_calls.get("gate.session.id") == 1


async def test_session_start_fires_once_and_end_is_a_notification(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    client = await plugin._ensure_client()
    requested: list[str] = []
    notifications: list[str] = []
    original_request = client.request

    async def spy(method, params=None, **kwargs):
        requested.append(method)
        return await original_request(method, params, **kwargs)

    monkeypatch.setattr(client, "request", spy)
    monkeypatch.setattr(client, "send_notification", lambda m, p=None: notifications.append(m))

    await plugin._ensure_init(client)
    await plugin._ensure_init(client)  # same host generation → no second start
    assert requested.count("session.start") == 1

    plugin.on_unload()
    assert notifications == ["session.end"]


async def test_mod_command_is_registered_listed_and_withdrawn(plugin):
    """`$.command.register` / `list` — the registry a mod contributes to."""
    from opensquad.cli import slash_commands as sc

    try:
        assert sc.resolve_command("/replay") is None

        registered = await plugin._on_host_request(
            "gate.command.register",
            {"_mod": MOD_NAME, "name": "replay", "description": "step through the last turn's edits"},
        )
        assert registered["command"] == {"name": "replay", "source": MOD_NAME}
        assert sc.resolve_command("/replay") is not None
        assert sc.command_source("/replay") == MOD_NAME

        listed = await plugin._on_host_request("gate.command.list", {"_mod": MOD_NAME})
        by_name = {c["name"]: c for c in listed["commands"]}
        assert by_name["replay"]["source"] == MOD_NAME
        assert by_name["replay"]["help"]
        # Builtins are listed too, and labelled as such — quick-buttons filters on this.
        assert by_name["help"]["source"] == "builtin"

        # A bad name is refused, not registered.
        bad = await plugin._on_host_request("gate.command.register", {"_mod": MOD_NAME, "name": "two words"})
        assert "error" in bad

        plugin.on_unload()
        assert sc.resolve_command("/replay") is None, "a command must not outlive its mod"
    finally:
        sc.clear_runtime_commands()


async def test_registering_a_command_announces_it_for_the_ui(plugin, monkeypatch, tmp_path):
    """The composer's menu is fed by a `mod_commands` frame, not by asking Python."""
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    announced: list[tuple[str, dict]] = []
    plugin.context = type(
        "C",
        (),
        {
            "agent_id": "a1",
            "event_bus": type("B", (), {"emit": lambda s, topic, payload: announced.append((topic, payload))})(),
        },
    )()

    await plugin._on_host_request(
        "gate.command.register", {"_mod": MOD_NAME, "name": "demo", "description": "say hello"}
    )
    topics = [t for t, _ in announced]
    assert "mod_commands" in topics, f"the UI must learn about the command, saw {topics}"
    payload = next(p for t, p in announced if t == "mod_commands")
    names = [c["name"] for c in payload["data"]["commands"]]
    assert names == ["demo"], "only mod-owned commands travel — builtins are never announced"
    assert payload["data"]["commands"][0]["source"] == MOD_NAME


async def test_command_run_dispatches_to_the_mod_verdict(plugin, monkeypatch, tmp_path):
    """`$.command.run` → host `command.dispatch` → the mod's `command.run` handler."""
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    served = await plugin._on_host_request("gate.command.run", {"_mod": MOD_NAME, "name": "demo", "args": ["a", "b"]})
    assert served["verdict"] == {"text": "deny-demo: hello (args=a|b)"}
    assert plugin._gate_calls.get("gate.command.run") == 1

    missing = await plugin._on_host_request("gate.command.run", {"_mod": MOD_NAME, "name": ""})
    assert "error" in missing


async def test_a_user_command_is_run_and_spoken(plugin, monkeypatch, tmp_path):
    """The bus path a surface uses: `mod_command` in → `to_user_final` out."""
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    spoken: list[tuple[str, dict]] = []
    plugin.context = type(
        "C",
        (),
        {
            "agent_id": "a1",
            "event_bus": type("B", (), {"emit": lambda s, topic, payload: spoken.append((topic, payload))})(),
        },
    )()
    await plugin._ensure_init(await plugin._ensure_client())

    await plugin._on_mod_command({"name": "demo", "args": [], "sid": "s1"})

    topics = [t for t, _ in spoken]
    assert "to_user_final" in topics, f"the mod's answer must be spoken, saw {topics}"
    _, payload = next(p for p in spoken if p[0] == "to_user_final")
    assert payload["data"] == "deny-demo: hello (args=)"
    assert payload["sid"] == "s1"


async def test_a_command_that_says_nothing_is_not_spoken(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    spoken: list[str] = []
    plugin.context = type(
        "C",
        (),
        {"agent_id": "a1", "event_bus": type("B", (), {"emit": lambda s, topic, payload: spoken.append(topic)})()},
    )()
    await plugin._ensure_init(await plugin._ensure_client())

    await plugin._on_mod_command({"name": "no-such-command", "sid": "s1"})
    assert [topic for topic in spoken if topic == "to_user_final"] == []


async def test_typing_a_mod_command_runs_it_and_stops_the_turn(plugin, monkeypatch, tmp_path):
    """One interception covers every surface: the text arrives as a message."""
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    spoken: list[tuple[str, dict]] = []
    plugin.context = type(
        "C",
        (),
        {
            "agent_id": "a1",
            "event_bus": type("B", (), {"emit": lambda s, topic, payload: spoken.append((topic, payload))})(),
        },
    )()
    await plugin._ensure_init(await plugin._ensure_client())  # registers /demo

    ctx = {"message": "/demo one two", "source_chat_id": "c1", "sid": "s7"}
    out = await plugin.on_message_received(dict(ctx))
    assert out["__stop__"] is True, "the LLM must not also see this as user text"
    assert [t for t, _ in spoken if t == "to_user_final"] == ["to_user_final"]
    payload = next(p for t, p in spoken if t == "to_user_final")
    assert payload["data"] == "deny-demo: hello (args=one|two)"
    assert payload["sid"] == "s7"


async def test_a_builtin_or_unknown_slash_message_is_left_alone(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    spoken: list[str] = []
    plugin.context = type(
        "C",
        (),
        {"agent_id": "a1", "event_bus": type("B", (), {"emit": lambda s, topic, payload: spoken.append(topic)})()},
    )()
    await plugin._ensure_init(await plugin._ensure_client())

    # A builtin command, an unknown one, and ordinary text all pass through.
    for message in ("/help", "/definitely-not-a-command", "hello /demo"):
        out = await plugin.on_message_received({"message": message, "sid": "s7"})
        assert "__stop__" not in out, message
    assert "to_user_final" not in spoken, "nothing may be spoken for a message we did not handle"


async def test_modal_state_is_per_mod_and_survives_across_calls(plugin):
    """`$.state` lives in the host: agent-scoped, not session-scoped."""
    first = await plugin.on_before_tool(_ctx("demo.state"))
    assert "state=1" in first["result"]
    assert "counter" in first["result"] and "label" in first["result"]

    second = await plugin.on_before_tool(_ctx("demo.state"))
    assert "state=2" in second["result"], "state must not reset between calls"


async def test_fs_list_and_stat_are_confined_and_capped(plugin, monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "a.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(plugin, "_workspace", lambda: str(ws))

    listed = await plugin._on_host_request("gate.fs.list", {"path": "."})
    by_name = {e["name"]: e for e in listed["entries"]}
    assert by_name["a.txt"]["type"] == "file" and by_name["a.txt"]["size"] == 5
    assert by_name["sub"]["type"] == "dir"

    stat = await plugin._on_host_request("gate.fs.stat", {"path": "a.txt"})
    assert stat["stat"]["exists"] is True and stat["stat"]["type"] == "file"
    missing = await plugin._on_host_request("gate.fs.stat", {"path": "nope.txt"})
    assert missing["stat"] == {"exists": False}

    # Listing a file is an error, and escaping the workspace is refused.
    assert "error" in await plugin._on_host_request("gate.fs.list", {"path": "a.txt"})
    assert "error" in await plugin._on_host_request("gate.fs.list", {"path": os.path.join("..", "..")})


async def test_fs_list_is_capped(plugin, monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    for i in range(12):
        (ws / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(plugin, "_workspace", lambda: str(ws))
    monkeypatch.setattr("plugins.mods_host.plugin._FS_LIST_LIMIT", 5)

    listed = await plugin._on_host_request("gate.fs.list", {"path": "."})
    assert len(listed["entries"]) == 5


# ── TypeScript + the claude-code SDK shim (P6-B1) ──────────────────────────


async def test_a_typescript_mod_loads_and_its_sdk_import_resolves(tmp_path):
    """`.ts` runs on Node's type stripping; `claude-code` is answered by the host."""
    state_root = str(tmp_path / "state")
    mods_compat.write_mod_state("ts-demo", enabled=True, state_root=state_root)
    instance = ModsHostPlugin(None)
    instance._mods = instance._load_mods(root=FIXTURE_ROOT, state_root=state_root)
    assert [m["name"] for m in instance._mods] == ["ts-demo"], "a .ts module must be loadable"
    assert instance._mods[0]["modules"] == ["hooks/register.ts"]

    try:
        first = await instance.on_before_tool(_ctx("demo.typed"))
        assert first["skip"] is True
        assert "hits 0 -> 1" in first["result"]
        second = await instance.on_before_tool(_ctx("demo.typed"))
        assert "hits 1 -> 2" in second["result"], "the SDK shim's atom must persist"
        assert "seen 0 -> 1" in first["result"] and "seen 1 -> 2" in second["result"], (
            "`memberOf` must resolve — a missing export fails the whole import, which would "
            "silently cost every handler this mod registers"
        )
        assert "elsewhere 0" in first["result"] and "elsewhere 0" in second["result"], (
            "member cells are per member: a shared cell would have leaked the count"
        )

        # `.ts` is transpiled too (not left to Node's native stripping, which needs
        # Node >=22.6): one code path for both extensions, one floor for the host.
        build = os.path.join(FIXTURE_ROOT, "ts-demo", "hooks", "register.mods-build.mjs")
        assert os.path.isfile(build), "`.ts` must be transpiled through the vendored sucrase"
    finally:
        if os.path.isfile(build):
            os.remove(build)
        instance.on_unload()


def test_the_scanner_accepts_both_ts_and_tsx():
    """`.ts` runs natively; `.tsx` is transpiled on load — neither is a gap now."""
    for name, module in (("ts-demo", "hooks/register.ts"), ("tsx-demo", "hooks/register.tsx")):
        info = mods_compat.scan_mod(os.path.join(FIXTURE_ROOT, name))
        assert info["modules"] == [module], (name, info["modules"])
        assert not any(b["kind"] == "module" for b in info["blocked_by"]), name

    tsx_info = mods_compat.scan_mod(os.path.join(FIXTURE_ROOT, "tsx-demo"))
    assert any("JSX" in note for note in tsx_info["notes"]), "the JSX build step must be stated"


async def test_a_tsx_mod_is_transpiled_on_load_and_draws(tmp_path):
    """P6-B2: JSX is rewritten on the way in, and the tree is a normal one."""
    state_root = str(tmp_path / "state")
    mods_compat.write_mod_state("tsx-demo", enabled=True, state_root=state_root)
    instance = ModsHostPlugin(None)
    instance._mods = instance._load_mods(root=FIXTURE_ROOT, state_root=state_root)
    assert [m["name"] for m in instance._mods] == ["tsx-demo"]

    try:
        # `render_slot` drives the handshake, so the init result is available after.
        drawn = await instance.render_slot("AbovePrompt")
        init = instance._last_init or {}
        assert init.get("diagnostics") == [], init.get("diagnostics")
        assert init.get("registered") == 2

        assert len(drawn["nodes"]) == 1
        root = drawn["nodes"][0]
        assert root["type"] == "Box" and root["props"]["flexDirection"] == "row"
        assert root["children"][0]["children"] == ["tsx-demo hits=0"]

        # The build artefact is written next to the source (so the mod's own
        # relative imports still resolve) and reused on a second load.
        build = os.path.join(FIXTURE_ROOT, "tsx-demo", "hooks", "register.mods-build.mjs")
        assert os.path.isfile(build), "JSX must have been transpiled on load"
        try:
            assert instance._client is not None
            inited = await instance._client.request(
                "init",
                {
                    "mods": [
                        {
                            "name": "tsx-demo",
                            "dir_name": "tsx-demo",
                            "root": instance._mods[0]["root"],
                            "modules": ["hooks/register.tsx"],
                        }
                    ]
                },
            )
            assert inited.get("diagnostics") == []
        finally:
            if os.path.isfile(build):
                os.remove(build)
    finally:
        instance.on_unload()


# ── gated capabilities: default-deny + per-mod grant (P5) ──────────────────


async def test_every_gated_capability_is_denied_by_default(plugin, monkeypatch, tmp_path):
    """Default-deny is an API contract (not a sandbox) — a mod must get a nameable answer."""
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    calls = [
        ("gate.fs.write", {"path": "x.txt", "text": "hi"}),
        ("gate.process.run", {"command": "echo", "args": ["hi"]}),
        ("gate.http.fetch", {"url": "https://example.com"}),
        ("gate.env.set", {"key": "K", "value": "v"}),
    ]
    for method, params in calls:
        served = await plugin._on_host_request(method, {**params, "_mod": MOD_NAME})
        assert "未授权" in served["error"], (method, served)
        assert "Mods" in served["error"], "the refusal must say where to grant it"


async def test_fs_write_after_a_grant_stays_confined(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(plugin, "_workspace", lambda: str(ws))
    mods_compat.write_mod_permissions(MOD_NAME, granted=["fs.write"], state_root=str(tmp_path / "data"))

    ok = await plugin._on_host_request("gate.fs.write", {"_mod": MOD_NAME, "path": "out.txt", "text": "hello"})
    assert ok["ok"] is True and ok["bytes"] == 5
    assert (ws / "out.txt").read_text(encoding="utf-8") == "hello"

    escaped = await plugin._on_host_request(
        "gate.fs.write", {"_mod": MOD_NAME, "path": str(tmp_path / "outside.txt"), "text": "no"}
    )
    assert "error" in escaped and "outside" in escaped["error"]
    assert not (tmp_path / "outside.txt").exists()

    huge = await plugin._on_host_request(
        "gate.fs.write", {"_mod": MOD_NAME, "path": "big.txt", "text": "x" * (5 * 1024 * 1024)}
    )
    assert "上限" in huge["error"]


async def test_process_run_after_a_grant_uses_an_argv_list(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    mods_compat.write_mod_permissions(MOD_NAME, granted=["process.run"], state_root=str(tmp_path / "data"))

    ran = await plugin._on_host_request(
        "gate.process.run",
        {"_mod": MOD_NAME, "command": sys.executable, "args": ["-c", "print('from-mod')"]},
    )
    assert ran["code"] == 0 and "from-mod" in ran["stdout"]

    slow = await plugin._on_host_request(
        "gate.process.run",
        {"_mod": MOD_NAME, "command": sys.executable, "args": ["-c", "import time; time.sleep(5)"], "timeout_ms": 1000},
    )
    assert "上限" in slow["error"]


async def test_http_fetch_requires_the_host_to_be_allowlisted(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    mods_compat.write_mod_permissions(
        MOD_NAME, granted=["http.fetch"], domains=["example.com"], state_root=str(tmp_path / "data")
    )

    blocked = await plugin._on_host_request("gate.http.fetch", {"_mod": MOD_NAME, "url": "https://evil.test/x"})
    assert "白名单" in blocked["error"] and "evil.test" in blocked["error"]

    # An allowlisted host goes through the (mocked) fetch, so no network in tests.
    monkeypatch.setattr("plugins.mods_host.plugin._http_fetch_capped", lambda *a: {"status": 200, "text": "ok"})
    allowed = await plugin._on_host_request("gate.http.fetch", {"_mod": MOD_NAME, "url": "https://example.com/x"})
    assert allowed == {"status": 200, "text": "ok"}


async def test_env_set_after_a_grant_reports_its_real_scope(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    mods_compat.write_mod_permissions(MOD_NAME, granted=["env.set"], state_root=str(tmp_path / "data"))

    served = await plugin._on_host_request(
        "gate.env.set", {"_mod": MOD_NAME, "key": "MODS_HOST_TEST_ENV", "value": "1"}
    )
    assert served["ok"] is True
    assert "agent" in served["scope"], "the change is process-wide and must say so"
    assert os.environ.get("MODS_HOST_TEST_ENV") == "1"
    os.environ.pop("MODS_HOST_TEST_ENV", None)

    bad = await plugin._on_host_request("gate.env.set", {"_mod": MOD_NAME, "key": "A=B", "value": "x"})
    assert "error" in bad


def test_permissions_round_trip_ignores_unknown_capabilities(tmp_path):
    state_root = str(tmp_path / "data")
    written = mods_compat.write_mod_permissions(
        "m", granted=["fs.write", "not-a-capability", "process.run"], domains=["Example.COM"], state_root=state_root
    )
    assert written["granted"] == ["fs.write", "process.run"]
    assert written["domains"] == ["example.com"]
    assert mods_compat.read_mod_permissions("m", state_root)["granted"] == ["fs.write", "process.run"]


async def test_gate_rejects_an_unknown_method(plugin):
    with pytest.raises(ValueError):
        await plugin._on_host_request("gate.session.authorize", {})


async def test_other_tools_pass_through_untouched(plugin):
    ctx = await plugin.on_before_tool(_ctx("read_file", {"path": "x"}))
    assert ctx == _ctx("read_file", {"path": "x"})


async def test_same_tool_twice_is_still_denied(plugin):
    """The host must stay usable across calls (init happens exactly once)."""
    for _ in range(3):
        ctx = await plugin.on_before_tool(_ctx("demo.echo"))
        assert ctx["skip"] is True


# ── gap reporting & degradation ────────────────────────────────────────────


async def test_missing_module_is_named_at_load_time(plugin):
    """Matrix §7: a gap is reported when the mod loads, not mid-run."""
    client = await plugin._ensure_client()
    result = await client.request(
        "init",
        {"mods": [{"name": "ghost-mod", "root": FIXTURE_ROOT, "modules": ["hooks/nope.mjs"]}]},
    )
    kinds = [d["kind"] for d in result["diagnostics"]]
    assert "import_failed" in kinds
    assert any("nope.mjs" in d["detail"] for d in result["diagnostics"])


async def test_missing_node_fails_open_and_is_visible(plugin, monkeypatch, caplog, tmp_path):
    """docs/mods-bridge-m0.md §3.1 — fail-open is only safe while it is *visible*.

    The host lives in the agent process, so the Mods page cannot probe it; the
    journal is the only reason a degradation is not a silent bypass.
    """
    from plugins.mods_host import _rpc

    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))

    def _no_node_please():
        raise _rpc.NodeHostUnavailableError("node runtime not found")

    monkeypatch.setattr(plugin, "_ensure_client", _no_node_please)
    ctx = _ctx("demo.echo")
    with caplog.at_level(logging.WARNING, logger="plugins.mods_host"):
        assert await plugin.on_before_tool(dict(ctx)) == ctx
    assert "degraded (fail-open" in caplog.text

    observations = mods_compat.read_host_observations()
    assert observations, "a degradation must leave a readable trace"
    assert observations[0]["state"] == "degraded"
    assert "node runtime not found" in observations[0]["last_failure"]


async def test_hook_is_discovered_and_runs_through_plugin_manager(plugin):
    """The real integration seam: our handler inside `plugin_manager.run_hook`.

    `_apply_tool_call` being correct is not enough — the decorated method has to
    be discovered as a hook, and the ctx mutation has to survive the chain
    (including the framework's `asyncio.wait_for(..., 10.0)` wrapper).
    """
    from opensquad.plugin_api import get_hook_methods
    from plugins.plugin_manager import PluginManager

    hooks = get_hook_methods(plugin)
    assert "on_before_tool" in hooks, "plugin_manager builds its chain from hook_map"
    assert hooks["on_before_tool"] == [plugin.on_before_tool]

    manager = PluginManager(agent_id="test-agent")
    manager._plugins["mods_host"] = {"plugin": plugin, "hook_map": hooks}
    manager._hook_chain_cache.pop("on_before_tool", None)

    ctx = await manager.run_hook("on_before_tool", _ctx("demo.echo"))
    assert ctx["skip"] is True
    assert ctx["result"].startswith("Error: deny-demo:")


async def test_a_killed_host_is_restarted_with_its_mods(plugin, monkeypatch, tmp_path):
    """Recovery, not just degradation: a dead host must come back *loaded*.

    The restarted process has none of the mods and no handshake, so the plugin's
    `init` cache has to be keyed on the host generation — object identity survives
    the restart and would happily skip re-initialising.
    """
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    client = await plugin._ensure_client()
    dead_pid = client.pid
    assert dead_pid is not None
    assert client.generation == 1

    client._proc.kill()
    for _ in range(50):
        if not client.is_running():
            break
        await asyncio.sleep(0.02)

    ctx = await plugin.on_before_tool(_ctx("demo.echo"))
    assert ctx["skip"] is True, "the restarted host must still have its mods"
    assert plugin._client.generation == 2
    assert client.pid != dead_pid

    try:
        import psutil
    except ImportError:  # pragma: no cover
        return
    assert not psutil.pid_exists(dead_pid), f"killed host pid {dead_pid} lingers"


async def test_unload_leaves_no_host_behind(plugin):
    client = await plugin._ensure_client()
    pid = client.pid
    plugin.on_unload()
    assert plugin._client is None
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return
    assert not psutil.pid_exists(pid)


async def test_plugin_is_inert_when_nothing_is_enabled(tmp_path):
    instance = ModsHostPlugin(None)
    ctx = _ctx("demo.echo")
    assert await instance.on_before_tool(dict(ctx)) == ctx
    assert instance._client is None, "no enabled mod must never start a node process"


# ── the action registry is per surface, not per render ─────────────────────


def _first_button_action(nodes: list) -> str:
    stack = list(nodes)
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        if item.get("type") == "Button":
            action = (item.get("props") or {}).get("action")
            if isinstance(action, str) and action:
                return action
        stack.extend(item.get("children") or [])
    return ""


async def test_a_button_survives_another_slot_being_drawn(tmp_path):
    """A later `ui.render` must not invalidate the surfaces already drawn.

    The plugin pushes several slots per hook — `AbovePrompt`, then each open
    pane, then `AssistantMessage`. When the host rebuilt one global action
    registry on every render, the earlier slots' buttons answered
    `unknown_action`; and because an unchanged tree is deduped by the plugin and
    never redrawn, they stayed dead for the rest of the turn instead of
    recovering. Live, that reads as "the button does nothing".
    """
    state_root = str(tmp_path / "state")
    mods_compat.write_mod_state("deny-demo", enabled=True, state_root=state_root)
    instance = ModsHostPlugin(None)
    instance._mods = instance._load_mods(root=FIXTURE_ROOT, state_root=state_root)
    assert [m["name"] for m in instance._mods] == ["deny-demo"]

    try:
        first = await instance.render_slot("AbovePrompt")
        action = _first_button_action(first["nodes"])
        assert action, f"the fixture must draw a Button, got {first['nodes']}"

        # Drawing any other surface is what used to wipe the registry.
        await instance.render_slot("AssistantMessage")

        verdict = await instance.invoke_action(action)
        assert verdict and verdict.get("verdict") == "pong", verdict
    finally:
        instance.on_unload()
