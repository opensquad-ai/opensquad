"""
opensquad/tasks/task_scheduler.py — Session-level parallel task scheduler (M2).

Turns ad-hoc async delegation into a first-class *task* abstraction with a
visible lifecycle:

- A **Task** owns a dedicated parallel session (via
  ``SessionManager.create_parallel_session``) and optionally an M1 git
  worktree for filesystem isolation.
- The scheduler enforces a **per-agent concurrency cap** (asyncio semaphore)
  and a **per-agent task ceiling** so runaway goal loops cannot exhaust the
  model quota.
- Status transitions broadcast ``task_update`` events which the gateway
  relays to the frontend task panel.
- Persistence: ``tasks.json`` under the OpenSquad data dir. On boot, running
  tasks are recovered as ``interrupted`` so the UI can prompt the user to
  resume or clean them up.

This module lives in the agent process (same process as the runner) so that
the per-session cwd override — which tools read via
``opensquad.utils.path_utils`` — applies to the task's session for real.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid

from opensquad.system_config import syscfg
from opensquad.tasks.goal_runner import KIND_GOAL, KIND_TASK, GoalPlan, resume_plan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_WAITING_APPROVAL = "waiting_approval"
# M3: a goal that stopped on its budget or on an unverified milestone. It is
# parked, not failed — ``resume()`` picks it up from its checkpoint.
STATUS_BLOCKED = "blocked"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_ABORTED = "aborted"
STATUS_INTERRUPTED = "interrupted"

TERMINAL_STATUSES = {
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_ABORTED,
    STATUS_INTERRUPTED,
    # Parked goals are terminal *for scheduling*: they hold no concurrency slot,
    # survive recover() untouched (so the checkpoint stays resumable) and can be
    # removed. resume() is the only way back to queued.
    STATUS_BLOCKED,
}


class TaskBlockedError(RuntimeError):
    """Raised by an executor to park a task as ``blocked`` instead of ``failed``.

    The distinction is the whole point of M3 resume: a goal that ran out of
    budget (or stopped on an unverified milestone) is *not* a failure — it holds
    its checkpoint and ``resume()`` continues from it. Executors that raise a
    plain exception still mean "something broke".
    """


class Task:
    # Order mirrors ``__init__`` for readability; slots have no ordering
    # semantics, so RUF023's alphabetical requirement is deliberately waived.
    __slots__ = (  # noqa: RUF023
        "task_id",
        "session_id",
        "agent_id",
        "title",
        "worktree_path",
        "base_dir",
        "status",
        "origin",
        "prompt",
        "created_at",
        "started_at",
        "finished_at",
        "plan_done",
        "plan_total",
        "cost",
        "error",
        "result_summary",
        # M3 — "task" (one turn) or "goal" (budgeted milestone run). ``plan``
        # carries the GoalPlan checkpoint for goals and stays {} for plain tasks.
        "kind",
        "plan",
        "on_event",
    )

    def __init__(self, **kw):
        self.task_id = kw.get("task_id", "")
        self.session_id = kw.get("session_id", "")
        self.agent_id = kw.get("agent_id", "")
        self.title = kw.get("title", "")
        self.worktree_path = kw.get("worktree_path", "")
        self.base_dir = kw.get("base_dir", "")
        self.status = kw.get("status", STATUS_QUEUED)
        self.origin = kw.get("origin", "manual")
        self.prompt = kw.get("prompt", "")
        self.created_at = float(kw.get("created_at", time.time()))
        self.started_at = kw.get("started_at")
        self.finished_at = kw.get("finished_at")
        self.plan_done = int(kw.get("plan_done", 0))
        self.plan_total = int(kw.get("plan_total", 0))
        self.cost = dict(kw.get("cost") or {"tokens": 0, "elapsed_ms": 0})
        self.error = kw.get("error", "")
        self.result_summary = kw.get("result_summary", "")
        self.kind = str(kw.get("kind") or KIND_TASK)
        plan = kw.get("plan")
        self.plan = dict(plan) if isinstance(plan, dict) else {}
        self.on_event = None  # runtime-only callback, never persisted

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "title": self.title,
            "worktree_path": self.worktree_path,
            "base_dir": self.base_dir,
            "status": self.status,
            "origin": self.origin,
            "prompt": self.prompt,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "plan_done": self.plan_done,
            "plan_total": self.plan_total,
            "cost": self.cost,
            "error": self.error,
            "result_summary": self.result_summary,
            "kind": self.kind,
            "plan": self.plan,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        return cls(**data)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class TaskScheduler:
    """Per-process task registry + runner.

    The agent process owns one scheduler instance. ``submit()`` queues a task;
    the event loop runs it once a semaphore slot frees up.
    """

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        self._sem: asyncio.Semaphore | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._agent_id = ""
        self._max_tasks_per_agent = 5
        self._persist_path = ""
        # Set by configure(). Distinguishes "this process really hosts the
        # scheduler" from the empty shell get_scheduler() lazily creates.
        self._configured = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def configure(self, *, agent_id: str = "", max_concurrent: int = 2, max_tasks: int = 5):
        self._agent_id = agent_id or self._agent_id
        self._max_tasks_per_agent = max(1, int(max_tasks))
        self._configured = True
        if self._sem is None or self._sem._value != max_concurrent:
            self._sem = asyncio.Semaphore(max(1, int(max_concurrent)))
        try:
            self._persist_path = os.path.join(syscfg.workspace_data_dir("tasks"), f"{self._agent_id or 'default'}.json")
        except Exception:
            self._persist_path = ""

    def is_configured(self) -> bool:
        """True once :meth:`configure` ran.

        A gateway process in a split deployment has no scheduler of its own,
        but ``get_scheduler()`` would still hand back a lazily created empty
        instance — whose task list is permanently empty and whose ``submit()``
        can never run (no loop attached). Callers that must not be fooled by
        that shell check this first.
        """
        return self._configured

    def attach_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    def _save(self):
        if not self._persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            with open(self._persist_path, "w", encoding="utf-8") as fh:
                json.dump([t.to_dict() for t in self._tasks.values()], fh, ensure_ascii=False, indent=2)
        except Exception:
            logger.debug("[tasks] persist failed", exc_info=True)

    def recover(self):
        """Mark in-flight tasks as interrupted after a process restart."""
        if not self._persist_path or not os.path.isfile(self._persist_path):
            return
        try:
            with open(self._persist_path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            return
        changed = False
        for item in raw if isinstance(raw, list) else []:
            try:
                task = Task.from_dict(item)
            except Exception:
                continue
            if task.status not in TERMINAL_STATUSES:
                task.status = STATUS_INTERRUPTED
                task.finished_at = task.finished_at or time.time()
                changed = True
            self._tasks[task.task_id] = task
        if changed:
            self._save()
            logger.info("[tasks] recovered %d interrupted tasks", len(self._tasks))

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------

    def _emit(self, task: Task, event_type: str = "task_update"):
        payload = {"type": event_type, "task": task.to_dict()}
        cb = getattr(task, "on_event", None)
        if callable(cb):
            try:
                cb(payload)
            except Exception:
                logger.debug("[tasks] on_event callback failed", exc_info=True)
        for sub in list(_subscribers):
            try:
                sub(payload)
            except Exception:
                logger.debug("[tasks] subscriber failed", exc_info=True)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def list_tasks(self, agent_id: str | None = None) -> list[dict]:
        with self._lock:
            tasks = list(self._tasks.values())
        if agent_id:
            tasks = [t for t in tasks if t.agent_id == agent_id]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tasks]

    def get_task(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._tasks.get(task_id)
        return task.to_dict() if task else None

    def submit(
        self,
        *,
        title: str,
        prompt: str,
        agent_id: str = "",
        origin: str = "manual",
        base_dir: str = "",
        use_worktree: bool = True,
        kind: str = KIND_TASK,
        plan: dict | None = None,
        on_event=None,
    ) -> dict:
        """Queue a new parallel task. Returns the task dict or raises."""
        agent_id = agent_id or self._agent_id
        kind = str(kind or KIND_TASK)
        plan = dict(plan) if isinstance(plan, dict) else {}
        with self._lock:
            active = [t for t in self._tasks.values() if t.agent_id == agent_id and t.status not in TERMINAL_STATUSES]
            if len(active) >= self._max_tasks_per_agent:
                raise RuntimeError(
                    f"Task limit reached for agent {agent_id} "
                    f"({self._max_tasks_per_agent} active). Finish or abort a task first."
                )
            task = Task(
                task_id=f"t{uuid.uuid4().hex[:12]}",
                agent_id=agent_id,
                title=title,
                prompt=prompt,
                origin=origin,
                base_dir=base_dir,
                status=STATUS_QUEUED,
                kind=kind,
                plan=plan,
            )
            if kind == KIND_GOAL:
                # Seed the progress counters so the panel shows 0/N immediately
                # instead of hiding the bar until the first milestone lands.
                done, total = GoalPlan.from_dict(plan).progress()
                task.plan_done, task.plan_total = done, total
            task.on_event = on_event
            self._tasks[task.task_id] = task
        self._save()
        self._emit(task, "task_update")
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._run(task), loop)
        else:
            logger.warning("[tasks] no event loop attached — task %s stays queued", task.task_id)
        return task.to_dict()

    async def _run(self, task: Task):
        """Execute one queued task: spawn a parallel session (optionally in a
        worktree), feed the prompt, and track completion."""
        assert self._sem is not None, "scheduler not configured"
        async with self._sem:
            task.status = STATUS_RUNNING
            task.started_at = time.time()
            self._emit(task)
            try:
                await self._execute(task)
            except asyncio.CancelledError:
                task.status = STATUS_ABORTED
                task.finished_at = time.time()
                self._save()
                self._emit(task)
                raise
            except Exception as exc:  # noqa: BLE001
                task.status = STATUS_FAILED
                task.error = str(exc)
                task.finished_at = time.time()
                logger.error("[tasks] task %s failed: %s", task.task_id, exc, exc_info=True)
                self._save()
                self._emit(task)

    async def _execute(self, task: Task):
        """Delegate the actual turn to the registered execution hook.

        The hook (registered by ``opensquad.tasks.hooks.register_executor``,
        wired in the gateway adapter) spawns the parallel session, applies
        M1 worktree isolation, binds the session cwd, and pushes
        ``task.prompt`` through the runner. Keeping the hook indirection here
        avoids a hard dependency on the gateway adapter from this module.
        """
        hook = get_executor()
        if hook is None:
            raise RuntimeError("no task executor registered (opensquad.tasks.hooks)")
        try:
            result = await hook(task)
            task.result_summary = str(result or "")
            task.status = STATUS_DONE
        except asyncio.CancelledError:
            task.status = STATUS_ABORTED
            raise
        except TaskBlockedError as exc:
            # Parked, not failed: the checkpoint stays resumable.
            task.status = STATUS_BLOCKED
            task.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            task.status = STATUS_FAILED
            task.error = str(exc)
        finally:
            task.finished_at = time.time()
            if task.started_at:
                task.cost["elapsed_ms"] = int((task.finished_at - task.started_at) * 1000)
            self._save()
            self._emit(task)

    def abort(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"status": "error", "message": f"unknown task {task_id}"}
            if task.status in TERMINAL_STATUSES:
                return {"status": "error", "message": f"task already {task.status}"}
            task.status = STATUS_ABORTED
            task.finished_at = time.time()
        self._save()
        self._emit(task)
        return {"status": "ok", "task": task.to_dict()}

    def update_progress(self, task_id: str, *, plan_done: int | None = None, plan_total: int | None = None):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status in TERMINAL_STATUSES:
                return
            if plan_done is not None:
                task.plan_done = int(plan_done)
            if plan_total is not None:
                task.plan_total = int(plan_total)
        self._save()
        self._emit(task)

    def set_approval(self, task_id: str, approved: bool):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status not in (STATUS_WAITING_APPROVAL, STATUS_RUNNING):
                return {"status": "error", "message": f"task not awaiting approval ({task.status})"}
            task.status = STATUS_RUNNING if approved else STATUS_ABORTED
            if not approved:
                task.finished_at = time.time()
        self._save()
        self._emit(task)
        return {"status": "ok", "task": task.to_dict()}

    def resume(self, task_id: str) -> dict:
        """Re-queue a parked goal so it continues from its checkpoint.

        Deliberately only for ``kind == "goal"``: a plain task is a single turn
        and re-running it would just repeat the work. Completed milestones keep
        their results, so this continues rather than restarts.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"status": "error", "message": f"unknown task {task_id}"}
            if task.kind != KIND_GOAL:
                return {"status": "error", "message": "only goal tasks can be resumed"}
            if task.status not in (STATUS_BLOCKED, STATUS_INTERRUPTED):
                return {
                    "status": "error",
                    "message": f"task is {task.status}; only blocked/interrupted goals resume",
                }
            plan = GoalPlan.from_dict(task.plan)
            if not plan.milestones:
                return {"status": "error", "message": "goal has no milestones to resume"}
            resume_plan(plan)
            if plan.status == STATUS_DONE:
                return {"status": "error", "message": "goal is already complete"}
            task.plan = plan.to_dict()
            task.plan_done, task.plan_total = plan.progress()
            task.status = STATUS_QUEUED
            task.error = ""
            task.finished_at = None
        self._save()
        self._emit(task, "task_update")
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._run(task), loop)
        else:
            logger.warning("[tasks] no event loop attached — resumed task %s stays queued", task.task_id)
        return {"status": "ok", "task": task.to_dict()}

    def remove(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"status": "error", "message": "unknown task"}
            if task.status not in TERMINAL_STATUSES:
                return {"status": "error", "message": f"task still {task.status} — abort first"}
            del self._tasks[task_id]
        self._save()
        self._emit(task, "task_removed")
        return {"status": "ok"}


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_scheduler: TaskScheduler | None = None
_scheduler_lock = threading.Lock()

