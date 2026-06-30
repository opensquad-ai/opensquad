"""
Task Watch Plugin (Decorator-based API)

Visualisation companion for the task_supervisor engine. Collects and
persists task lifecycle events and tool activity for the dashboard.

Data sources:
1. on_after_tool hook: records every tool invocation timestamp + agent_id
2. EventBus subscriptions: listens for task_supervisor lifecycle events
   emitted by runner.py (task_started, task_stall, task_completed, etc.)
3. State change listener: monitors agent state (idle/working/sleeping)
4. Periodic poll of TaskSupervisor.get_status() for live state

All data is persisted to:
    data/plugins/task_watch/task_watch.db
"""

import logging
import os
from typing import Any

from opensquad.plugin_api import Context, Plugin, hook, on_event, register

from .storage import TaskWatchStorage

logger = logging.getLogger("plugins.task_watch")


@register(
    name="task_watch",
    author="OpenSquad",
    description="Task supervision dashboard — monitors agent task lifecycle, check-ins, stalls, and tool activity",
    version="1.0.0",
    plugin_type="hook",
    display_name="Task Watch",
    config_schema={
        "db_path": {
            "type": "string",
            "default": "data/plugins/task_watch/task_watch.db",
            "description": "SQLite database file path (relative to project root)",
        },
    },
    contributes={
        "views": [
            {
                "name": "task_dashboard",
                "title": "Task Watch",
                "icon": "ClipboardList",
                "data_endpoint": "/api/plugins/task_watch/data",
            }
        ]
    },
    tags=["monitoring", "supervision"],
)
class TaskWatchPlugin(Plugin):
    """
    Collects task supervision events and tool activity for the dashboard.

    Enhanced: Now monitors agent state changes (idle/working/sleeping) via state_manager.
    """

    def __init__(self, context: Context):
        super().__init__(context)
        self._storage: TaskWatchStorage = None
        self._state_listener_registered = False

    def on_load(self) -> None:
        config = self.context.config
        db_rel = config.get("db_path", "data/plugins/task_watch/task_watch.db")
        db_path = os.path.join(self.context.project_root, db_rel)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self._storage = TaskWatchStorage(db_path=db_path)

        # Register state_manager listener to monitor agent state changes
        self._register_state_listener()

        logger.info(f"[TaskWatch] Initialized (agent={self.context.agent_id}, db={db_path})")

    def _register_state_listener(self):
        """Register listener for agent state changes (idle/working/sleeping)."""
        try:
            from opensquad.state_manager import state_manager

            state_manager.add_listener(self._on_state_change)
            self._state_listener_registered = True
            logger.info("[TaskWatch] State change listener registered")
        except Exception as e:
            logger.warning(f"[TaskWatch] Failed to register state listener: {e}")

    def _on_state_change(self, old_state: str, new_state: str):
        """
        Callback when agent state changes.

        This is called by state_manager when:
        - idle -> working: agent starts processing
        - working -> idle: agent finishes processing
        - working -> sleeping: agent enters sleep (system.wait)
        - sleeping -> idle: agent wakes up
        """
        logger.info(f"[TaskWatch] Agent state changed: {old_state} -> {new_state}")

        # Report activity to task_supervisor on ANY state change
        # This means: state transitions count as "activity"
        try:
            from opensquad.task_supervisor import task_supervisor

            task_supervisor.report_activity()
        except Exception:
            pass

        # Record state change as a special tool activity
        if self._storage:
            try:
                self._storage.record_tool_activity(
                    agent_id=self.context.agent_id,
                    tool_name=f"state:{new_state}",
                    success=True,
                )
            except Exception as e:
                logger.error(f"[TaskWatch] Error recording state change: {e}")

    @hook.on_after_tool
    async def track_tool_activity(self, context: dict[str, Any]) -> dict[str, Any]:
        """Record every tool call for the activity timeline."""
        if not self._storage:
            return context

        try:
            tool_name = context.get("tool_name", "unknown")
            agent_id = context.get("agent_id", "") or self.context.agent_id
            result = context.get("result", "")
            # Heuristic: if result contains "error" key, mark as failure
            success = True
            if (isinstance(result, dict) and result.get("error")) or (
                isinstance(result, str) and result.startswith("Error")
            ):
                success = False

            self._storage.record_tool_activity(
                agent_id=agent_id,
                tool_name=tool_name,
                success=success,
            )
        except Exception as e:
            logger.error(f"[TaskWatch] Error recording tool activity: {e}", exc_info=True)

        # Also report activity to task_supervisor (resets stall timer)
        try:
            from opensquad.task_supervisor import task_supervisor

            task_supervisor.report_activity()
        except Exception:
            pass

        return context

    @on_event("task_supervisor")
    def handle_task_event(self, event_data: dict[str, Any]) -> None:
        """
        EventBus callback: record task lifecycle events.

        event_data format (emitted by runner.py / task_supervisor):
        {
            "event": "start" | "update" | "stall" | "complete" | "abandon",
            "task_id": "tw_...",
            "agent_id": "...",
            "description": "...",
            "detail": "...",
            "stall_count": N,
            "elapsed_sec": N.N,
        }
        """
        if not self._storage:
            return

        try:
            self._storage.record_task_event(
                event_type=event_data.get("event", "unknown"),
                task_id=event_data.get("task_id", ""),
                agent_id=event_data.get("agent_id", "") or self.context.agent_id,
                description=event_data.get("description", ""),
                detail=event_data.get("detail", ""),
                stall_count=event_data.get("stall_count", 0),
                elapsed_sec=event_data.get("elapsed_sec", 0),
            )
        except Exception as e:
            logger.error(f"[TaskWatch] Error recording task event: {e}", exc_info=True)

    def on_unload(self) -> None:
        # Unregister state listener
        if self._state_listener_registered:
            try:
                from opensquad.state_manager import state_manager

                state_manager.remove_listener(self._on_state_change)
                logger.info("[TaskWatch] State change listener unregistered")
            except Exception:
                pass

        if self._storage:
            self._storage.close()
            self._storage = None
