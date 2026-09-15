"""opensquad/tasks/hooks.py — task executors (M2 single turn / M3 goal run).

Bridges the TaskScheduler to the live agent runtime: spawning a parallel
session for the task, applying M1 worktree isolation, binding the session
cwd, and driving the prompt through the runner.

Runs inside the agent process (same process as the runner), which is what
makes the per-session cwd override — read by tools via
``opensquad.utils.path_utils.get_workspace_root()`` — actually take effect
for the task's tool calls.

How a turn is driven (and why)
------------------------------
The dispatcher's own ingress path (``push_ingress`` → hub → dispatcher) is
fire-and-forget: the hub only hands the item to the dispatcher, so the caller
learns *nothing* about completion. M2 shipped that way and therefore marked a
task ``done`` the moment the prompt was delivered.

M3 needs the opposite: milestones run **one at a time** and each one has to
report tokens + outcome before the next starts. So the executor awaits the
runner's own per-session turn coroutine
(``AgentRunner._parallel_session_turn``) directly, but still goes through
``ParallelTurnScheduler.start`` so the session shows up as busy in the UI and
occupies a parallel slot. Awaiting the coroutine is the only completion signal
in this process that cannot be missed:

* ``scheduled_task_turn_done`` is only emitted for ``scheduled-task:`` user ids;
* the ``busy_sessions`` edge is explicitly documented as racy
  (see ``scheduled_tasks.reconcile_executions_for_busy_sessions``);
* ``turn_usage`` is skipped entirely when nothing was billed, which is exactly
  the failed-turn case a milestone retry cares about.
"""

from __future__ import annotations

import asyncio
import logging
import os

from opensquad.tasks.goal_runner import (
    KIND_GOAL,
    PLAN_BLOCKED,
    PLAN_DONE,
    GoalPlan,
    Milestone,
    TurnOutcome,
    run_goal,
)
from opensquad.tasks.task_scheduler import Task, TaskBlockedError

logger = logging.getLogger(__name__)

# How long to wait for the dispatcher/scheduler to pick the turn up.
TURN_START_TIMEOUT = 60.0
# Hard ceiling for one milestone turn. A turn that somehow never returns must
# not hold a concurrency slot forever; hitting it fails the milestone so the
# retry/blocked accounting still runs.
TURN_MAX_SECONDS = 3600.0

# Which session id / task id prefix identifies goal-authored turns.
_TASK_USER_PREFIX = "task:"


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------


def _resolve_base_dir(task: Task) -> str:
    """task.base_dir, else the session project cwd, else the workspace root."""
    base_dir = (task.base_dir or "").strip()
    if not base_dir:
        try:
            from opensquad.utils.path_utils import get_session_cwd_override

            base_dir = get_session_cwd_override() or ""
        except Exception:
            base_dir = ""
    if not base_dir:
        try:
            from opensquad.system_config import syscfg

            base_dir = syscfg.get_workspace()
        except Exception:
            base_dir = os.getcwd()
    return base_dir


def _prepare_worktree(task: Task, base_dir: str) -> str:
    """M1 isolation — its own worktree so parallel tasks never collide.

    The worktree outlives the run on purpose: the M1 diff report and the
    merge/discard decision need it after the task reaches a terminal state.
    """
    try:
        from opensquad.workspace.worktree_manager import prepare_task_workspace

        prep = prepare_task_workspace(base_dir, task.task_id)
        path = prep.get("path") or base_dir
        if prep.get("worktree"):
            task.worktree_path = path
        return path
    except Exception:
        logger.warning("[tasks] worktree prepare failed; using base dir", exc_info=True)
        return base_dir


def _create_session(task: Task) -> str:
    from opensquad.session_manager import get_session_manager

    mgr = get_session_manager()
    sid = mgr.create_parallel_session(title=task.title or task.task_id, origin="task")
    task.session_id = sid
    return sid


def _bind_cwd(path: str) -> None:
    try:
        from opensquad.utils.path_utils import set_session_cwd_override

        set_session_cwd_override(path)
    except Exception:
        logger.warning("[tasks] set session cwd failed", exc_info=True)


def _active_runner():
    try:
        from opensquad.runner import _active_runner

        return _active_runner
    except Exception:
        return None


# ---------------------------------------------------------------------------
# one turn
# ---------------------------------------------------------------------------


def _usage_snapshot(runner, sid: str) -> dict:
    """Mirror of the runner's own ``_round_usage_snapshot`` (defensive copy)."""
    try:
        snap = runner._round_usage_snapshot(sid)
        if isinstance(snap, dict):
            return snap
    except Exception:
        pass
    return {}


def _tokens_spent(before: dict, after: dict) -> int:
    """Billed tokens for the turn, tolerating a mid-turn ChatAPI rebind.

    Same rule as ``Runner._finalize_round_usage``: when the api object changed
    (auth fallback) the deltas are meaningless and the absolute totals win.
    """
    if not after:
        return 0
    if before.get("api") is not None and before.get("api") is after.get("api"):
        inp = int(after.get("input", 0) or 0) - int(before.get("input", 0) or 0)
        outp = int(after.get("output", 0) or 0) - int(before.get("output", 0) or 0)
        return max(0, inp) + max(0, outp)
    return max(0, int(after.get("input", 0) or 0)) + max(0, int(after.get("output", 0) or 0))


def _last_assistant_text(sid: str) -> str:
    """The session's newest assistant message — the milestone's evidence."""
    try:
        from opensquad.session_manager import get_session_manager

        msgs = get_session_manager().get_messages(sid=sid) or []
    except Exception:
        return ""
    for msg in reversed(msgs):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role") or "") == "assistant":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return ""


