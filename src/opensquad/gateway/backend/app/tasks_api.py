"""Parallel task REST API (Gateway proxy layer) — M2.

Exposes the agent-side TaskScheduler to the web UI:

- ``GET    /api/tasks``                — list tasks (optionally ?agent_id=)
- ``POST   /api/tasks``                — submit (title, prompt, base_dir?, use_worktree?,
                                         and for M3 goals: kind="goal", milestones[], budget{})
- ``GET    /api/tasks/{task_id}``      — one task
- ``POST   /api/tasks/{task_id}/abort``   — abort a running/queued task
- ``POST   /api/tasks/{task_id}/approve`` — approve a waiting_approval task
- ``POST   /api/tasks/{task_id}/resume``  — continue a parked (blocked) goal from its checkpoint
- ``DELETE /api/tasks/{task_id}``      — remove a terminal task from the registry

**Where the scheduler lives.** ``TaskScheduler`` is created by the *agent*
process (``gateway_adapter._wire_task_scheduler``), while this router is mounted
in the *gateway* process. In the split deployment that means a local scheduler
is either absent or a lazily created empty shell — reading it returned a
convincing ``{"status": "ok", "tasks": []}`` forever, and submitting never ran.

So every op is routed by :func:`_dispatch`:

1. agent WS connected → proxy over the agent command channel
   (``AgentTaskRPCBridge`` in ``ai_web/websocket.py`` ↔ ``task_rpc`` command in
   ``opensquad/gateway_adapter.py``);
2. otherwise, and only when *this* process really hosts a configured scheduler
   (embedded desktop / single-process deploy), call it in a worker thread;
3. otherwise 503 with a reason — never a fake empty list.
"""

from __future__ import annotations

import asyncio
import os
import sys

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


# ==================== Pydantic Models ====================


class SubmitTaskRequest(BaseModel):
    title: str
    prompt: str
    agent_id: str = ""
    base_dir: str = ""
    use_worktree: bool = True
    # M3 — "task" (one turn) or "goal" (budgeted, resumable milestone run).
    kind: str = "task"
    # Goal only. ``goal`` defaults to ``prompt`` downstream.
    goal: str = ""
    milestones: list[str | dict] | None = None
    budget: dict | None = None


class ApproveTaskRequest(BaseModel):
    approved: bool = True


# ==================== Helpers ====================


def _ws_module():
    """Import the ai_web websocket module under either sys.path layout."""
    try:
        from app.ai_web import websocket as ws_mod

        return ws_mod
    except Exception:
        pass
    try:
        from ai_web import websocket as ws_mod

        return ws_mod
    except Exception:
        return None


def _resolve_agent_id(agent_id: str) -> str:
    """Map UI / on-disk dir aliases (``agent305``) to the registered WS id."""
    if not agent_id:
        return ""
    ws_mod = _ws_module()
    resolver = getattr(ws_mod, "_resolve_registered_agent_id", None)
    if resolver is None:
        return agent_id
    try:
        return resolver(agent_id)
    except Exception:
        return agent_id


def _connected_agents() -> list[str]:
    ws_mod = _ws_module()
    registry = getattr(ws_mod, "registry", None)
    connections = getattr(registry, "connections", None)
    if not isinstance(connections, dict):
        return []
    return [a for a in connections if a]


def _resolve_target_agent(agent_id: str) -> str:
    """Pick the agent a task op should target.

    An explicit id wins (after alias resolution). An empty one falls back to the
    only connected agent — the usual single-agent layout — and stays empty when
    the choice would be ambiguous rather than guessing.
    """
    resolved = _resolve_agent_id(agent_id)
    if resolved:
        return resolved
    live = _connected_agents()
    return live[0] if len(live) == 1 else ""


def _local_scheduler():
    """The in-process scheduler, or None when this process does not host one."""
    try:
        from opensquad.tasks.task_scheduler import get_local_scheduler

        return get_local_scheduler()
    except Exception:
        return None


