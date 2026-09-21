"""
scheduled_tasks — Persistent Scheduled Task Tools（定时任务）

Lets the agent manage its own scheduled tasks (the same store the
Agent Web "定时任务" page uses). Tasks are persisted per agent and
survive restarts; each fire spawns a fresh session that executes the
configured prompt (optionally with skills).

Tools:
  - create_task:     Create a scheduled task (daily / weekly / interval / once)
  - list_tasks:      List this agent's scheduled tasks
  - update_task:     Change prompt / schedule / name of an existing task
  - delete_task:     Remove a task
  - set_task_enabled: Pause or resume a task without deleting it
  - run_task_now:    Trigger a task immediately (ignores the schedule)

Storage and firing are delegated to
opensquad.scheduled_tasks.get_task_manager — identical behaviour to the
admin REST routes, so tasks created here are visible/editable in the UI.
"""

import logging
from typing import Any

from opensquad.scheduled_tasks import get_task_manager

logger = logging.getLogger(__name__)

# 由 on_loaded（agent 启动）注入，见 agent_boot_phases.register_builtin_tools。
_agent_id = ""


def set_agent_id(agent_id: str) -> None:
    global _agent_id
    _agent_id = agent_id or ""


def _mgr():
    # 与管理路由一致：按 agent 名取/建单例管理器（自动加载持久化任务）。
    return get_task_manager(_agent_id or "default")


def _ok(message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "success", "message": message, **extra}


def _err(message: str) -> dict[str, Any]:
    return {"status": "error", "message": message}


def _build_schedule(
    schedule_type: str,
    time: str,
    weekdays: str,
    interval_seconds: int,
    run_at_ts: float,
) -> dict[str, Any]:
    t = (schedule_type or "daily").strip().lower()
    if t == "once":
        try:
            ts = float(run_at_ts or 0)
        except (TypeError, ValueError):
            ts = 0.0
        return {"type": "once", "run_at_ts": ts}
    if t == "weekly":
        return {
            "type": "weekly",
            "time": (time or "09:00").strip(),
            "weekdays": (weekdays or "0,1,2,3,4,5,6").strip(),
        }
    if t == "interval":
        try:
            total = int(interval_seconds or 0)
        except (TypeError, ValueError):
            total = 0
        return {"type": "interval", "total_seconds": total}
    return {"type": "daily", "time": (time or "09:00").strip()}


def _norm_skills(skills: str | list[str] | None) -> list[str]:
    if not skills:
        return []
    if isinstance(skills, list):
        return [str(s).strip() for s in skills if str(s).strip()]
    return [s.strip() for s in str(skills).split(",") if s.strip()]