# Process-local subscribers for task_update / task_removed events (used by the
# gateway adapter to relay events to the websocket layer).
_subscribers: list = []
_subscribers_lock = threading.Lock()


def subscribe(callback) -> None:
    with _subscribers_lock:
        if callback not in _subscribers:
            _subscribers.append(callback)


def unsubscribe(callback) -> None:
    with _subscribers_lock:
        try:
            _subscribers.remove(callback)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Execution hook (registered by the gateway adapter at startup)
# ---------------------------------------------------------------------------

_executor = None
_executor_lock = threading.Lock()


def register_executor(coro_fn) -> None:
    """Register the async callable that executes one task turn."""
    global _executor
    with _executor_lock:
        _executor = coro_fn


def get_executor():
    with _executor_lock:
        return _executor


def get_scheduler() -> TaskScheduler:
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = TaskScheduler()
    return _scheduler


def get_local_scheduler() -> TaskScheduler | None:
    """The scheduler **only when this process really hosts one**.

    ``get_scheduler()`` always returns an instance, but in a split deployment
    the gateway's copy is a lazily created empty shell: its list is permanently
    empty and ``submit()`` cannot run (no event loop attached). Callers that must
    fall back to "the local scheduler" — the gateway's ``/api/tasks`` proxy — use
    this instead and get ``None`` rather than a convincing empty shell.
    """
    sched = _scheduler
    if sched is None or not sched.is_configured():
        return None
    return sched
