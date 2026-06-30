"""
task_supervisor.py — Active Task Supervision Engine ("打卡制度")

Unlike the passive heartbeat/checkpoint system (Layer 1-4), this is an
**agent-initiated** supervision mechanism. The agent decides to activate it
when starting a complex long-running task, and the supervisor actively
"pokes" the agent if it goes quiet — like a project manager checking in.

Flow:
  1. Agent starts complex task → calls task_watch.start(description, interval)
  2. Supervisor starts a background timer
  3. Agent works, periodically calling task_watch.update(progress)
     - Each update resets the inactivity timer
  4. If no update/activity for `interval` seconds:
     - Supervisor injects a wake-up message via input_hub
     - Agent receives it, self-checks, and either continues or reports
  5. Agent completes task → calls task_watch.complete(summary)
     - Supervisor stops the timer, logs the completion

The key insight: this catches failures where the agent is technically alive
but the task silently stalled (malformed tool call → text output → turn ends →
agent goes idle without completing the task).
"""

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# EventBus integration - emit events for plugin consumption (task_watch dashboard)
try:
    from opensquad.events import bus as _event_bus
except Exception:
    _event_bus = None


def _emit(event: str, **kwargs):
    """Emit a task_supervisor event on the EventBus (best-effort, never raises)."""
    if _event_bus is None:
        return
    with contextlib.suppress(Exception):
        _event_bus.emit("task_supervisor", {"event": event, **kwargs})


class _SupervisedTask:
    """State for a single supervised task."""

    def __init__(
        self,
        task_id: str,
        description: str,
        check_interval: int = 120,
        max_stalls: int = 5,
    ):
        self.task_id = task_id
        self.description = description
        self.check_interval = check_interval  # seconds between check-ins
        self.max_stalls = max_stalls  # max consecutive stalls before escalation
        self.stall_count = 0  # consecutive stalls without agent progress
        self.created_at = time.time()
        self.last_activity_time = time.time()
        self.progress_log: list[dict[str, Any]] = []
        self.status = "active"  # active | completed | abandoned
        self.monitor_task: asyncio.Task | None = None
        self._monitor_future = None  # concurrent.futures.Future from run_coroutine_threadsafe


