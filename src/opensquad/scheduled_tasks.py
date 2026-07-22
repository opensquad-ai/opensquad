"""Scheduled tasks: persistent delegated tasks with cron/interval scheduling.

Per-agent JSON storage + threading.Timer scheduling (mirrors reminder plugin).
Execution pushes the task prompt into a dedicated session via input_hub,
so it runs as an independent parallel turn without blocking interactive chat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# The gateway's main asyncio event loop, captured at gateway startup (see
# set_gateway_loop) so scheduled (timer-thread) fires can route prompts to the
# Agent via the Gateway WS registry even before any admin route lazily created
# a manager. Without this, a persisted task that fires right after a gateway
# restart would have self._loop=None, fall back to gateway-local push_ingress,
# and create the session in the GATEWAY workspace -> "Session not found" when
# the frontend reads it from the Agent workspace.
_gateway_loop: asyncio.AbstractEventLoop | None = None


def set_gateway_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Capture the gateway's running loop (called from app startup)."""
    global _gateway_loop
    _gateway_loop = loop


def _parse_hhmm(s: str) -> tuple[int, int]:
    try:
        h, m = s.split(":")
        return int(h), int(m)
    except Exception:
        return 9, 0


def _compute_next_ts(schedule: dict[str, Any]) -> float | None:
    now = datetime.now()
    rtype = schedule.get("type", "once")
    if rtype == "once":
        ts = schedule.get("run_at_ts")
        if isinstance(ts, (int, float)) and ts > now.timestamp():
            return float(ts)
        return None
    if rtype == "daily":
        h, m = _parse_hhmm(schedule.get("time", "09:00"))
        c = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if c <= now:
            c += timedelta(days=1)
        return c.timestamp()
    if rtype == "weekly":
        h, m = _parse_hhmm(schedule.get("time", "09:00"))
        raw = schedule.get("weekdays", "0,1,2,3,4,5,6")
        try:
            weekdays = [int(d.strip()) for d in raw.split(",")]
        except ValueError:
            weekdays = list(range(7))
        for delta in range(0, 8):
            cdate = now + timedelta(days=delta)
            if cdate.weekday() in weekdays:
                c = cdate.replace(hour=h, minute=m, second=0, microsecond=0)
                if c > now:
                    return c.timestamp()
        return None
    if rtype == "interval":
        total = int(schedule.get("total_seconds", 0) or 0)
        if total > 0:
            return now.timestamp() + total
        return None
    return None


