"""Task RPC — op dispatch shared by the agent and the gateway.

The Gateway owns ``/api/tasks`` (REST), but the ``TaskScheduler`` lives in the
*agent* process. In a split deployment (gateway and agent as separate OS
processes) every op therefore has to travel the agent WebSocket as a
``type=command, command=task_rpc`` frame and come back as a
``task_command_result`` frame.

``dispatch_task_op`` is deliberately free of any transport concern: it takes a
duck-typed scheduler and returns a plain dict. Both sides use it —

* the **agent** as the server side (``gateway_adapter._handle_task_rpc``),
* the **gateway** as the fallback path when the scheduler happens to live in
  its own process (embedded desktop / single-process deploy),
* **tests**, which pass a stub and assert the contract without sockets.

Return contract: ``{"ok": True, ...}`` or ``{"ok": False, "error": str}``.
Never raises — a bad op must not be able to kill a caller's command loop.
"""

from __future__ import annotations

import logging

from opensquad.tasks.goal_runner import KIND_GOAL, KIND_TASK, build_plan

logger = logging.getLogger(__name__)

# Ops the gateway may invoke. Kept as an explicit tuple so the dispatch surface
# is greppable, and so a scan test can pin it against the REST routes.
TASK_RPC_OPS: tuple[str, ...] = (
    "list",
    "get",
    "submit",
    "abort",
    "approve",
    "remove",
    "resume",
)

# Ops whose scheduler method returns {"status": "ok"|"error", ...} instead of
# raising. Normalised below so the RPC frame has exactly one success signal.
_STATUS_STYLE_OPS = frozenset({"abort", "approve", "remove", "resume"})


def _normalize_status_result(result) -> dict:
    """Map ``{"status": "ok"|"error"}`` onto the ``{"ok": bool}`` contract."""
    if not isinstance(result, dict):
        return {"ok": False, "error": "unexpected scheduler result"}
    if result.get("status") == "error":
        return {"ok": False, "error": result.get("message") or "task op failed"}
    return {"ok": True, **{k: v for k, v in result.items() if k != "status"}}


def _plan_for_submit(kind: str, params: dict) -> dict:
    """Assemble the initial ``task.plan`` checkpoint for a submission.

    A goal carries a real GoalPlan (milestones + budget) so the panel can render
    a progress bar before the first turn runs, and so ``resume()`` has something
    to continue from. A caller that already holds a checkpoint (a re-submit) can
    pass ``plan`` verbatim instead.
    """
    if kind != KIND_GOAL:
        return {}
    existing = params.get("plan")
    if isinstance(existing, dict) and existing.get("milestones"):
        return existing
    return build_plan(
        goal=str(params.get("goal") or params.get("prompt") or params.get("title") or ""),
        milestones=params.get("milestones"),
        budget=params.get("budget"),
    ).to_dict()


def dispatch_task_op(scheduler, op: str, params: dict | None = None) -> dict:
    """Run one task-RPC op against ``scheduler`` and return a result dict.

    ``scheduler`` is duck-typed (normally
    :class:`opensquad.tasks.task_scheduler.TaskScheduler`).
    """
    params = params if isinstance(params, dict) else {}
    try:
        if op == "list":
            return {
                "ok": True,
                "tasks": scheduler.list_tasks(agent_id=params.get("agent_id") or None),
            }

        if op == "get":
            task = scheduler.get_task(str(params.get("task_id") or ""))
            if not task:
                # error_kind lets the gateway pick 404 instead of a blanket 409.
                return {"ok": False, "error": "task not found", "error_kind": "not_found"}
            return {"ok": True, "task": task}

        if op == "submit":
            kind = str(params.get("kind") or "").strip() or KIND_TASK
            task = scheduler.submit(
                title=str(params.get("title") or ""),
                prompt=str(params.get("prompt") or ""),
                agent_id=str(params.get("agent_id") or ""),
                origin=str(params.get("origin") or "manual"),
                base_dir=str(params.get("base_dir") or ""),
                use_worktree=bool(params.get("use_worktree", True)),
                kind=kind,
                plan=_plan_for_submit(kind, params),
            )
            return {"ok": True, "task": task}

        if op in _STATUS_STYLE_OPS:
            task_id = str(params.get("task_id") or "")
            if op == "abort":
                result = scheduler.abort(task_id)
            elif op == "approve":
                result = scheduler.set_approval(task_id, bool(params.get("approved", True)))
            elif op == "resume":
                result = scheduler.resume(task_id)
            else:
                result = scheduler.remove(task_id)
            return _normalize_status_result(result)

        return {"ok": False, "error": f"unknown op: {op}"}
    except RuntimeError as exc:
        # submit() raises RuntimeError when the per-agent active-task cap is hit
        # — the gateway maps this to HTTP 429 rather than 503.
        return {"ok": False, "error": str(exc), "error_kind": "limit"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[tasks] rpc op=%s failed", op, exc_info=True)
        return {"ok": False, "error": str(exc)}
