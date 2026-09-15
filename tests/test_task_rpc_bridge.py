"""Cross-process task RPC — the M2 split-deployment data path.

The Gateway hosts ``/api/tasks``; the ``TaskScheduler`` lives in the *agent*
process. Every op therefore travels the agent WebSocket as a
``type=command, command=task_rpc`` frame and comes back as a
``task_command_result`` frame correlated by ``req_id``.

Before this existed the gateway read ``get_scheduler()`` in its own process —
a lazily created empty shell — so the panel showed ``tasks: []`` forever and
submit silently never ran.

These tests pin the four joints of that path:

1. ``opensquad.tasks.rpc.dispatch_task_op`` — the op table and its ``ok``
   contract (shared by agent server and gateway fallback);
2. ``AgentTaskRPCBridge`` — request/response correlation, agent scoping,
   cancellation, timeout, unreachable agent;
3. ``gateway_adapter`` — the agent must actually *have* the ``task_rpc``
   command branch and answer with ``task_command_result``;
4. ``tasks_api`` — which process an op is routed to, and the HTTP status it
   produces when neither end can serve it.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import re
import types

import pytest

_BACKEND_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "src", "opensquad", "gateway", "backend")
)
if _BACKEND_ROOT not in os.sys.path:
    os.sys.path.insert(0, _BACKEND_ROOT)

from app import tasks_api  # noqa: E402
from app.ai_web import websocket as ws_mod  # noqa: E402
from app.ai_web.websocket import AgentTaskRPCBridge  # noqa: E402

from opensquad.tasks.rpc import TASK_RPC_OPS, dispatch_task_op  # noqa: E402

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WS_SRC = _REPO_ROOT / "src" / "opensquad" / "gateway" / "backend" / "app" / "ai_web" / "websocket.py"
ADAPTER_SRC = _REPO_ROOT / "src" / "opensquad" / "gateway_adapter.py"


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------


class _StubScheduler:
    """Duck-typed TaskScheduler stand-in — records calls, injects failures."""

    def __init__(self, *, raise_on=frozenset(), limit_on=frozenset()):
        self.calls: list[tuple] = []
        self.raise_on = set(raise_on)
        self.limit_on = set(limit_on)
        self._tasks = {"t1": {"task_id": "t1", "agent_id": "a1", "status": "running"}}

    def list_tasks(self, agent_id=None):
        self.calls.append(("list", agent_id))
        if "list" in self.raise_on:
            raise OSError("registry exploded")
        tasks = list(self._tasks.values())
        if agent_id:
            tasks = [t for t in tasks if t["agent_id"] == agent_id]
        return tasks

    def get_task(self, task_id):
        self.calls.append(("get", task_id))
        return self._tasks.get(task_id)

    def submit(self, **kw):
        self.calls.append(("submit", kw))
        if "submit" in self.limit_on:
            raise RuntimeError("Task limit reached for agent a1 (5 active)")
        return {"task_id": "tnew", "status": "queued", **kw}

    def abort(self, task_id):
        self.calls.append(("abort", task_id))
        if task_id not in self._tasks:
            return {"status": "error", "message": f"unknown task {task_id}"}
        return {"status": "ok", "task": {"task_id": task_id, "status": "aborted"}}

    def set_approval(self, task_id, approved):
        self.calls.append(("approve", task_id, approved))
        return {"status": "ok", "task": {"task_id": task_id, "approved": approved}}

    def remove(self, task_id):
        self.calls.append(("remove", task_id))
        if task_id not in self._tasks:
            return {"status": "error", "message": "unknown task"}
        return {"status": "ok"}


class _FakeWS:
    """Minimal agent socket: records outbound frames, optionally replies."""

    def __init__(self, responder=None):
        self.sent: list[dict] = []
        self._responder = responder

    async def send_text(self, raw: str):
        frame = json.loads(raw)
        self.sent.append(frame)
        if self._responder is not None:
            self._responder(frame)


def _fake_ws_module(*, connections=None, rpc=None, resolver=None):
    """Stand-in for ``app.ai_web.websocket`` as seen by tasks_api."""
    mod = types.SimpleNamespace()
    mod.registry = types.SimpleNamespace(connections=connections if connections is not None else {})
    mod._resolve_registered_agent_id = resolver or (lambda a: a)
    mod.task_rpc_bridge = types.SimpleNamespace(rpc=rpc)
    return mod


# ---------------------------------------------------------------------------
# 1. dispatch_task_op — the shared op table
# ---------------------------------------------------------------------------


class TestDispatchTaskOp:
    def test_op_table_covers_the_rest_surface(self):
        # Every op the /api/tasks routes can ask for must be dispatchable.
        assert set(TASK_RPC_OPS) == {
            "list",
            "get",
            "submit",
            "abort",
            "approve",
            "remove",
            "resume",  # M3 — continue a parked goal from its checkpoint
        }

    def test_list_passes_agent_filter(self):
        sched = _StubScheduler()
        out = dispatch_task_op(sched, "list", {"agent_id": "a1"})
        assert out == {"ok": True, "tasks": [{"task_id": "t1", "agent_id": "a1", "status": "running"}]}
        assert sched.calls == [("list", "a1")]

    def test_list_without_filter_passes_none(self):
        sched = _StubScheduler()
        dispatch_task_op(sched, "list", {})
        assert sched.calls == [("list", None)]

    def test_get_returns_task(self):
        out = dispatch_task_op(_StubScheduler(), "get", {"task_id": "t1"})
        assert out["ok"] is True
        assert out["task"]["task_id"] == "t1"

    def test_get_missing_is_not_found(self):
        out = dispatch_task_op(_StubScheduler(), "get", {"task_id": "nope"})
        assert out["ok"] is False
        # The gateway maps error_kind -> HTTP 404 rather than a blanket 409.
        assert out["error_kind"] == "not_found"

    def test_submit_forwards_every_field(self):
        sched = _StubScheduler()
        out = dispatch_task_op(
            sched,
            "submit",
            {
                "title": "T",
                "prompt": "P",
                "agent_id": "a1",
                "origin": "manual",
                "base_dir": "/tmp/x",
                "use_worktree": False,
            },
        )
        assert out["ok"] is True
        assert out["task"]["title"] == "T"
        op, kw = sched.calls[0]
        assert op == "submit"
        assert kw == {
            "title": "T",
            "prompt": "P",
            "agent_id": "a1",
            "origin": "manual",
            "base_dir": "/tmp/x",
            "use_worktree": False,
            # M3 additions — a plain submission carries no goal plan.
            "kind": "task",
            "plan": {},
        }

    def test_submit_defaults_are_stable(self):
        sched = _StubScheduler()
        dispatch_task_op(sched, "submit", {"title": "T", "prompt": "P"})
        _, kw = sched.calls[0]
        assert kw["origin"] == "manual"
        assert kw["use_worktree"] is True  # isolation is on unless asked off
        assert kw["base_dir"] == ""

    def test_submit_limit_becomes_error_kind(self):
        sched = _StubScheduler(limit_on={"submit"})
        out = dispatch_task_op(sched, "submit", {"title": "T", "prompt": "P"})
        assert out["ok"] is False
        assert out["error_kind"] == "limit"
        assert "limit reached" in out["error"]

    @pytest.mark.parametrize(
        "op,params",
        [
            ("abort", {"task_id": "t1"}),
            ("approve", {"task_id": "t1", "approved": True}),
            ("remove", {"task_id": "t1"}),
        ],
    )
    def test_status_style_ops_are_normalised_to_ok(self, op, params):
        # The scheduler returns {"status": "ok"} for these while list/get/submit
        # signal success by not raising. The RPC frame must have ONE success
        # signal, otherwise the gateway has to know op-specific shapes.
        out = dispatch_task_op(_StubScheduler(), op, params)
        assert out["ok"] is True
        assert "status" not in out

    @pytest.mark.parametrize(
        "op,params,needle",
        [
            ("abort", {"task_id": "ghost"}, "unknown task"),
            ("remove", {"task_id": "ghost"}, "unknown task"),
        ],
    )
    def test_status_error_becomes_ok_false(self, op, params, needle):
        out = dispatch_task_op(_StubScheduler(), op, params)
        assert out["ok"] is False
        assert needle in out["error"]

    def test_approve_forwards_the_flag(self):
        sched = _StubScheduler()
        dispatch_task_op(sched, "approve", {"task_id": "t1", "approved": False})
        assert sched.calls == [("approve", "t1", False)]

    def test_unknown_op_is_reported_not_raised(self):
        out = dispatch_task_op(_StubScheduler(), "explode", {})
        assert out["ok"] is False
        assert "unknown op" in out["error"]

    def test_scheduler_exception_is_contained(self):
        # A raising scheduler must not be able to kill the agent's command loop.
        out = dispatch_task_op(_StubScheduler(raise_on={"list"}), "list", {})
        assert out["ok"] is False
        assert "registry exploded" in out["error"]

    @pytest.mark.parametrize("params", [None, "not-a-dict", 42])
    def test_non_dict_params_do_not_crash(self, params):
        out = dispatch_task_op(_StubScheduler(), "list", params)
        assert out["ok"] is True


# ---------------------------------------------------------------------------
# 2. AgentTaskRPCBridge — correlation over the agent channel
# ---------------------------------------------------------------------------


class TestAgentTaskRPCBridge:
    async def test_round_trip_resolves_by_req_id(self, monkeypatch):
        bridge = AgentTaskRPCBridge()
        fake = _FakeWS()
        monkeypatch.setitem(ws_mod.registry.connections, "a1", fake)

        call = asyncio.create_task(bridge.rpc("a1", "list", {"agent_id": "a1"}))
        await asyncio.sleep(0)  # let send_text run

        assert len(fake.sent) == 1
        frame = fake.sent[0]
        assert frame["type"] == "command"
        assert frame["command"] == "task_rpc"
        # `data` is what gateway_adapter.coerce_command_data() prefers.
        assert frame["data"]["op"] == "list"
        assert frame["data"]["params"] == {"agent_id": "a1"}
        req_id = frame["data"]["req_id"]
        assert req_id

        assert bridge.resolve("a1", {"req_id": req_id, "op": "list", "result": {"ok": True, "tasks": []}})
        envelope = await call
        assert envelope == {"req_id": req_id, "op": "list", "result": {"ok": True, "tasks": []}}
        assert bridge.pending_count() == 0

    async def test_resolve_is_scoped_to_the_agent(self):
        bridge = AgentTaskRPCBridge()
        fut = asyncio.get_running_loop().create_future()
        bridge._pending[bridge._key("a1", "r1")] = fut

        # Another agent must not be able to settle this request.
        assert bridge.resolve("a2", {"req_id": "r1"}) is False
        assert bridge.pending_count("a1") == 1
        assert bridge.resolve("a1", {"req_id": "r1"}) is True
        assert fut.result() == {"req_id": "r1"}

    async def test_resolve_unknown_req_id_is_false_not_an_error(self):
        bridge = AgentTaskRPCBridge()
        # Late / duplicate reply — no waiter. Must not raise inside the agent
        # message loop (that would unregister the agent).
        assert bridge.resolve("a1", {"req_id": "never-seen"}) is False
        assert bridge.resolve("a1", {}) is False

    async def test_resolve_rejects_a_second_reply_for_one_request(self):
        bridge = AgentTaskRPCBridge()
        fut = asyncio.get_running_loop().create_future()
        bridge._pending[bridge._key("a1", "r1")] = fut
        assert bridge.resolve("a1", {"req_id": "r1"}) is True
        assert bridge.resolve("a1", {"req_id": "r1"}) is False

    async def test_cancel_agent_fails_outstanding_calls(self):
        bridge = AgentTaskRPCBridge()
        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        f2 = loop.create_future()
        bridge._pending[bridge._key("a1", "r1")] = f1
        bridge._pending[bridge._key("a1", "r2")] = f2
        bridge._pending[bridge._key("a2", "r3")] = loop.create_future()

        assert bridge.cancel_agent("a1") == 2
        assert f1.cancelled() and f2.cancelled()
        # The other agent's request is untouched.
        assert bridge.pending_count("a2") == 1

    async def test_unreachable_agent_raises_502(self):
        from fastapi import HTTPException

        bridge = AgentTaskRPCBridge()
        with pytest.raises(HTTPException) as exc:
            await bridge.rpc("ghost", "list", {})
        assert exc.value.status_code == 502
        assert bridge.pending_count() == 0

    async def test_timeout_raises_504_and_leaks_nothing(self, monkeypatch):
        from fastapi import HTTPException

        bridge = AgentTaskRPCBridge()
        monkeypatch.setitem(ws_mod.registry.connections, "a1", _FakeWS())  # never answers
        with pytest.raises(HTTPException) as exc:
            await bridge.rpc("a1", "list", {}, timeout=0.05)
        assert exc.value.status_code == 504
        # A timed-out request must not stay in the map (it would leak per call).
        assert bridge.pending_count() == 0

    async def test_malformed_reply_is_a_502(self, monkeypatch):
        from fastapi import HTTPException

        bridge = AgentTaskRPCBridge()

        def responder(frame):
            req_id = frame["data"]["req_id"]
            asyncio.get_running_loop().call_soon(bridge.resolve, "a1", {"req_id": req_id, "result": "not-a-dict"})

        monkeypatch.setitem(ws_mod.registry.connections, "a1", _FakeWS(responder=responder))
        with pytest.raises(HTTPException) as exc:
            await bridge.rpc("a1", "list", {}, timeout=1.0)
        assert exc.value.status_code == 502
        assert bridge.pending_count() == 0


# ---------------------------------------------------------------------------
# 3. gateway message-loop gates (the "silently dropped frame" trap)
# ---------------------------------------------------------------------------


def _message_loop_node():
    tree = ast.parse(WS_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_agent_message_loop":
            return node
    raise AssertionError("_agent_message_loop not found in websocket.py")


def _membership_gate_types() -> set[str]:
    """String literals of the ``elif msg_type in [...]`` gate in the loop.

    Located structurally rather than by "first list literal mentioning X": the
    module-level ``_AGENT_OUTPUT_BROADCAST_TYPES`` frozenset is defined *earlier
    in the file*, so a text/regex match would silently attribute its contents to
    the gate and hide a missing entry (a false negative that already bit us).
    """
    loop = _message_loop_node()
    for node in ast.walk(loop):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "msg_type"):
            continue
        if not any(isinstance(op, ast.In) for op in node.ops):
            continue
        for comp in node.comparators:
            if not isinstance(comp, ast.List):
                continue
            vals = {e.value for e in comp.elts if isinstance(e, ast.Constant)}
            if "tool_call" in vals:  # stable anchor unique to the gate list
                return vals
    raise AssertionError("msg_type membership gate not found inside _agent_message_loop")


def _equality_gate_types() -> set[str]:
    loop = _message_loop_node()
    found: set[str] = set()
    for node in ast.walk(loop):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "msg_type"):
            continue
        if not any(isinstance(op, ast.Eq) for op in node.ops):
            continue
        for comp in node.comparators:
            if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                found.add(comp.value)
    return found


class TestMessageLoopGates:
    """Two gates must agree, or the task panel silently never updates."""

    def test_broadcast_set_has_the_task_events(self):
        broadcast = ws_mod._AGENT_OUTPUT_BROADCAST_TYPES
        assert "task_update" in broadcast
        assert "task_removed" in broadcast

    @pytest.mark.parametrize("event", ["task_update", "task_removed"])
    def test_loop_gate_has_the_task_events(self, event):
        # The frozenset only decides broadcast-vs-directed. This list is what
        # stops the frame falling through to
        # `logger.warning("Unknown message from agent ...")` and being dropped.
        assert event in _membership_gate_types(), (
            f"{event} is missing from the _agent_message_loop elif gate — "
            "the frame would be dropped silently and the UI would never update"
        )

    def test_broadcast_set_is_not_a_substitute_for_the_gate(self):
        # Guard against future drift in either direction: the two sets must not
        # be identical (the gate intentionally omits some broadcast types such
        # as turn_cancelled handling paths), so a test that only checks the
        # frozenset cannot stand in for this one.
        broadcast = ws_mod._AGENT_OUTPUT_BROADCAST_TYPES
        gate = _membership_gate_types()
        assert {"task_update", "task_removed"} <= (broadcast & gate)

    def test_task_command_result_is_intercepted_before_forwarding(self):
        assert "task_command_result" in _equality_gate_types()
        src = WS_SRC.read_text(encoding="utf-8")
        # And the branch must actually hand the frame to the bridge.
        assert "task_rpc_bridge.resolve(" in src

    def test_agent_unregister_cancels_pending_rpcs(self):
        src = WS_SRC.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_unregister_agent":
                body_src = ast.dump(node)
                assert "cancel_agent" in body_src, (
                    "_unregister_agent must cancel pending task RPCs, otherwise "
                    "every /api/tasks call waits out its full timeout after an "
                    "agent crash"
                )
                return
        raise AssertionError("_unregister_agent not found")


# ---------------------------------------------------------------------------
# 4. gateway_adapter — the agent-side server
# ---------------------------------------------------------------------------


def _adapter_fn(name: str):
    tree = ast.parse(ADAPTER_SRC.read_text(encoding="utf-8"))
    return next(
        (n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name),
        None,
    )


def _string_literals(node) -> set[str]:
    """Every string literal inside ``node``, compared as exact values.

    Exact values matter: a substring assertion would accept
    ``"task_command_resultx"`` for ``"task_command_result"`` and silently stop
    guarding the frame type (a false negative that already bit this file).
    """
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _bridge_rpc_strings() -> set[str]:
    tree = ast.parse(WS_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AgentTaskRPCBridge":
            for sub in ast.walk(node):
                if isinstance(sub, ast.AsyncFunctionDef) and sub.name == "rpc":
                    return _string_literals(sub)
    raise AssertionError("AgentTaskRPCBridge.rpc not found in websocket.py")


def _adapter_command_branches() -> set[str]:
    """String literals compared against ``command`` in _handle_command."""
    tree = ast.parse(ADAPTER_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle_command":
            out: set[str] = set()
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Compare):
                    continue
                if not (isinstance(sub.left, ast.Name) and sub.left.id == "command"):
                    continue
                for comp in sub.comparators:
                    if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                        out.add(comp.value)
            return out
    raise AssertionError("_handle_command not found in gateway_adapter.py")


class TestAgentSideHandler:
    def test_task_rpc_command_is_handled(self):
        branches = _adapter_command_branches()
        assert "task_rpc" in branches, (
            "agent must handle command=task_rpc; without it the gateway's RPC "
            "falls through to the base handler and every call times out"
        )

    def test_handler_answers_with_task_command_result(self):
        fn = _adapter_fn("_handle_task_rpc")
        assert fn is not None, "_handle_task_rpc not found"
        # Must reuse the shared op table, not a private copy that can drift.
        assert "dispatch_task_op" in ast.dump(fn)
        assert "task_command_result" in _string_literals(fn)
        assert "req_id" in _string_literals(fn)

    def test_agent_and_gateway_agree_on_the_reply_frame_type(self):
        """Both ends must name the *same* frame type.

        Asserting each side in isolation misses a rename on one end: the agent
        would publish ``X`` while the gateway waits for ``Y``, every
        ``/api/tasks`` call would time out, and both files would still look
        individually plausible.
        """
        emitted = {v for v in _string_literals(_adapter_fn("_handle_task_rpc")) if v.startswith("task_command_result")}
        intercepted = {t for t in _equality_gate_types() if t.startswith("task_command_result")}
        assert emitted == {"task_command_result"}
        assert intercepted == {"task_command_result"}

    def test_agent_and_gateway_agree_on_the_command_name(self):
        # Same argument for the request direction: gateway sends
        # `command=task_rpc`, agent must branch on exactly that.
        assert "task_rpc" in _adapter_command_branches()
        assert "task_rpc" in _bridge_rpc_strings()

    def test_handler_replies_even_when_bootstrap_fails(self):
        src = ADAPTER_SRC.read_text(encoding="utf-8")
        start = src.index("async def _handle_task_rpc")
        end = src.index("async def _try_wake_agent", start)
        body = src[start:end]
        # The import/dispatch is inside try/except so a broken scheduler still
        # produces a frame (the send sits *after* the try block).
        assert "except Exception" in body
        assert body.rindex("task_command_result") > body.index("except Exception")


# ---------------------------------------------------------------------------
# 5. tasks_api routing
# ---------------------------------------------------------------------------


class TestRouterDispatch:
    async def test_online_agent_is_proxied_over_ws(self, monkeypatch):
        seen = {}

        async def fake_rpc(agent_id, op, params, **kw):
            seen.update(agent_id=agent_id, op=op, params=params)
            return {"req_id": "r", "op": op, "result": {"ok": True, "tasks": [{"task_id": "t9"}]}}

        monkeypatch.setattr(
            tasks_api,
            "_ws_module",
            lambda: _fake_ws_module(connections={"agent305-001": object()}, rpc=fake_rpc),
        )
        monkeypatch.setattr(tasks_api, "_resolve_agent_id", lambda a: "agent305-001")

        out = await tasks_api.list_tasks(agent_id="")
        assert out == {"status": "ok", "tasks": [{"task_id": "t9"}]}
        assert seen == {"agent_id": "agent305-001", "op": "list", "params": {}}

    async def test_empty_agent_id_targets_the_only_live_agent(self, monkeypatch):
        seen = {}

        async def fake_rpc(agent_id, op, params, **kw):
            seen["agent_id"] = agent_id
            return {"op": op, "result": {"ok": True, "tasks": []}}

        monkeypatch.setattr(
            tasks_api,
            "_ws_module",
            lambda: _fake_ws_module(connections={"solo": object()}, rpc=fake_rpc),
        )
        monkeypatch.setattr(tasks_api, "_resolve_agent_id", lambda a: a)

        await tasks_api.list_tasks(agent_id="")
        assert seen["agent_id"] == "solo"

    async def test_ambiguous_agents_fall_back_instead_of_guessing(self, monkeypatch):
        from fastapi import HTTPException

        called = []

        async def fake_rpc(agent_id, op, params, **kw):
            called.append(agent_id)
            return {"op": op, "result": {"ok": True, "tasks": []}}

        monkeypatch.setattr(
            tasks_api,
            "_ws_module",
            lambda: _fake_ws_module(connections={"a": object(), "b": object()}, rpc=fake_rpc),
        )
        monkeypatch.setattr(tasks_api, "_resolve_agent_id", lambda a: a)
        monkeypatch.setattr(tasks_api, "_local_scheduler", lambda: None)

        with pytest.raises(HTTPException) as exc:
            await tasks_api.submit_task(tasks_api.SubmitTaskRequest(title="T", prompt="P", agent_id=""))
        assert exc.value.status_code == 503
        # Picking one of two live agents on the caller's behalf would silently
        # run the task on the wrong one.
        assert called == []

    async def test_offline_agent_uses_a_local_scheduler_when_one_exists(self, monkeypatch):
        calls = []

        def fake_dispatch(sched, op, params):
            calls.append((op, params))
            return {"ok": True, "tasks": [{"task_id": "local"}]}

        monkeypatch.setattr(tasks_api, "_ws_module", lambda: _fake_ws_module(connections={}, rpc=None))
        monkeypatch.setattr(tasks_api, "_resolve_agent_id", lambda a: a or "offline-agent")
        monkeypatch.setattr(tasks_api, "_local_scheduler", lambda: _StubScheduler())
        monkeypatch.setattr("opensquad.tasks.rpc.dispatch_task_op", fake_dispatch)

        out = await tasks_api.list_tasks(agent_id="offline-agent")
        assert out == {"status": "ok", "tasks": [{"task_id": "local"}]}
        assert calls == [("list", {})]

    async def test_no_endpoint_available_is_503_not_an_empty_list(self, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(tasks_api, "_ws_module", lambda: _fake_ws_module(connections={}, rpc=None))
        monkeypatch.setattr(tasks_api, "_resolve_agent_id", lambda a: a or "offline-agent")
        monkeypatch.setattr(tasks_api, "_local_scheduler", lambda: None)

        with pytest.raises(HTTPException) as exc:
            await tasks_api.submit_task(tasks_api.SubmitTaskRequest(title="T", prompt="P", agent_id="offline-agent"))
        assert exc.value.status_code == 503
        assert "not connected" in exc.value.detail

    async def test_idle_list_returns_empty_rather_than_an_error(self, monkeypatch):
        # A UI with nothing connected must render "no tasks", not an error banner.
        monkeypatch.setattr(tasks_api, "_ws_module", lambda: _fake_ws_module(connections={}, rpc=None))
        monkeypatch.setattr(tasks_api, "_resolve_agent_id", lambda a: a)
        monkeypatch.setattr(tasks_api, "_local_scheduler", lambda: None)

        assert await tasks_api.list_tasks(agent_id="") == {"status": "ok", "tasks": []}

    async def test_limit_error_maps_to_429(self, monkeypatch):
        from fastapi import HTTPException

        async def fake_rpc(agent_id, op, params, **kw):
            return {"op": op, "result": {"ok": False, "error": "limit reached", "error_kind": "limit"}}

        monkeypatch.setattr(
            tasks_api,
            "_ws_module",
            lambda: _fake_ws_module(connections={"a": object()}, rpc=fake_rpc),
        )
        monkeypatch.setattr(tasks_api, "_resolve_agent_id", lambda a: "a")

        with pytest.raises(HTTPException) as exc:
            await tasks_api.submit_task(tasks_api.SubmitTaskRequest(title="T", prompt="P", agent_id="a"))
        assert exc.value.status_code == 429

    async def test_missing_task_maps_to_404(self, monkeypatch):
        from fastapi import HTTPException

        async def fake_rpc(agent_id, op, params, **kw):
            return {"op": op, "result": {"ok": False, "error": "task not found", "error_kind": "not_found"}}

        monkeypatch.setattr(
            tasks_api,
            "_ws_module",
            lambda: _fake_ws_module(connections={"a": object()}, rpc=fake_rpc),
        )
        monkeypatch.setattr(tasks_api, "_resolve_agent_id", lambda a: "a")

        with pytest.raises(HTTPException) as exc:
            await tasks_api.get_task("ghost", agent_id="a")
        assert exc.value.status_code == 404

    async def test_get_scopes_the_view_to_the_requested_agent(self, monkeypatch):
        from fastapi import HTTPException

        async def fake_rpc(agent_id, op, params, **kw):
            return {"op": op, "result": {"ok": True, "task": {"task_id": "t1", "agent_id": "other"}}}

        monkeypatch.setattr(
            tasks_api,
            "_ws_module",
            lambda: _fake_ws_module(connections={"a": object()}, rpc=fake_rpc),
        )
        monkeypatch.setattr(tasks_api, "_resolve_agent_id", lambda a: "a")

        with pytest.raises(HTTPException) as exc:
            await tasks_api.get_task("t1", agent_id="a")
        assert exc.value.status_code == 404

    async def test_abort_and_remove_round_trip(self, monkeypatch):
        async def fake_rpc(agent_id, op, params, **kw):
            if op == "remove":
                # remove() carries no task back — only a success status.
                return {"op": op, "result": {"ok": True}}
            return {"op": op, "result": {"ok": True, "task": {"task_id": params["task_id"]}}}

        monkeypatch.setattr(
            tasks_api,
            "_ws_module",
            lambda: _fake_ws_module(connections={"a": object()}, rpc=fake_rpc),
        )
        monkeypatch.setattr(tasks_api, "_resolve_agent_id", lambda a: "a")

        assert (await tasks_api.abort_task("t1", agent_id="a"))["status"] == "ok"
        assert await tasks_api.remove_task("t1", agent_id="a") == {"status": "ok"}
        assert (await tasks_api.approve_task("t1", tasks_api.ApproveTaskRequest(), agent_id="a"))["status"] == "ok"

    async def test_resume_route_round_trip(self, monkeypatch):
        seen = {}

        async def fake_rpc(agent_id, op, params, **kw):
            seen.update(op=op, params=params)
            return {"op": op, "result": {"ok": True, "task": {"task_id": params["task_id"]}}}

        monkeypatch.setattr(
            tasks_api,
            "_ws_module",
            lambda: _fake_ws_module(connections={"a": object()}, rpc=fake_rpc),
        )
        monkeypatch.setattr(tasks_api, "_resolve_agent_id", lambda a: "a")

        out = await tasks_api.resume_task("t1", agent_id="a")
        assert out["status"] == "ok"
        assert seen == {"op": "resume", "params": {"task_id": "t1"}}

    def test_every_task_scoped_route_has_a_matching_op(self):
        """Bidirectional parity between the REST routes and the RPC op table.

        A route with no op (or an op with no route) is precisely the drift that
        left ``/api/tasks`` unusable in the split deployment — pin both ways.
        """
        src = pathlib.Path(tasks_api.__file__).read_text(encoding="utf-8")
        suffix_routed = set(re.findall(r'@router\.(?:get|post|delete)\("/\{task_id\}/([a-z_]+)"\)', src))
        # GET /{task_id} and DELETE /{task_id} carry no suffix of their own.
        suffix_routed |= {"get", "remove"}

        assert suffix_routed == set(TASK_RPC_OPS) - {"list", "submit"}

    def test_no_relative_url_httpx_fallback_remains(self):
        # The previous fallback built a URL like "/agent/default/tasks/..." and
        # handed it to httpx without a base_url — every call raised InvalidURL.
        src = pathlib.Path(tasks_api.__file__).read_text(encoding="utf-8")
        assert "get_local_http_client" not in src
        assert "/agent/" not in src