class ScheduledTaskManager:
    """Per-agent scheduled task store + scheduler."""

    def __init__(self, agent_id: str, data_dir: str):
        self.agent_id = agent_id or "default"
        self._data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._data_file = os.path.join(data_dir, f"{agent_id}_scheduled_tasks.json")
        self._tasks: dict[str, dict[str, Any]] = {}
        self._executions: list[dict[str, Any]] = []
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- lifecycle --
    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._load_persisted()
        logger.info("[ScheduledTasks] started agent=%s tasks=%d", self.agent_id, len(self._tasks))

    def stop(self) -> None:
        with self._lock:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()
        logger.info("[ScheduledTasks] stopped agent=%s", self.agent_id)

    # -- persistence --
    def _load_persisted(self) -> None:
        if not os.path.isfile(self._data_file):
            return
        try:
            with open(self._data_file, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("[ScheduledTasks] load failed: %s", e)
            return
        now_ts = time.time()
        with self._lock:
            self._tasks = {k: v for k, v in (data.get("tasks") or {}).items()}
            self._executions = list(data.get("executions") or [])
        for tid, task in list(self._tasks.items()):
            if not task.get("enabled", True):
                continue
            next_ts = task.get("next_run_ts")
            if not next_ts or next_ts <= now_ts:
                next_ts = _compute_next_ts(task.get("schedule") or {})
            if next_ts and next_ts > now_ts:
                self._arm_timer(tid, next_ts - now_ts)
            elif task.get("schedule", {}).get("type") == "once" and next_ts and next_ts <= now_ts:
                # Missed one-shot — mark disabled
                task["enabled"] = False
                task["last_status"] = "missed"
        self._save_persisted()

    def _save_persisted(self) -> None:
        try:
            with open(self._data_file, "w", encoding="utf-8") as f:
                json.dump(
                    {"tasks": self._tasks, "executions": self._executions[-200:]},
                    f,
                    ensure_ascii=False,
                )
        except Exception as e:
            logger.error("[ScheduledTasks] save failed: %s", e)

    # -- CRUD --
    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public(t) for t in self._tasks.values()]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            t = self._tasks.get(task_id)
            return self._public(t) if t else None

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        tid = payload.get("id") or uuid.uuid4().hex[:12]
        now_ts = time.time()
        task = {
            "id": tid,
            "name": (payload.get("name") or "").strip() or "Untitled",
            "prompt": payload.get("prompt") or "",
            "workspace": payload.get("workspace") or "",
            "delegate_agent": payload.get("delegate_agent") or self.agent_id,
            "model_card": payload.get("model_card") or "",
            "skills": list(payload.get("skills") or []),
            "schedule": payload.get("schedule") or {"type": "once"},
            "enabled": bool(payload.get("enabled", True)),
            "created_at": now_ts,
            "updated_at": now_ts,
            "last_run_ts": None,
            "last_status": None,
            "next_run_ts": None,
            "run_count": 0,
        }
        self._recompute_next(task)
        with self._lock:
            self._tasks[tid] = task
            self._save_persisted()
        if task["enabled"] and task["next_run_ts"]:
            self._arm_timer(tid, max(0.0, task["next_run_ts"] - now_ts))
        return self._public(task)

    def update_task(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            for k in ("name", "prompt", "workspace", "delegate_agent", "model_card", "skills", "schedule"):
                if k in payload:
                    task[k] = payload[k]
            if "enabled" in payload:
                task["enabled"] = bool(payload["enabled"])
            task["updated_at"] = time.time()
            self._cancel_timer(task_id)
            self._recompute_next(task)
            self._save_persisted()
        if task["enabled"] and task["next_run_ts"]:
            self._arm_timer(task_id, max(0.0, task["next_run_ts"] - time.time()))
        return self._public(task)

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._tasks:
                return False
            self._cancel_timer(task_id)
            del self._tasks[task_id]
            self._save_persisted()
        return True

    def set_enabled(self, task_id: str, enabled: bool) -> dict[str, Any] | None:
        return self.update_task(task_id, {"enabled": bool(enabled)})

    def run_now(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            # Reject duplicate concurrent runs: if an execution for this task is
            # still running, don't fire another — tell the caller it's running.
            for e in self._executions:
                if e.get("task_id") == task_id and e.get("status") == "running":
                    return {"already_running": True}
        self._execute(task_id, manual=True)
        return self.get_task(task_id)

    def list_executions(self, task_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            ex = list(self._executions)
        if task_id:
            ex = [e for e in ex if e.get("task_id") == task_id]
        return ex

    def get_execution(self, exec_id: str) -> dict[str, Any] | None:
        with self._lock:
            for e in self._executions:
                if e.get("id") == exec_id:
                    return dict(e)
        return None

    # -- internals --
    def _recompute_next(self, task: dict[str, Any]) -> None:
        if not task.get("enabled"):
            task["next_run_ts"] = None
            return
        task["next_run_ts"] = _compute_next_ts(task.get("schedule") or {})

    def _cancel_timer(self, task_id: str) -> None:
        t = self._timers.pop(task_id, None)
        if t:
            t.cancel()

    def _arm_timer(self, task_id: str, delay: float) -> None:
        self._cancel_timer(task_id)
        if delay < 0:
            delay = 0.0
        timer = threading.Timer(delay, self._fire, args=(task_id,))
        timer.daemon = True
        timer.start()
        self._timers[task_id] = timer

    def _fire(self, task_id: str) -> None:
        self._execute(task_id, manual=False)
        # Reschedule
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            self._cancel_timer(task_id)
            self._recompute_next(task)
            self._save_persisted()
            nxt = task.get("next_run_ts")
        if nxt:
            self._arm_timer(task_id, max(0.0, nxt - time.time()))

    def _execute(self, task_id: str, manual: bool = False) -> str:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return ""
            prompt = task.get("prompt") or ""
            name = task.get("name") or "task"
            delegate_agent = (task.get("delegate_agent") or self.agent_id or "").strip()
        exec_id = uuid.uuid4().hex[:12]
        started = time.time()
        exec_record = {
            "id": exec_id,
            "task_id": task_id,
            "task_name": name,
            "started_at": started,
            "ended_at": None,
            "status": "running",
            "manual": manual,
            "session_id": None,
            "delegate_agent": delegate_agent,
            "error": None,
        }
        with self._lock:
            self._executions.append(exec_record)
            task["last_run_ts"] = started
            task["last_status"] = "running"
            task["run_count"] = int(task.get("run_count", 0)) + 1
            self._save_persisted()

        # Deliver the prompt to the delegated Agent process over the Gateway WS
        # (the same path web chat uses), so the turn — and its session — is
        # created in the Agent's own workspace. The frontend reads sessions from
        # the Agent's workspace, so the recorded session_id will be loadable
        # (fixes "Session not found"). The Agent reports the disk session_id
        # via the `current_session` WS event; the gateway WS loop correlates it
        # by user_id ("scheduled-task:{exec_id}") and fills session_id here
        # (see set_execution_session_by_exec_id).
        try:
            content = f"[Scheduled Task: {name}]\n{prompt}"
            sent = self._send_to_agent(delegate_agent, exec_id, content, task.get("model_card") or "")
            if not sent:
                raise RuntimeError("delegate agent not connected")
            logger.info(
                "[ScheduledTasks] fired task=%s exec=%s delegate=%s manual=%s",
                task_id,
                exec_id,
                delegate_agent,
                manual,
            )
        except Exception as e:
            with self._lock:
                exec_record["status"] = "failed"
                exec_record["ended_at"] = time.time()
                exec_record["error"] = str(e)
                task["last_status"] = "failed"
                self._save_persisted()
            logger.error("[ScheduledTasks] fire failed: %s", e)
        return exec_id

    def mark_execution_done(self, exec_id: str, status: str = "success") -> None:
        with self._lock:
            for e in self._executions:
                if e.get("id") == exec_id:
                    e["status"] = status
                    e["ended_at"] = time.time()
                    break
            for t in self._tasks.values():
                if t.get("last_status") == "running":
                    t["last_status"] = status
                    break
            self._save_persisted()

    def stop_execution(self, exec_id: str) -> dict[str, Any] | None:
        """Cancel a running execution's in-flight turn and mark it stopped."""
        with self._lock:
            sid = ""
            delegate_agent = ""
            found = False
            for e in self._executions:
                if e.get("id") == exec_id:
                    found = True
                    sid = (e.get("session_id") or "").strip()
                    delegate_agent = (e.get("delegate_agent") or self.agent_id or "").strip()
                    if e.get("status") == "running":
                        e["status"] = "stopped"
                        e["ended_at"] = time.time()
                    break
            if not found:
                return None
            for t in self._tasks.values():
                if t.get("last_status") == "running":
                    t["last_status"] = "stopped"
                    break
            self._save_persisted()
        # Cancel the in-flight turn in the AGENT process via the Gateway WS
        # (the turn runs in the agent, not the gateway, so gateway-local
        # request_stop_session would be a no-op).
        if sid and delegate_agent:
            self._send_stop_to_agent(delegate_agent, exec_id, sid)
        return self.get_execution(exec_id)

    def set_execution_session(self, exec_id: str, session_id: str) -> bool:
        """Record the Agent-reported disk session_id for an execution.

        Called by the gateway WS loop when the `current_session` event for a
        scheduled-task turn arrives (correlated by user_id). Only fills when
        empty so a later re-fire doesn't clobber an existing binding.
        """
        if not exec_id or not session_id:
            return False
        with self._lock:
            for e in self._executions:
                if e.get("id") == exec_id:
                    if not (e.get("session_id") or "").strip():
                        e["session_id"] = session_id
                    self._save_persisted()
                    return True
            return False

    # -- agent delivery (gateway WS registry) --

    @staticmethod
    def _import_registry():
        """Import the Gateway's live AgentRegistry singleton.

        IMPORTANT: the gateway app is started via ``run.py`` which puts
        ``<backend>`` (so ``app`` is importable) AND ``<project-root>`` (so
        ``opensquad`` is importable) on sys.path. The gateway's WebSocket
        handler imports the registry as ``from .registry import registry``
        inside ``app/ai_web`` — i.e. the module is ``app.ai_web.registry``.
        If we import it here as ``opensquad.gateway.backend.app.ai_web.registry``
        Python treats it as a DIFFERENT module (different ``__name__``) and
        creates a SECOND ``registry`` singleton — empty. Agents register into
        the gateway's singleton, but we'd read our empty one, so
        ``list_agents()`` returns [] and every delivery fails with
        "delegate agent not connected" even though the agent is online.

        Fix: import via the same ``app.ai_web.registry`` path the gateway uses
        so we share the live, populated singleton. Fall back to the long path
        only for non-gateway contexts (tests, ad-hoc imports) where ``app`` is
        not on sys.path.
        """
        # 1) canonical path used by the gateway at runtime (shares the singleton)
        try:
            from app.ai_web.registry import registry as _reg

            return _reg
        except Exception:
            pass
        # 2) fallback for contexts where `app` is not importable
        try:
            from opensquad.gateway.backend.app.ai_web.registry import registry as _reg

            return _reg
        except Exception as e:
            logger.debug("[ScheduledTasks] registry import failed: %s", e)
            return None

    @staticmethod
    def _resolve_registry_agent_id(agent_id: str) -> str:
        """Resolve a delegate_agent id to the agent's registered WS agent_id.

        ``registry.send_to_agent`` is keyed by the registered agent_id (from the
        agent's config, e.g. "agent305-001"), but the task UI stores the on-disk
        directory name (e.g. "agent305"). When they differ, a direct lookup
        misses and the execution fails with "delegate agent not connected".
        Here we map the stored id back to a registered one via the live registry.
        """
        if not agent_id:
            return agent_id
        registry = ScheduledTaskManager._import_registry()
        if registry is None:
            return agent_id
        try:
            agents = registry.list_agents()
        except Exception:
            return agent_id
        # 1) exact match
        for a in agents:
            if (a.agent_id or "").strip() == agent_id:
                return agent_id
        # 2) dir_name "agent305" -> registered "agent305-001" (prefix or suffix split)
        for a in agents:
            aid = (a.agent_id or "").strip()
            if not aid:
                continue
            if aid.startswith(agent_id + "-") or aid.rsplit("-", 1)[0] == agent_id:
                return aid
        return agent_id

    def _send_to_agent(self, agent_id: str, exec_id: str, content: str, model_card: str) -> bool:
        """Push a chat message to the delegated Agent via the Gateway WS registry.

        Runs the async registry.send_to_agent on the gateway event loop (captured
        in get_task_manager). user_id is "scheduled-task:{exec_id}" so the
        `current_session` event can be correlated back to this execution. Falls
        back to legacy gateway-local push_ingress when no loop/registry is
        available so the feature still functions (session then lives in the
        gateway workspace — the old, "Session not found"-prone behavior).
        """
        loop = self._loop or _gateway_loop
        if loop is None or not loop.is_running():
            from opensquad.ingress_policy import push_ingress

            sid = push_ingress(content, source="scheduled-task", channel="external", model_card=model_card)
            with self._lock:
                for e in self._executions:
                    if e.get("id") == exec_id:
                        e["session_id"] = sid
                        break
                self._save_persisted()
            return True
        registry = self._import_registry()
        if registry is None:
            return False
        target = self._resolve_registry_agent_id(agent_id)
        message = {
            "type": "chat",
            "user_id": f"scheduled-task:{exec_id}",
            "content": content,
            # external → Agent's primary session (dedicated to automated input),
            # separate from the user's interactive web chat. Matches the
            # original push_ingress fallback semantics.
            "channel": "external",
        }
        if model_card:
            message["model_card"] = model_card
        try:
            fut = asyncio.run_coroutine_threadsafe(registry.send_to_agent(target, message), loop)
            return bool(fut.result(timeout=10))
        except Exception as e:
            logger.warning("[ScheduledTasks] send_to_agent failed: %s", e)
            return False

    def _send_stop_to_agent(self, agent_id: str, exec_id: str, sid: str) -> None:
        """Send a stop_task command to the Agent for this execution's session."""
        loop = self._loop or _gateway_loop
        if loop is None or not loop.is_running():
            try:
                from opensquad.input_hub import get_input_hub

                get_input_hub().request_stop_session(sid)
            except Exception as e:
                logger.warning("[ScheduledTasks] legacy stop failed: %s", e)
            return
        registry = self._import_registry()
        if registry is None:
            return
        target = self._resolve_registry_agent_id(agent_id)
        message = {
            "type": "command",
            "user_id": f"scheduled-task:{exec_id}",
            "command": "stop_task",
            "data": {"session_id": sid},
        }
        try:
            fut = asyncio.run_coroutine_threadsafe(registry.send_to_agent(target, message), loop)
            fut.result(timeout=5)
        except Exception as e:
            logger.warning("[ScheduledTasks] stop send_to_agent failed: %s", e)

    @staticmethod
    def _public(task: dict[str, Any]) -> dict[str, Any]:
        return dict(task)


# -- per-agent singleton registry --
_managers: dict[str, ScheduledTaskManager] = {}
_managers_lock = threading.Lock()


def get_task_manager(agent_id: str, data_dir: str | None = None) -> ScheduledTaskManager:
    key = agent_id or "default"
    with _managers_lock:
        m = _managers.get(key)
        if m is None:
            if data_dir is None:
                from opensquad.system_config import syscfg

                data_dir = syscfg.workspace_data_dir("scheduled_tasks")
            m = ScheduledTaskManager(key, data_dir)
            # Auto-start: load persisted tasks and arm timers. Agent delivery
            # uses the gateway WS registry (async), so we capture the running
            # event loop below when called from an async (FastAPI) context.
            try:
                m.start(None)
            except Exception as e:
                logger.warning("[ScheduledTasks] auto-start failed for %s: %s", key, e)
            _managers[key] = m
        # Capture the running event loop (when called from an async context
        # such as the FastAPI admin routes) so timer-fired _execute can route
        # prompts to the Agent via the Gateway WS registry. Fall back to the
        # gateway loop captured at startup (set_gateway_loop) so persisted
        # tasks that fire before any admin route is hit still use the registry
        # path instead of the gateway-local push_ingress fallback.
        if m._loop is None:
            m._loop = _gateway_loop
            try:
                m._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        return m


def set_execution_session_by_exec_id(exec_id: str, session_id: str) -> bool:
    """Fill the session_id of an execution identified only by exec_id.

    The gateway WS loop receives the Agent's `current_session` event with the
    registered agent_id, which may differ from the manager key (URL name), so we
    locate the owning manager by scanning all of them rather than keying by
    agent_id.
    """
    if not exec_id or not session_id:
        return False
    with _managers_lock:
        managers = list(_managers.values())
    return any(m.set_execution_session(exec_id, session_id) for m in managers)