async def run_session_turn(session_id: str, content: str, task: Task | None = None) -> TurnOutcome:
    """Deliver one turn into *session_id* and wait for it to finish."""
    runner = _active_runner()
    if runner is None:
        return TurnOutcome(ok=False, error="no active agent runner")
    turn_fn = getattr(runner, "_parallel_session_turn", None)
    if not callable(turn_fn):
        return TurnOutcome(ok=False, error="runner has no _parallel_session_turn")

    item = {
        "content": content,
        "source": "task",
        "channel": "web",
        "user_id": f"{_TASK_USER_PREFIX}{getattr(task, 'task_id', '') or session_id}",
        "session_id": session_id,
        "model_card": "",
    }

    before = _usage_snapshot(runner, session_id)
    sched = getattr(runner, "_parallel_scheduler", None)
    ok = True
    error = ""
    try:
        coro = turn_fn(session_id, item)
        if sched is not None:
            # Occupies a parallel slot and shows the pane as busy; the awaited
            # task is the completion signal.
            await asyncio.wait_for(sched.acquire_slot(session_id, timeout=TURN_START_TIMEOUT), TURN_START_TIMEOUT)
            fut = sched.start(session_id, coro)
            try:
                await asyncio.wait_for(asyncio.shield(fut), timeout=TURN_MAX_SECONDS)
            finally:
                sched.finish(session_id)
        else:
            await asyncio.wait_for(coro, timeout=TURN_MAX_SECONDS)
    except asyncio.TimeoutError:
        ok, error = False, "turn timed out"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[tasks] turn failed sid=%s", session_id, exc_info=True)
        ok, error = False, str(exc)

    after = _usage_snapshot(runner, session_id)
    return TurnOutcome(
        ok=ok,
        text=_last_assistant_text(session_id) if ok else "",
        tokens=_tokens_spent(before, after),
        error=error,
    )


# ---------------------------------------------------------------------------
# executors
# ---------------------------------------------------------------------------


async def _prepare_task_env(task: Task) -> tuple[str, str]:
    """Shared prologue: pick a worktree, spawn the session, bind its cwd."""
    base_dir = _resolve_base_dir(task)
    worktree_path = _prepare_worktree(task, base_dir)
    try:
        sid = _create_session(task)
    except Exception as exc:
        raise RuntimeError(f"failed to create task session: {exc}") from exc
    _bind_cwd(worktree_path)
    logger.info("[tasks] task=%s kind=%s session=%s cwd=%s", task.task_id, task.kind, sid, worktree_path)
    return sid, worktree_path


async def default_task_executor(task: Task) -> str:
    """Execute one task turn (M2 path: a plain task is a single turn)."""
    sid, worktree_path = await _prepare_task_env(task)
    outcome = await run_session_turn(sid, task.prompt, task)
    task.cost["tokens"] = int(task.cost.get("tokens", 0) or 0) + max(0, outcome.tokens)
    if not outcome.ok:
        raise RuntimeError(outcome.error or "task turn failed")
    bits = [f"session={sid}"]
    if worktree_path:
        bits.append(f"worktree={os.path.basename(worktree_path)}")
    return "; ".join(bits)


def _checkpoint(task: Task, plan: GoalPlan) -> None:
    """Persist the plan after every milestone so a resume can continue.

    Runs through the scheduler so the panel gets a ``task_update`` with fresh
    ``plan_done`` / ``plan_total`` on each milestone, and its ``_save()`` is what
    actually writes ``task.plan`` to disk.
    """
    task.plan = plan.to_dict()
    done, total = plan.progress()
    task.plan_done, task.plan_total = done, total
    task.cost["tokens"] = int(plan.spent_tokens)
    try:
        from opensquad.tasks.task_scheduler import get_scheduler

        get_scheduler().update_progress(task.task_id, plan_done=done, plan_total=total)
    except Exception:
        logger.debug("[tasks] goal checkpoint persist skipped", exc_info=True)


async def goal_task_executor(task: Task) -> str:
    """M3 — drive a budgeted, resumable milestone run in one session."""
    sid, worktree_path = await _prepare_task_env(task)

    plan = GoalPlan.from_dict(task.plan)
    if not plan.milestones:
        # A goal submitted without explicit milestones still has to mean
        # something: treat the goal text itself as the single milestone.
        plan.goal = plan.goal or task.prompt or task.title
        plan.milestones = [Milestone(id="m1", title=plan.goal or task.title, prompt=plan.goal or task.prompt)]
    if not plan.goal:
        plan.goal = task.title or task.prompt

    async def _turn(prompt: str, milestone) -> TurnOutcome:
        content = prompt
        if milestone.verify:
            content = f"{prompt}\n\nAcceptance criteria:\n{milestone.verify}"
        return await run_session_turn(sid, content, task)

    final = await run_goal(plan, _turn, on_update=lambda p: _checkpoint(task, p))
    done, total = final.progress()

    if final.status == PLAN_DONE:
        bits = [f"session={sid}", f"milestones={done}/{total}"]
        if worktree_path:
            bits.append(f"worktree={os.path.basename(worktree_path)}")
        return "; ".join(bits)

    if final.status == PLAN_BLOCKED:
        raise TaskBlockedError(final.blocked_reason or f"goal blocked at {done}/{total} milestones")
    raise TaskBlockedError(f"goal ended {final.status} at {done}/{total} milestones")


async def task_executor(task: Task) -> str:
    """Dispatch by ``task.kind`` — a goal gets the milestone driver."""
    if str(getattr(task, "kind", "") or "") == KIND_GOAL:
        return await goal_task_executor(task)
    return await default_task_executor(task)


def register_default_executor() -> None:
    from opensquad.tasks.task_scheduler import register_executor

    register_executor(task_executor)
