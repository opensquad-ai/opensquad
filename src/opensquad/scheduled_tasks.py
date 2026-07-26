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

# How long after a successful send we wait for Agent to spawn a session and
# bind session_id. Half-dead WS: send_text returns True but chat never reaches
# GatewayAdapter — without this watchdog executions stay "running" forever.
_SESSION_SPAWN_TIMEOUT_S = 45.0
# Extra redeliveries after the initial fire (probe + send again).
_MAX_REDELIVERIES = 2
# Delay between redelivery attempts.
_REDELIVER_GAP_S = 5.0

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
        # exec_id -> Timer watching for session_id after fire
        self._spawn_watchdogs: dict[str, threading.Timer] = {}
        # exec_id -> payload needed to redeliver (content, model_card, delegate, …)
        self._pending_deliveries: dict[str, dict[str, Any]] = {}

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
            for t in self._spawn_watchdogs.values():
                t.cancel()
            self._spawn_watchdogs.clear()
            self._pending_deliveries.clear()
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
            # Gateway/agent restart: in-flight turns are gone. Leave no zombie
            # "running" rows that block run_now and show empty workflow panes.
            for e in self._executions:
                if e.get("status") == "running":
                    e["status"] = "failed"
                    e["ended_at"] = now_ts
                    e["error"] = e.get("error") or "interrupted (process restart)"
            for task in self._tasks.values():
                if task.get("last_status") == "running":
                    task["last_status"] = "failed"
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
            skills = list(task.get("skills") or [])
            model_card = task.get("model_card") or ""
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
            content = self._build_fire_content(name, prompt, skills)
            sent = self._send_to_agent(delegate_agent, exec_id, content, model_card, verified=True)
            if not sent:
                raise RuntimeError("delegate agent not connected")
            logger.info(
                "[ScheduledTasks] fired task=%s exec=%s delegate=%s skills=%s manual=%s",
                task_id,
                exec_id,
                delegate_agent,
                skills,
                manual,
            )
            self._arm_spawn_watchdog(
                exec_id,
                {
                    "delegate_agent": delegate_agent,
                    "content": content,
                    "model_card": model_card,
                    "attempts": 0,
                },
            )
            self._notify_execution_changed(dict(exec_record))
        except Exception as e:
            with self._lock:
                exec_record["status"] = "failed"
                exec_record["ended_at"] = time.time()
                exec_record["error"] = str(e)
                task["last_status"] = "failed"
                self._save_persisted()
            logger.error("[ScheduledTasks] fire failed: %s", e)
            self._notify_execution_changed(dict(exec_record))
        return exec_id

    def _cancel_spawn_watchdog(self, exec_id: str) -> None:
        with self._lock:
            t = self._spawn_watchdogs.pop(exec_id, None)
            self._pending_deliveries.pop(exec_id, None)
        if t:
            t.cancel()

    def _arm_spawn_watchdog(self, exec_id: str, delivery: dict[str, Any]) -> None:
        """Fail / redeliver if Agent never binds a session_id after fire."""
        self._cancel_spawn_watchdog(exec_id)
        with self._lock:
            self._pending_deliveries[exec_id] = dict(delivery)

        def _on_timeout() -> None:
            self._on_spawn_timeout(exec_id)

        timer = threading.Timer(_SESSION_SPAWN_TIMEOUT_S, _on_timeout)
        timer.daemon = True
        with self._lock:
            self._spawn_watchdogs[exec_id] = timer
        timer.start()

    def _on_spawn_timeout(self, exec_id: str) -> None:
        """Called when session_id was not bound in time after fire/redeliver."""
        with self._lock:
            self._spawn_watchdogs.pop(exec_id, None)
            delivery = dict(self._pending_deliveries.get(exec_id) or {})
            rec = None
            for e in self._executions:
                if e.get("id") == exec_id:
                    rec = e
                    break
            if rec is None:
                self._pending_deliveries.pop(exec_id, None)
                return
            # Already bound or already terminal — nothing to do.
            if (rec.get("session_id") or "").strip() or rec.get("status") != "running":
                self._pending_deliveries.pop(exec_id, None)
                return
            attempts = int(delivery.get("attempts") or 0)

        if attempts < _MAX_REDELIVERIES and delivery:
            logger.warning(
                "[ScheduledTasks] exec=%s no session after %.0fs — redelivering (%d/%d)",
                exec_id,
                _SESSION_SPAWN_TIMEOUT_S,
                attempts + 1,
                _MAX_REDELIVERIES,
            )
            time.sleep(_REDELIVER_GAP_S)
            # Re-check after gap (session may have arrived late).
            with self._lock:
                still = None
                for e in self._executions:
                    if e.get("id") == exec_id:
                        still = e
                        break
                if still is None or (still.get("session_id") or "").strip() or still.get("status") != "running":
                    self._pending_deliveries.pop(exec_id, None)
                    return
            sent = self._send_to_agent(
                delivery.get("delegate_agent") or "",
                exec_id,
                delivery.get("content") or "",
                delivery.get("model_card") or "",
                verified=True,
            )
            if sent:
                delivery["attempts"] = attempts + 1
                self._arm_spawn_watchdog(exec_id, delivery)
                return
            err = "delegate agent not connected (redeliver failed)"
        else:
            err = (
                f"agent did not spawn session within {_SESSION_SPAWN_TIMEOUT_S:.0f}s "
                f"(delivery timeout after {attempts} redeliveries)"
            )

        with self._lock:
            self._pending_deliveries.pop(exec_id, None)
            for e in self._executions:
                if e.get("id") == exec_id and e.get("status") == "running":
                    e["status"] = "failed"
                    e["ended_at"] = time.time()
                    e["error"] = err
                    rec = dict(e)
                    break
            else:
                rec = None
            for t in self._tasks.values():
                if t.get("last_status") == "running":
                    t["last_status"] = "failed"
                    break
            self._save_persisted()
        logger.error("[ScheduledTasks] exec=%s marked failed: %s", exec_id, err)
        if rec:
            self._notify_execution_changed(rec)

    @staticmethod
    def _build_fire_content(name: str, prompt: str, skills: list | None = None) -> str:
        """Build the initial auto-send content for a scheduled-task fire.

        Mirrors Agent Web skill selection: prefix ``<user_send_skill>dir</user_send_skill>``
        so the runner expands skill instructions the same way as a manual chip send.
        """
        body = (
            f"[Scheduled Task: {name}]\n"
            "You are in Scheduled Task mode. First call "
            "`task_watch.start(description=..., check_interval=120)` "
            "before executing; use `task_watch.update` for progress and "
            "`task_watch.complete` when done.\n"
            f"{prompt or ''}"
        )
        tags: list[str] = []
        for raw in skills or []:
            sid = (raw if isinstance(raw, str) else str(raw or "")).strip()
            if sid:
                tags.append(f"<user_send_skill>{sid}</user_send_skill>")
        if not tags:
            return body
        return "\n".join(tags) + "\n\n" + body

    def mark_execution_done(self, exec_id: str, status: str = "success") -> dict[str, Any] | None:
        """Mark a running execution terminal. No-op if already finished."""
        self._cancel_spawn_watchdog(exec_id)
        rec: dict[str, Any] | None = None
        with self._lock:
            for e in self._executions:
                if e.get("id") == exec_id:
                    if e.get("status") != "running":
                        return dict(e)
                    e["status"] = status
                    e["ended_at"] = time.time()
                    rec = dict(e)
                    break
            if rec is None:
                return None
            for t in self._tasks.values():
                if t.get("last_status") == "running":
                    t["last_status"] = status
                    break
            self._save_persisted()
        self._notify_execution_changed(rec)
        return rec

    def stop_execution(self, exec_id: str) -> dict[str, Any] | None:
        """Cancel a running execution's in-flight turn and mark it stopped."""
        self._cancel_spawn_watchdog(exec_id)
        with self._lock:
            sid = ""
            delegate_agent = ""
            found = False
            changed = False
            for e in self._executions:
                if e.get("id") == exec_id:
                    found = True
                    sid = (e.get("session_id") or "").strip()
                    delegate_agent = (e.get("delegate_agent") or self.agent_id or "").strip()
                    if e.get("status") == "running":
                        e["status"] = "stopped"
                        e["ended_at"] = time.time()
                        changed = True
                    break
            if not found:
                return None
            if changed:
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
        rec = self.get_execution(exec_id)
        if changed and rec:
            self._notify_execution_changed(rec)
        return rec

    def delete_execution(self, exec_id: str) -> bool:
        """Remove an execution record from the list.

        If the execution is still running, stop it first (best-effort) so the
        agent turn does not keep running orphaned after the UI entry is gone.
        """
        if not exec_id:
            return False
        self._cancel_spawn_watchdog(exec_id)
        with self._lock:
            target = None
            for e in self._executions:
                if e.get("id") == exec_id:
                    target = e
                    break
            if target is None:
                return False
            was_running = target.get("status") == "running"
            sid = (target.get("session_id") or "").strip()
            delegate_agent = (target.get("delegate_agent") or self.agent_id or "").strip()
            removed = dict(target)
            self._executions = [e for e in self._executions if e.get("id") != exec_id]
            if was_running:
                for t in self._tasks.values():
                    if t.get("last_status") == "running":
                        t["last_status"] = "stopped"
                        break
            self._save_persisted()
        if was_running and sid and delegate_agent:
            self._send_stop_to_agent(delegate_agent, exec_id, sid)
        if removed:
            removed["status"] = "deleted"
            self._notify_execution_changed(removed)
        return True

    def set_execution_session(self, exec_id: str, session_id: str) -> bool:
        """Record the Agent-reported disk session_id for an execution.

        Called by the gateway WS loop when the `current_session` event for a
        scheduled-task turn arrives (correlated by user_id). Only fills when
        empty so a later re-fire doesn't clobber an existing binding.
        """
        if not exec_id or not session_id:
            return False
        changed = False
        rec: dict[str, Any] | None = None
        with self._lock:
            for e in self._executions:
                if e.get("id") == exec_id:
                    if not (e.get("session_id") or "").strip():
                        e["session_id"] = session_id
                        changed = True
                    self._save_persisted()
                    rec = dict(e)
                    break
        # Session bound — cancel delivery watchdog / pending redeliveries.
        if rec is not None:
            self._cancel_spawn_watchdog(exec_id)
        if changed and rec:
            self._notify_execution_changed(rec)
        return rec is not None

    def _notify_execution_changed(self, execution: dict[str, Any] | None) -> None:
        """Push a scheduled_execution event to browsers watching this agent."""
        if not execution:
            return
        loop = self._loop or _gateway_loop
        if loop is None or not loop.is_running():
            return
        payload = {
            "type": "scheduled_execution",
            "content": dict(execution),
            "data": dict(execution),
        }
        agent_ids: list[str] = []
        aid = (self.agent_id or "").strip()
        if aid:
            agent_ids.append(aid)
        try:
            resolved = self._resolve_registry_agent_id(aid) if aid else ""
            if resolved and resolved not in agent_ids:
                agent_ids.append(resolved)
        except Exception:
            pass
        if not agent_ids:
            return

        async def _broadcast() -> None:
            try:
                # Prefer the same import path the gateway WS loop uses.
                try:
                    from app.ai_web.websocket import user_handler as _uh
                except Exception:
                    from opensquad.gateway.backend.app.ai_web.websocket import user_handler as _uh
                for target in agent_ids:
                    await _uh.broadcast_to_agent(target, payload)
            except Exception as e:
                logger.debug("[ScheduledTasks] notify broadcast failed: %s", e)

        try:
            asyncio.run_coroutine_threadsafe(_broadcast(), loop)
        except Exception as e:
            logger.debug("[ScheduledTasks] notify schedule failed: %s", e)

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

    def _send_to_agent(
        self,
        agent_id: str,
        exec_id: str,
        content: str,
        model_card: str,
        *,
        verified: bool = True,
    ) -> bool:
        """Push a chat message to the delegated Agent via the Gateway WS registry.

        Runs the async registry.send_to_agent on the gateway event loop (captured
        in get_task_manager). user_id is "scheduled-task:{exec_id}" so the
        `current_session` event can be correlated back to this execution. Falls
        back to legacy gateway-local push_ingress when no loop/registry is
        available so the feature still functions (session then lives in the
        gateway workspace — the old, "Session not found"-prone behavior).

        When *verified* is True (default), an application-level ping/pong probe
        runs first so we refuse to "successfully" fire into a half-dead WS
        (send_text would return True but chat never reaches GatewayAdapter).
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
        # Extract task name from "[Scheduled Task: {name}]" (may follow skill tags).
        session_title = ""
        try:
            import re

            m = re.search(r"\[Scheduled Task:\s*(.+?)\]", content or "")
            if m:
                session_title = m.group(1).strip()
        except Exception:
            session_title = ""
        message = {
            "type": "chat",
            "user_id": f"scheduled-task:{exec_id}",
            "content": content,
            # web + no session_id → Agent GatewayAdapter spawns a brand-new
            # parallel session (does NOT steal the user's focused pane) and
            # binds the turn to it. Follow-ups carry session_id explicitly.
            "channel": "web",
        }
        if session_title:
            message["session_title"] = session_title
        if model_card:
            message["model_card"] = model_card

        async def _deliver() -> bool:
            send_fn = getattr(registry, "send_to_agent_verified", None)
            if verified and callable(send_fn):
                return bool(await send_fn(target, message, probe=True))
            return bool(await registry.send_to_agent(target, message))

        try:
            fut = asyncio.run_coroutine_threadsafe(_deliver(), loop)
            # Probe (upto ~8s) + send — allow generous budget.
            return bool(fut.result(timeout=20))
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
# Sessions observed busy while a scheduled execution was running.
_seen_busy_exec_sessions: set[str] = set()


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


def warm_all_task_managers(loop: asyncio.AbstractEventLoop | None = None) -> int:
    """Load every persisted ``*_scheduled_tasks.json`` and arm timers.

    Called from gateway startup so fires work even if nobody opens the
    scheduled-tasks admin page (which used to be the only lazy trigger).
    """
    try:
        from opensquad.system_config import syscfg

        data_dir = syscfg.workspace_data_dir("scheduled_tasks")
    except Exception as e:
        logger.warning("[ScheduledTasks] warm: data dir unavailable: %s", e)
        return 0
    if not data_dir or not os.path.isdir(data_dir):
        return 0
    count = 0
    suffix = "_scheduled_tasks.json"
    for name in os.listdir(data_dir):
        if not name.endswith(suffix):
            continue
        agent_id = name[: -len(suffix)]
        if not agent_id:
            continue
        try:
            m = get_task_manager(agent_id, data_dir)
            if loop is not None:
                m._loop = loop
            elif m._loop is None:
                m._loop = _gateway_loop
            count += 1
        except Exception as e:
            logger.warning("[ScheduledTasks] warm failed for %s: %s", agent_id, e)
    return count


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


def mark_execution_done_by_exec_id(exec_id: str, status: str = "success") -> dict[str, Any] | None:
    """Mark an execution terminal by exec_id across all managers."""
    if not exec_id:
        return None
    with _managers_lock:
        managers = list(_managers.values())
    for m in managers:
        rec = m.mark_execution_done(exec_id, status=status)
        if rec is not None:
            return rec
    return None


def scheduled_execution_session_ids() -> set[str]:
    """All session_ids bound to scheduled executions (for sidebar hide fallback)."""
    out: set[str] = set()
    with _managers_lock:
        managers = list(_managers.values())
    for m in managers:
        with m._lock:
            for e in m._executions:
                sid = (e.get("session_id") or "").strip()
                if sid:
                    out.add(sid)
    return out


def reconcile_executions_for_busy_sessions(agent_id: str, busy_session_ids: list | set) -> int:
    """Track busy sessions for scheduled executions — do NOT auto-complete.

    Completing on ``busy_sessions`` edge (seen busy → not busy) races with the
    dispatcher, which re-broadcasts busy every loop tick. A flicker marks
    ``success`` while the parallel turn is still running. Terminal status must
    come from ``scheduled_task_turn_done`` / ``stop_execution`` only.

    Returns 0 always; kept as a hook so the gateway WS call site stays stable.
    """
    busy = {str(s).strip() for s in (busy_session_ids or []) if str(s).strip()}
    global _seen_busy_exec_sessions
    # Still record observations for debugging / future crash-recovery heuristics.
    if busy:
        _seen_busy_exec_sessions |= busy
    _ = agent_id  # agent-scoped filtering reserved for a future recovery path
    return 0