class TaskSupervisor:
    """
    Singleton active task supervision engine.

    The agent activates supervision by calling tools; the supervisor
    runs a background asyncio loop that injects wake-up messages when
    the agent goes quiet.

    IMPORTANT: Tool functions are sync and run in a thread pool executor.
    We must store the event loop reference from the main thread and use
    asyncio.run_coroutine_threadsafe() for cross-thread scheduling.
    """

    def __init__(self):
        self._current: _SupervisedTask | None = None
        self._history: list[dict[str, Any]] = []  # completed/abandoned tasks
        self._loop: asyncio.AbstractEventLoop | None = None  # main event loop ref

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """
        Store a reference to the main asyncio event loop.
        Must be called from the event loop thread (e.g., during runner init).
        """
        self._loop = loop
        logger.info(f"[TaskSupervisor] Event loop registered: {loop}")

    @property
    def is_active(self) -> bool:
        return self._current is not None and self._current.status == "active"

    @property
    def current_task(self) -> _SupervisedTask | None:
        return self._current

    def _send_heartbeat(self, event: str, task_id: str, detail: str = ""):
        """Send lightweight heartbeat HTTP POST to the parent launcher process."""
        try:
            import json
            import os
            import urllib.request

            launcher_url = (
                f"http://127.0.0.1:{os.environ.get('OPENSQUAD_LAUNCHER_PORT', '9600')}/_internal/task_watch_heartbeat"
            )
            data = json.dumps(
                {
                    "agent_id": os.environ.get("OPENSQUAD_AGENT_ID", ""),
                    "event": event,
                    "task_id": task_id,
                    "detail": detail,
                    "timestamp": time.time(),
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                launcher_url, data=data, method="POST", headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass  # Non-critical — launcher may be restarting

    def start(
        self,
        description: str,
        check_interval: int = 120,
        max_stalls: int = 5,
    ) -> str:
        """
        Start supervising a new task.

        If a previous task is still active, it is auto-abandoned (logged).
        Returns the task_id.
        """
        # Auto-abandon previous task if still active
        if self._current and self._current.status == "active":
            logger.warning(f"[TaskSupervisor] Auto-abandoning previous task '{self._current.task_id}' to start new one")
            self._current.status = "abandoned"
            _emit(
                "abandon",
                task_id=self._current.task_id,
                description=self._current.description,
                detail="auto-abandoned: new task started",
                elapsed_sec=round(time.time() - self._current.created_at, 1),
            )
            self._cancel_monitor()  # Cancel monitor BEFORE archiving (archive sets _current = None)
            self._archive_current()

        task_id = f"tw_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._current = _SupervisedTask(
            task_id=task_id,
            description=description,
            check_interval=check_interval,
            max_stalls=max_stalls,
        )

        # Start background monitor
        # CRITICAL: tool functions run in a thread pool executor (registry.py),
        # so we cannot use asyncio.get_event_loop() or loop.create_task() here
        # — they are not thread-safe from worker threads.
        # Instead, use the pre-stored event loop + run_coroutine_threadsafe().
        loop = self._loop
        if loop is None:
            # Fallback: try to get the running loop (works if called from event loop thread)
            try:
                loop = asyncio.get_running_loop()
                self._loop = loop
            except RuntimeError:
                pass

        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._monitor_loop(task_id), loop)
            # Wrap the concurrent.futures.Future so we can cancel it later
            self._current._monitor_future = future
            logger.info(f"[TaskSupervisor] Monitor loop scheduled via run_coroutine_threadsafe for {task_id}")
        else:
            logger.warning(f"[TaskSupervisor] No running event loop available, monitor NOT started for {task_id}")

        logger.info(
            f"[TaskSupervisor] Started supervision: {task_id} (interval={check_interval}s, max_stalls={max_stalls})"
        )
        _emit("start", task_id=task_id, description=description, check_interval=check_interval, max_stalls=max_stalls)
        self._send_heartbeat("start", task_id, description)
        return task_id

    def report_activity(self):
        """
        Report that the agent is still active (e.g., tool call executed).
        Resets the inactivity timer without requiring an explicit progress update.
        Called automatically by runner.py on each tool execution.
        """
        if self._current and self._current.status == "active":
            self._current.last_activity_time = time.time()
            # Reset stall count on any activity
            if self._current.stall_count > 0:
                logger.info(
                    f"[TaskSupervisor] Activity detected, resetting stall count ({self._current.stall_count} -> 0)"
                )
                self._current.stall_count = 0

    def update_progress(self, progress_text: str) -> bool:
        """
        Agent explicitly reports progress. Resets timer + logs the update.
        Returns True if there's an active task.
        """
        if not self._current or self._current.status != "active":
            return False

        self._current.last_activity_time = time.time()
        self._current.stall_count = 0
        self._current.progress_log.append(
            {
                "time": datetime.now().isoformat(),
                "elapsed": time.time() - self._current.created_at,
                "text": progress_text[:500],
            }
        )
        logger.info(f"[TaskSupervisor] Progress update on {self._current.task_id}: {progress_text[:100]}")
        _emit(
            "update",
            task_id=self._current.task_id,
            description=self._current.description,
            detail=progress_text[:500],
            elapsed_sec=round(time.time() - self._current.created_at, 1),
        )
        self._send_heartbeat("update", self._current.task_id, progress_text[:100])
        return True

    def complete(self, summary: str = "") -> dict[str, Any]:
        """
        Mark the current task as completed. Stops the monitor.
        Returns a summary dict.
        """
        if not self._current:
            return {"status": "error", "message": "No active supervised task"}

        self._current.status = "completed"
        elapsed = time.time() - self._current.created_at
        result = {
            "task_id": self._current.task_id,
            "description": self._current.description,
            "status": "completed",
            "elapsed_seconds": round(elapsed, 1),
            "progress_updates": len(self._current.progress_log),
            "stalls_recovered": sum(1 for _ in self._current.progress_log),  # approximate
            "summary": summary[:500],
        }

        logger.info(
            f"[TaskSupervisor] Task completed: {self._current.task_id} "
            f"({elapsed:.0f}s, {len(self._current.progress_log)} updates)"
        )
        _emit(
            "complete",
            task_id=self._current.task_id,
            description=self._current.description,
            detail=summary[:500],
            elapsed_sec=round(elapsed, 1),
            stall_count=self._current.stall_count,
        )
        self._send_heartbeat("complete", self._current.task_id, summary[:100])
        self._cancel_monitor()  # Cancel monitor BEFORE archiving (archive sets _current = None)
        self._archive_current()
        return result

    def get_status(self) -> dict[str, Any]:
        """Get current supervision status (for diagnostics)."""
        if not self._current:
            return {"active": False, "history_count": len(self._history), "agent_state": "unknown"}

        t = self._current
        return {
            "active": t.status == "active",
            "task_id": t.task_id,
            "description": t.description[:200],
            "status": t.status,
            "elapsed_seconds": round(time.time() - t.created_at, 1),
            "since_last_activity": round(time.time() - t.last_activity_time, 1),
            "stall_count": t.stall_count,
            "progress_updates": len(t.progress_log),
            "check_interval": t.check_interval,
            "history_count": len(self._history),
            "agent_state": self._get_agent_state(),
        }

    def _get_agent_state(self) -> str:
        """Get current agent state from state_manager (best-effort)."""
        try:
            from opensquad.state_manager import state_manager

            # state_manager.get_state() is async, but we're in sync context
            # Use the internal state dict directly
            return state_manager._state.get("ai_state", "unknown")
        except Exception:
            return "unknown"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _monitor_loop(self, task_id: str):
        """
        Background loop: check for inactivity and inject wake-up messages.
        Runs until the task is completed/abandoned or max_stalls exceeded.
        """
        logger.info(f"[TaskSupervisor] Monitor loop started for {task_id}")

        while True:
            # Check if task still active
            if not self._current or self._current.task_id != task_id or self._current.status != "active":
                logger.info(f"[TaskSupervisor] Monitor loop ending for {task_id} (task no longer active)")
                break

            # Wait for the check interval
            await asyncio.sleep(self._current.check_interval)

            # Re-check after sleep
            if not self._current or self._current.task_id != task_id or self._current.status != "active":
                break

            # Check inactivity
            elapsed_since_activity = time.time() - self._current.last_activity_time
            if elapsed_since_activity >= self._current.check_interval:
                self._current.stall_count += 1
                stall = self._current.stall_count
                max_s = self._current.max_stalls

                if stall > max_s:
                    # Too many stalls — escalate: abandon and notify
                    logger.warning(f"[TaskSupervisor] Task {task_id} exceeded max stalls ({stall}/{max_s}), abandoning")
                    self._current.status = "abandoned"
                    _emit(
                        "abandon",
                        task_id=task_id,
                        description=self._current.description,
                        stall_count=stall,
                        elapsed_sec=round(time.time() - self._current.created_at, 1),
                    )
                    self._inject_message(
                        f"[TASK_SUPERVISOR] Task '{self._current.description[:100]}' "
                        f"has been ABANDONED after {stall} consecutive check-ins "
                        f"with no progress. The task supervision has been stopped. "
                        f"If the task is still needed, please restart it or ask the user for guidance."
                    )
                    self._archive_current()
                    break

                # Inject wake-up message
                logger.info(
                    f"[TaskSupervisor] Stall #{stall}/{max_s} on {task_id} (inactive {elapsed_since_activity:.0f}s)"
                )
                _emit(
                    "stall",
                    task_id=task_id,
                    description=self._current.description,
                    stall_count=stall,
                    elapsed_sec=round(time.time() - self._current.created_at, 1),
                )
                self._inject_checkin_message(stall, max_s)

        logger.info(f"[TaskSupervisor] Monitor loop ended for {task_id}")

    def _inject_checkin_message(self, stall_count: int, max_stalls: int):
        """Inject a check-in message into input_hub to wake the agent."""
        if not self._current:
            return

        desc = self._current.description[:150]
        interval = self._current.check_interval
        elapsed = time.time() - self._current.created_at

        # Build progressively more urgent messages
        if stall_count <= 2:
            urgency = "REMINDER"
            tone = f"No tool activity detected for {interval}s. Please report your current status and continue working."
        elif stall_count <= 4:
            urgency = "WARNING"
            tone = (
                f"No progress detected for {stall_count * interval}s. "
                f"If you're stuck, try a different approach. "
                f"If the task is blocked, report the issue."
            )
        else:
            urgency = "URGENT"
            tone = (
                f"No progress for {stall_count * interval}s — this is check-in "
                f"{stall_count}/{max_stalls} before auto-abandonment. "
                f"You MUST either continue the task or call task_watch.complete() "
                f"to end supervision."
            )

        message = (
            f"[TASK_SUPERVISOR:{urgency}] "
            f'Active task: "{desc}"\n'
            f"Task elapsed: {elapsed:.0f}s | Stall #{stall_count}/{max_stalls}\n"
            f"{tone}\n\n"
            f"Actions: call task_watch.update(progress) to report progress, "
            f"or task_watch.complete(summary) if finished."
        )

        self._inject_message(message)

    def _inject_message(self, message: str):
        """
        Push a message into the agent's input_hub.

        Called from _monitor_loop (runs in event loop) so input_hub.push()
        is called from the event loop thread — asyncio.Queue.put_nowait() is safe.
        """
        try:
            from opensquad.input_hub import input_hub

            input_hub.push(message, source="task_supervisor")
            logger.info(f"[TaskSupervisor] Injected message ({len(message)} chars)")
        except Exception as e:
            logger.error(f"[TaskSupervisor] Failed to inject message: {e}", exc_info=True)

    def _cancel_monitor(self):
        """Cancel the background monitor task.

        CRITICAL: Must set task status to non-active BEFORE cancelling, because
        the monitor loop checks self._current.status after waking from sleep.
        Without this, there's a race where the monitor sees status still "active",
        continues execution, and injects a stall message after the task was
        supposedly completed/abandoned.
        """
        if not self._current:
            return
        # Mark status as non-active FIRST — this is the cancellation signal the
        # monitor loop checks at lines 290 and 302.
        if self._current.status == "active":
            self._current.status = "completed"
        # Cancel asyncio.Task (if created directly)
        if self._current.monitor_task:
            with contextlib.suppress(Exception):
                self._current.monitor_task.cancel()
            self._current.monitor_task = None
        # Cancel concurrent.futures.Future (from run_coroutine_threadsafe)
        if hasattr(self._current, "_monitor_future") and self._current._monitor_future:
            with contextlib.suppress(Exception):
                self._current._monitor_future.cancel()
            self._current._monitor_future = None

    def _archive_current(self):
        """Move the current task to history."""
        if not self._current:
            return
        self._history.append(
            {
                "task_id": self._current.task_id,
                "description": self._current.description[:200],
                "status": self._current.status,
                "created_at": datetime.fromtimestamp(self._current.created_at).isoformat(),
                "elapsed_seconds": round(time.time() - self._current.created_at, 1),
                "progress_updates": len(self._current.progress_log),
                "stall_count": self._current.stall_count,
            }
        )
        # Keep only last 20 tasks in history
        if len(self._history) > 20:
            self._history = self._history[-20:]
        self._current = None


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------
task_supervisor = TaskSupervisor()
