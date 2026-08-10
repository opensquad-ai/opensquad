"""
task_watch — Active Task Supervision Tools ("打卡制度")

Provides tools for the agent to manage active task supervision:
  - start:    "Clock in" — begin supervised work on a complex task
  - update:   "Progress report" — report progress, reset inactivity timer
  - complete: "Clock out" — mark the task as done, stop supervision
  - status:   Check current supervision status

When supervision is active, a background timer watches for inactivity.
If the agent goes quiet (no tool calls, no progress updates) for the
configured interval AND the agent is in idle state, the supervisor
injects a reminder message via input_hub.

Enhanced features:
  - Agent state awareness: Only sends reminders when agent is idle
  - Configurable reminder interval: Customize check-in frequency
  - Smart reminders: Asks agent to report progress or complete task
"""

import asyncio
import logging
from typing import Any

from opensquad.task_supervisor import task_supervisor

logger = logging.getLogger(__name__)


def start(
    description: str = "",
    *,
    task_description: str | None = None,
    check_interval: int = 120,
    max_stalls: int = 5,
    reminder_interval: int = 300,
    enable_reminder: bool = True,
) -> dict[str, Any]:
    """
    Start active task supervision with optional smart reminders.

    Args:
        description: Clear description of the task objective.
        task_description: Deprecated alias for ``description`` (kept for back-compat).
        check_interval: Seconds of inactivity before a check-in is triggered. Default 120.
        max_stalls: Maximum consecutive stalls before the task is auto-abandoned. Default 5.
        reminder_interval: Seconds between smart reminders when agent is idle. Default 300 (5 min).
            Only sends reminder if agent has been idle for this duration AND no progress reported.
        enable_reminder: Whether to enable smart reminders. Default True.

    Returns:
        Task ID and confirmation message.
    """
    desc = (description or "").strip() or (task_description or "").strip() or "Untitled task"
    task_id = task_supervisor.start(
        description=desc,
        check_interval=check_interval,
        max_stalls=max_stalls,
    )

    # Start smart reminder if enabled
    if enable_reminder:
        _start_smart_reminder(task_id, desc, reminder_interval)

    return {
        "status": "success",
        "task_id": task_id,
        "reminder_enabled": enable_reminder,
        "reminder_interval": reminder_interval,
        "message": (
            f"Task supervision started (task_id={task_id}).\n"
            f"- Check interval: {check_interval}s\n"
            f"- Max stalls: {max_stalls}\n"
            f"- Smart reminder: {'enabled' if enable_reminder else 'disabled'}"
            + (f" (every {reminder_interval}s)" if enable_reminder else "")
            + "\n\n"
            "Use task_watch.update(progress) to report progress.\n"
            "Use task_watch.complete(summary) when finished."
        ),
    }


def _start_smart_reminder(task_id: str, description: str, interval: int):
    """Start a background smart reminder that checks agent state."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(_smart_reminder_loop(task_id, description, interval), loop)
        logger.info(f"[TaskWatch] Smart reminder started for {task_id} (interval={interval}s)")
    else:
        logger.warning("[TaskWatch] No running event loop, smart reminder not started")


async def _smart_reminder_loop(task_id: str, description: str, interval: int):
    """
    Background loop that sends reminders when agent is idle.

    Logic:
    1. Wait for reminder_interval seconds
    2. Check if task is still active
    3. Check if agent state is idle
    4. Check if no progress reported since last check
    5. If all conditions met, send reminder via input_hub
    """
    last_progress_count = 0

    while True:
        await asyncio.sleep(interval)

        # Check if task still active
        if not task_supervisor.is_active:
            logger.info("[TaskWatch] Smart reminder ending: task no longer active")
            break

        current_task = task_supervisor.current_task
        if not current_task or current_task.task_id != task_id:
            logger.info("[TaskWatch] Smart reminder ending: task changed")
            break

        # Check agent state
        agent_state = task_supervisor._get_agent_state()

        # Only remind if agent is idle
        if agent_state != "idle":
            logger.debug(f"[TaskWatch] Skipping reminder: agent state={agent_state}")
            continue

        # Check if progress was reported since last check
        current_progress_count = len(current_task.progress_log)
        if current_progress_count > last_progress_count:
            # Progress was made, reset timer
            last_progress_count = current_progress_count
            logger.debug("[TaskWatch] Progress detected, resetting reminder timer")
            continue

        # Check time since last activity
        import time

        elapsed_since_activity = time.time() - current_task.last_activity_time

        # Only remind if inactive for the full interval
        if elapsed_since_activity < interval:
            continue

        # Send reminder!
        elapsed_total = time.time() - current_task.created_at

        message = (
            f'[TASK_WATCH:REMINDER] Task: "{description[:100]}"\n'
            f"Status: Agent has been idle for {elapsed_since_activity:.0f}s\n"
            f"Task elapsed: {elapsed_total:.0f}s\n"
            f"Progress updates: {current_progress_count}\n\n"
            f"Please:\n"
            f"1. Report your current progress with task_watch.update(progress)\n"
            f"2. Or complete the task with task_watch.complete(summary) if finished\n\n"
            f"If you're waiting for external input, you can ignore this reminder."
        )

        _inject_reminder(message)
        logger.info(f"[TaskWatch] Smart reminder sent for {task_id}")


def _inject_reminder(message: str):
    """Push a reminder message into the agent's input_hub."""
    try:
        from opensquad.input_hub import input_hub

        input_hub.push(message, source="task_watch")
        logger.info(f"[TaskWatch] Injected reminder ({len(message)} chars)")
    except Exception as e:
        logger.error(f"[TaskWatch] Failed to inject reminder: {e}", exc_info=True)


def update(progress: str) -> dict[str, Any]:
    """
    Report progress on the current supervised task.
    Resets the inactivity timer and logs the progress update.
    Call this after completing significant sub-steps.

    Args:
        progress: Brief description of what was just accomplished or current status.
    """
    ok = task_supervisor.update_progress(progress)
    if ok:
        status_info = task_supervisor.get_status()
        return {
            "status": "success",
            "message": "Progress recorded, timer reset.",
            "elapsed_seconds": status_info.get("elapsed_seconds", 0),
            "progress_updates_count": status_info.get("progress_updates", 0),
        }
    return {
        "status": "error",
        "message": "No active supervised task. Call task_watch.start() first.",
    }


def complete(summary: str = "") -> dict[str, Any]:
    """
    Mark the current supervised task as completed and stop supervision.
    Call this when the task objective has been achieved.

    Args:
        summary: Brief summary of the final result or outcome.
    """
    result = task_supervisor.complete(summary)
    if result.get("status") == "error":
        return result
    return {
        "status": "success",
        "task_id": result.get("task_id", ""),
        "elapsed_seconds": result.get("elapsed_seconds", 0),
        "progress_updates": result.get("progress_updates", 0),
        "message": (
            f"Task supervision ended. Total time: {result.get('elapsed_seconds', 0):.0f}s, "
            f"progress updates: {result.get('progress_updates', 0)}."
        ),
    }


def status() -> dict[str, Any]:
    """
    Get the current task supervision status.
    Returns information about the active supervised task, if any.
    """
    return task_supervisor.get_status()