async def _dispatch(op: str, params: dict, agent_id: str) -> dict:
    """Run one task op against whichever process owns the scheduler.

    Returns the raw RPC contract (``{"ok": bool, ...}``); use :func:`_ok` to
    turn it into the HTTP shape.
    """
    target = _resolve_target_agent(agent_id)

    if target and target in _connected_agents():
        ws_mod = _ws_module()
        bridge = getattr(ws_mod, "task_rpc_bridge", None)
        if bridge is None:
            raise HTTPException(status_code=503, detail="task RPC bridge unavailable")
        envelope = await bridge.rpc(target, op, params)
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise HTTPException(status_code=502, detail="malformed task RPC result")
        return result

    local = _local_scheduler()
    if local is not None:
        from opensquad.tasks.rpc import dispatch_task_op

        # The scheduler holds locks and rewrites its JSON state — keep it off
        # the event loop.
        return await asyncio.to_thread(dispatch_task_op, local, op, params)

    raise HTTPException(
        status_code=503,
        detail=(
            f"agent '{agent_id}' is not connected and no task scheduler runs in this process"
            if agent_id
            else "no agent is connected and no task scheduler runs in this process"
        ),
    )


def _ok(result: dict) -> dict:
    """Translate the dispatch contract into the HTTP surface."""
    if result.get("ok"):
        return {"status": "ok", **{k: v for k, v in result.items() if k != "ok"}}
    error = result.get("error") or "task op failed"
    kind = result.get("error_kind")
    if kind == "limit":
        raise HTTPException(status_code=429, detail=error)
    if kind == "not_found":
        raise HTTPException(status_code=404, detail=error)
    raise HTTPException(status_code=409, detail=error)


# ==================== Routes ====================


@router.get("")
async def list_tasks(agent_id: str = Query(default="")):
    # Views can list while nothing is connected: an idle UI must show "no
    # tasks", not an error banner. Reachability is only decisive for ops that
    # mutate or read one specific task.
    try:
        result = await _dispatch("list", {}, agent_id)
    except HTTPException as exc:
        if exc.status_code == 503 and not agent_id:
            return {"status": "ok", "tasks": []}
        raise
    return _ok(result)


@router.post("")
async def submit_task(body: SubmitTaskRequest):
    result = await _dispatch(
        "submit",
        {
            "title": body.title,
            "prompt": body.prompt,
            "agent_id": body.agent_id,
            "origin": "manual",
            "base_dir": body.base_dir,
            "use_worktree": body.use_worktree,
            "kind": body.kind,
            "goal": body.goal,
            "milestones": body.milestones,
            "budget": body.budget,
        },
        body.agent_id,
    )
    return _ok(result)


@router.get("/{task_id}")
async def get_task(task_id: str, agent_id: str = Query(default="")):
    result = await _dispatch("get", {"task_id": task_id}, agent_id)
    task = result.get("task")
    if agent_id and isinstance(task, dict) and task.get("agent_id") != agent_id:
        # The scheduler is keyed globally; the query param scopes the view to one
        # agent so a UI cannot read another agent's task by id.
        raise HTTPException(status_code=404, detail="task not found")
    return _ok(result)


@router.post("/{task_id}/abort")
async def abort_task(task_id: str, agent_id: str = Query(default="")):
    result = await _dispatch("abort", {"task_id": task_id}, agent_id)
    return _ok(result)


@router.post("/{task_id}/approve")
async def approve_task(task_id: str, body: ApproveTaskRequest, agent_id: str = Query(default="")):
    result = await _dispatch("approve", {"task_id": task_id, "approved": body.approved}, agent_id)
    return _ok(result)


@router.post("/{task_id}/resume")
async def resume_task(task_id: str, agent_id: str = Query(default="")):
    """Continue a parked goal from its checkpoint (M3).

    Only ``blocked`` / ``interrupted`` *goal* tasks resume — the scheduler
    rejects the rest, so a stray click cannot re-run a finished task.
    """
    result = await _dispatch("resume", {"task_id": task_id}, agent_id)
    return _ok(result)


@router.delete("/{task_id}")
async def remove_task(task_id: str, agent_id: str = Query(default="")):
    result = await _dispatch("remove", {"task_id": task_id}, agent_id)
    return _ok(result)