def create_task(
    name: str = "",
    prompt: str = "",
    schedule_type: str = "daily",
    time: str = "09:00",
    weekdays: str = "0,1,2,3,4,5,6",
    interval_seconds: int = 0,
    run_at_ts: float = 0,
    skills: str | list[str] | None = None,
    workspace: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    """
    Create a scheduled task that runs automatically. The task persists across
    restarts and executes ``prompt`` in a fresh session at fire time.

    Args:
        name: Short task name, e.g. "每日晨报".
        prompt: What the agent should do on each fire. Be specific and self-contained.
        schedule_type: "daily" | "weekly" | "interval" | "once". Default "daily".
        time: Fire time "HH:MM" (24h, local). Used by daily/weekly. Default "09:00".
        weekdays: Weekly only — comma-separated Python weekday numbers where 0=Monday
            (e.g. "0,2,4" = Mon/Wed/Fri). Default every day.
        interval_seconds: Interval only — seconds between fires (e.g. 3600 = hourly).
        run_at_ts: Once only — Unix timestamp (seconds) of the single fire time.
        skills: Optional skill names to load at fire time, comma-separated or list.
        workspace: Optional workspace directory for the fire session.
        enabled: Create enabled (true) or paused (false). Default true.

    Returns:
        {"status": "success", "task": {id, next_run_ts, ...}} or {"status": "error"}.
    """
    if not (prompt or "").strip():
        return _err("prompt 不能为空：请说明到点后要执行什么")
    payload: dict[str, Any] = {
        "name": (name or "").strip() or "Untitled",
        "prompt": prompt,
        "schedule": _build_schedule(schedule_type, time, weekdays, interval_seconds, run_at_ts),
        "skills": _norm_skills(skills),
        "workspace": (workspace or "").strip(),
        "enabled": bool(enabled),
    }
    task = _mgr().create_task(payload)
    return _ok(
        f"已创建定时任务「{task.get('name')}」（schedule={task.get('schedule')}）",
        task=task,
    )


def list_tasks() -> dict[str, Any]:
    """
    List this agent's scheduled tasks with their schedule, next fire time and
    last run status.

    Args:
        (none)

    Returns:
        {"status": "success", "count": N, "tasks": [...]}.
    """
    tasks = _mgr().list_tasks()
    return _ok(f"共 {len(tasks)} 个定时任务", count=len(tasks), tasks=tasks)


def update_task(
    task_id: str = "",
    name: str | None = None,
    prompt: str | None = None,
    schedule_type: str | None = None,
    time: str | None = None,
    weekdays: str | None = None,
    interval_seconds: int | None = None,
    run_at_ts: float | None = None,
    skills: str | list[str] | None = None,
) -> dict[str, Any]:
    """
    Update an existing scheduled task. Only the fields you pass are changed.

    Args:
        task_id: Task id (from list_tasks).
        name: New task name.
        prompt: New fire-time prompt.
        schedule_type: "daily" | "weekly" | "interval" | "once".
        time: New fire time "HH:MM" (daily/weekly).
        weekdays: New weekday list (weekly), e.g. "0,2,4".
        interval_seconds: New interval in seconds (interval).
        run_at_ts: New Unix timestamp (once).
        skills: New skill list (pass empty string to clear).

    Returns:
        {"status": "success", "task": {...}} or {"status": "error"} if not found.
    """
    if not (task_id or "").strip():
        return _err("task_id 不能为空（用 list_tasks 查询）")
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if prompt is not None:
        payload["prompt"] = prompt
    if skills is not None:
        payload["skills"] = _norm_skills(skills)
    if (
        schedule_type is not None
        or time is not None
        or weekdays is not None
        or interval_seconds is not None
        or run_at_ts is not None
    ):
        payload["schedule"] = _build_schedule(
            schedule_type or "daily",
            time or "09:00",
            weekdays or "0,1,2,3,4,5,6",
            interval_seconds or 0,
            run_at_ts or 0,
        )
    task = _mgr().update_task(task_id.strip(), payload)
    if task is None:
        return _err(f"任务 {task_id} 不存在")
    return _ok(f"已更新定时任务「{task.get('name')}」", task=task)


def delete_task(task_id: str = "") -> dict[str, Any]:
    """
    Permanently remove a scheduled task (including its schedule).

    Args:
        task_id: Task id (from list_tasks).

    Returns:
        {"status": "success"} or {"status": "error"} if not found.
    """
    if not (task_id or "").strip():
        return _err("task_id 不能为空（用 list_tasks 查询）")
    if not _mgr().delete_task(task_id.strip()):
        return _err(f"任务 {task_id} 不存在")
    return _ok(f"已删除定时任务 {task_id}")


def set_task_enabled(task_id: str = "", enabled: bool = True) -> dict[str, Any]:
    """
    Pause (enabled=false) or resume (enabled=true) a scheduled task without
    deleting it. Paused tasks keep their config but never fire.

    Args:
        task_id: Task id (from list_tasks).
        enabled: true = resume, false = pause. Default true.

    Returns:
        {"status": "success", "task": {...}} or {"status": "error"} if not found.
    """
    if not (task_id or "").strip():
        return _err("task_id 不能为空（用 list_tasks 查询）")
    task = _mgr().set_enabled(task_id.strip(), bool(enabled))
    if task is None:
        return _err(f"任务 {task_id} 不存在")
    state = "恢复" if enabled else "暂停"
    return _ok(f"已{state}定时任务「{task.get('name')}」", task=task)


def run_task_now(task_id: str = "") -> dict[str, Any]:
    """
    Trigger a scheduled task immediately, regardless of its schedule. Useful
    to verify a newly created task actually works.

    Args:
        task_id: Task id (from list_tasks).

    Returns:
        {"status": "success", "execution": {...}} or {"status": "error"} if not found.
    """
    if not (task_id or "").strip():
        return _err("task_id 不能为空（用 list_tasks 查询）")
    task = _mgr().run_now(task_id.strip())
    if task is None:
        return _err(f"任务 {task_id} 不存在")
    if task.get("already_running"):
        return _ok("该任务正在执行中，已跳过重复触发", task=task)
    return _ok(f"已手动触发「{task.get('name')}」", task=task)
