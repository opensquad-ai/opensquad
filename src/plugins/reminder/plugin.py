"""
Reminder Plugin - Scheduled notification plugin

Supports three trigger modes:
  1. Delayed trigger:   delay_seconds / delay_minutes / delay_hours / delay_days (combinable)
  2. Absolute time:     datetime_str="YYYY-MM-DD HH:MM:SS"
  3. Recurring trigger: set_recurring() — daily/weekly at a specified time, or fixed interval loop

Supports two delivery channels:
  - "agent" (default): pushes the reminder into the input_hub queue to wake the agent
  - "group" / "dm" :   sends via im.send_message to a specified group or direct message

After a process restart, unfired reminders (including recurring ones) are automatically
restored from the persisted JSON file.

Usage examples (agent calls):
  reminder.set(message="Meeting reminder", delay_minutes=30)
  reminder.set_at(message="Project deadline", datetime_str="2026-03-01 09:00:00")
  reminder.set_recurring(message="Daily briefing", recur_type="daily", time="09:00")
  reminder.set_recurring(message="Weekly meeting", recur_type="weekly", time="09:30", weekdays="0,2,4")
  reminder.set_recurring(message="Drink water", recur_type="interval", interval_minutes=60)
  reminder.cancel(reminder_id="abc12345")
  reminder.list_reminders()

weekdays convention (matches Python datetime.weekday()):
  0=Mon  1=Tue  2=Wed  3=Thu  4=Fri  5=Sat  6=Sun
"""

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any

from opensquad.input_hub import input_hub
from opensquad.plugin_api import Context, Plugin, register, tool

logger = logging.getLogger("plugins.reminder")


# ── Module-level helpers ───────────────────────────────────────────────────────


def _parse_hhmm(time_str: str):
    """Parse 'HH:MM' or 'HH:MM:SS' into (hour, minute); returns (9, 0) on failure."""
    parts = time_str.strip().split(":")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return 9, 0


@register(
    name="reminder",
    author="OpenSquad",
    description="Scheduled notification plugin: supports delayed (seconds/minutes/hours/days) and absolute time triggers; can push to agent or im group/DM",
    version="1.0.0",
    plugin_type="tool",
    display_name="Reminder",
    tags=["utility"],
)
class ReminderPlugin(Plugin):
    """
    Scheduled reminder plugin.

    Each reminder is driven by a daemon threading.Timer. On process restart,
    unfired reminders are automatically restored from
    data/plugins/reminder/{agent_id}_reminders.json.
    Timer callbacks use loop.call_soon_threadsafe / run_coroutine_threadsafe
    to safely push notifications back to the asyncio main thread.
    """

    def __init__(self, context: Context):
        super().__init__(context)
        # {reminder_id: {message, fire_at_ts, fire_at_str, target_type, target_id,
        #                created_at, recurrence?}}
        # recurrence examples:
        #   {"type": "daily",    "time": "09:00"}
        #   {"type": "weekly",   "time": "09:30", "weekdays": "0,2,4"}
        #   {"type": "interval", "total_seconds": 3600}
        self._reminders: dict[str, dict[str, Any]] = {}
        # {reminder_id: threading.Timer}
        self._timers: dict[str, threading.Timer] = {}
        # Pending restart tasks: messages delivered once after agent restart, then cleared
        self._pending_restart_tasks: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._data_file: str = ""

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_load(self) -> None:
        """Called by boot.py during startup (inside an async context)."""
        # Get the current asyncio event loop
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.get_event_loop()

        # Data directory: isolated by agent_id to avoid multiple agents sharing reminders
        agent_id = self.context.agent_id or "default"
        data_dir = self.context.data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._data_file = os.path.join(data_dir, f"{agent_id}_reminders.json")

        # Restore unfired reminders
        self._load_persisted()
        logger.info(
            f"[ReminderPlugin] Loaded: agent={agent_id}, pending={len(self._reminders)}, data={self._data_file}"
        )

    def on_unload(self) -> None:
        """Cancel all pending Timers when the plugin is unloaded."""
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
        logger.info("[ReminderPlugin] Unloaded, all timers cancelled")

    # ── Recurrence calculation ─────────────────────────────────────────────────

    def _compute_next_ts(self, recurrence: dict[str, Any]) -> float | None:
        """
        Compute the next Unix timestamp for a given recurrence rule.
        Returns None if the rule is invalid and the time cannot be computed.
        """
        now = datetime.now()
        rtype = recurrence.get("type", "")

        if rtype == "daily":
            h, m = _parse_hhmm(recurrence.get("time", "09:00"))
            candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate.timestamp()

        elif rtype == "weekly":
            h, m = _parse_hhmm(recurrence.get("time", "09:00"))
            raw = recurrence.get("weekdays", "0,1,2,3,4,5,6")
            try:
                weekdays: list[int] = [int(d.strip()) for d in raw.split(",")]
            except ValueError:
                weekdays = list(range(7))
            # Search up to 7 days ahead from today to find the first matching time
            for delta in range(0, 8):
                cdate = now + timedelta(days=delta)
                if cdate.weekday() in weekdays:
                    candidate = cdate.replace(hour=h, minute=m, second=0, microsecond=0)
                    if candidate > now:
                        return candidate.timestamp()
            return None

        elif rtype == "interval":
            total = recurrence.get("total_seconds", 0)
            if total > 0:
                return now.timestamp() + total
            return None

        return None

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_persisted(self) -> None:
        """Load from JSON file and rebuild Timers for reminders that have not yet fired."""
        if not self._data_file or not os.path.isfile(self._data_file):
            return
        try:
            with open(self._data_file, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"[ReminderPlugin] Failed to read data file: {e}")
            return

        now_ts = datetime.now().timestamp()
        restored = 0
        for rid, r in data.get("reminders", {}).items():
            fire_ts = r.get("fire_at_ts", 0)
            if fire_ts <= now_ts:
                continue  # Already expired, skip
            delay = fire_ts - now_ts
            with self._lock:
                self._reminders[rid] = r
            timer = threading.Timer(delay, self._fire, args=(rid,))
            timer.daemon = True
            timer.start()
            with self._lock:
                self._timers[rid] = timer
            restored += 1

        # ── Fire pending restart tasks (next_restart) immediately ──
        restart_tasks = data.get("next_restart", [])
        if restart_tasks:
            logger.info(f"[ReminderPlugin] Firing {len(restart_tasks)} pending restart task(s) immediately")
            for task in restart_tasks:
                message = task.get("message", "Continue the previous task.")
                target_type = task.get("target_type", "agent")
                target_id = task.get("target_id", "")
                self._deliver(message, target_type, target_id)
            # Clear after firing so they don't fire again on next restart
            self._pending_restart_tasks = []
            self._save_persisted()

        if restored:
            logger.info(f"[ReminderPlugin] Restored {restored} pending reminder(s)")

    def _save_persisted(self) -> None:
        """Persist the current in-memory reminders to JSON (caller must not hold the lock)."""
        if not self._data_file:
            return
        try:
            with self._lock:
                snapshot = dict(self._reminders)
                restart_snapshot = list(self._pending_restart_tasks)
            with open(self._data_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "reminders": snapshot,
                        "next_restart": restart_snapshot,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as e:
            logger.error(f"[ReminderPlugin] Failed to save reminders: {e}")

    # ── Firing and delivery ────────────────────────────────────────────────────

    def _fire(self, reminder_id: str) -> None:
        """
        Timer expiry callback (runs in a daemon thread).

        - One-shot reminder: remove then deliver notification.
        - Recurring reminder: compute and register next fire time first,
          then deliver notification. The same reminder_id is kept so the
          user can cancel the entire series via cancel().
        """
        with self._lock:
            r = self._reminders.pop(reminder_id, None)
            self._timers.pop(reminder_id, None)
        if not r:
            return

        recurrence = r.get("recurrence")
        if recurrence:
            # ── Recurring: schedule next occurrence ───────────────────────────
            next_ts = self._compute_next_ts(recurrence)
            if next_ts:
                next_str = datetime.fromtimestamp(next_ts).strftime("%Y-%m-%d %H:%M:%S")
                next_delay = max(next_ts - datetime.now().timestamp(), 0.1)
                next_r = dict(r)
                next_r["fire_at_ts"] = next_ts
                next_r["fire_at_str"] = next_str
                with self._lock:
                    self._reminders[reminder_id] = next_r
                timer = threading.Timer(next_delay, self._fire, args=(reminder_id,))
                timer.daemon = True
                timer.start()
                with self._lock:
                    self._timers[reminder_id] = timer
                logger.info(f"[ReminderPlugin] Recurring {reminder_id} rescheduled: next={next_str}")
            else:
                logger.warning(
                    f"[ReminderPlugin] Recurring {reminder_id} could not compute next fire time, stopping recurrence"
                )
        # Persist (next occurrence written for recurring; one-shot has been removed)
        self._save_persisted()

        message = r.get("message", "")
        target_type = r.get("target_type", "agent")
        target_id = r.get("target_id", "")
        logger.info(
            f"[ReminderPlugin] Firing {reminder_id}: target={target_type}/{target_id or 'agent'}, msg={message[:50]}"
        )
        self._deliver(message, target_type, target_id)

    def _deliver(self, message: str, target_type: str, target_id: str) -> None:
        """
        Deliver a reminder to the target channel
        (called safely from a worker thread into asyncio/input_hub).

        - agent : loop.call_soon_threadsafe -> input_hub queue
        - group/dm : run_coroutine_threadsafe -> im.send_message
        """
        if not self._loop or self._loop.is_closed():
            logger.error("[ReminderPlugin] Event loop unavailable, cannot deliver reminder")
            return

        if target_type in ("group", "dm"):
            # ── IM send path: call sync function inside the event loop thread ──
            async def _im_task():
                try:
                    from opensquad.tools import im as im_module

                    im_module.send_message(
                        content=f"[Reminder] {message}",
                        target_id=target_id,
                        target_type=target_type,
                    )
                except Exception as e:
                    logger.error(f"[ReminderPlugin] im.send_message failed: {e}")

            asyncio.run_coroutine_threadsafe(_im_task(), self._loop)

        else:
            # ── Agent wake-up path: call_soon_threadsafe -> input_hub queue ──
            payload = {
                "source": "reminder",
                "content": f"[Reminder] {message}",
            }

            def _push_to_queue():
                try:
                    input_hub._get_queue().put_nowait(payload)
                except Exception as e:
                    logger.error(f"[ReminderPlugin] input_hub push failed: {e}")

            self._loop.call_soon_threadsafe(_push_to_queue)

    # ── Tool methods (exposed to the agent) ────────────────────────────────────

    @tool(name="reminder", level="extended", auto_register=True)
    def set(
        self,
        message: str,
        delay_seconds: int = 0,
        delay_minutes: int = 0,
        delay_hours: int = 0,
        delay_days: int = 0,
        target_type: str = "agent",
        target_id: str = "",
    ) -> dict[str, Any]:
        """
        Create a delayed reminder. Multiple delay_* parameters can be combined.

        Args:
            message:        Reminder text content.
            delay_seconds:  Delay in seconds (default 0).
            delay_minutes:  Delay in minutes (default 0).
            delay_hours:    Delay in hours (default 0).
            delay_days:     Delay in days (default 0).
            target_type:    Delivery channel:
                              "agent" (default) — push to agent input queue to wake agent;
                              "group"           — send to group chat (target_id required);
                              "dm"              — send direct message (target_id required).
            target_id:      Group ID or username (required when target_type is group/dm).

        Returns:
            {
              "success": true,
              "reminder_id": "abc12345",
              "fire_at": "2026-02-19 10:30:00",
              "delay_total_seconds": 1800
            }
        """
        total = delay_days * 86400 + delay_hours * 3600 + delay_minutes * 60 + delay_seconds
        if total <= 0:
            return {
                "success": False,
                "error": "Total delay must be greater than 0 seconds; please specify at least one delay_* parameter.",
            }
        if target_type in ("group", "dm") and not target_id:
            return {
                "success": False,
                "error": f"target_id cannot be empty when target_type='{target_type}'",
            }

        rid = uuid.uuid4().hex[:8]
        fire_ts = datetime.now().timestamp() + total
        fire_str = datetime.fromtimestamp(fire_ts).strftime("%Y-%m-%d %H:%M:%S")

        r = {
            "message": message,
            "fire_at_ts": fire_ts,
            "fire_at_str": fire_str,
            "target_type": target_type,
            "target_id": target_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self._lock:
            self._reminders[rid] = r

        timer = threading.Timer(total, self._fire, args=(rid,))
        timer.daemon = True
        timer.start()
        with self._lock:
            self._timers[rid] = timer

        self._save_persisted()
        logger.info(
            f"[ReminderPlugin] Scheduled {rid}: delay={total}s, fire_at={fire_str}, "
            f"target={target_type}/{target_id or 'agent'}"
        )
        return {
            "success": True,
            "reminder_id": rid,
            "fire_at": fire_str,
            "delay_total_seconds": total,
        }

    @tool(name="reminder", level="extended", auto_register=True)
    def set_at(
        self,
        message: str,
        datetime_str: str,
        target_type: str = "agent",
        target_id: str = "",
    ) -> dict[str, Any]:
        """
        Create a reminder that fires at a specific absolute time.

        Args:
            message:      Reminder text content.
            datetime_str: Fire time; supported formats:
                            "YYYY-MM-DD HH:MM:SS"
                            "YYYY-MM-DDTHH:MM:SS"
                            "YYYY-MM-DD HH:MM"
                            "YYYY/MM/DD HH:MM:SS"
            target_type:  Delivery channel, same as set().
            target_id:    Target ID, same as set().

        Returns:
            {
              "success": true,
              "reminder_id": "abc12345",
              "fire_at": "2026-03-01 09:00:00",
              "delay_total_seconds": 864000
            }
        """
        fire_dt = None
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
        ):
            try:
                fire_dt = datetime.strptime(datetime_str.strip(), fmt)
                break
            except ValueError:
                continue

        if fire_dt is None:
            return {
                "success": False,
                "error": (f"Cannot parse time '{datetime_str}'; please use the format 'YYYY-MM-DD HH:MM:SS'"),
            }

        now = datetime.now()
        delay = (fire_dt - now).total_seconds()
        if delay <= 0:
            return {
                "success": False,
                "error": (
                    f"Specified time '{datetime_str}' is in the past "
                    f"(current time: {now.strftime('%Y-%m-%d %H:%M:%S')})"
                ),
            }
        if target_type in ("group", "dm") and not target_id:
            return {
                "success": False,
                "error": f"target_id cannot be empty when target_type='{target_type}'",
            }

        rid = uuid.uuid4().hex[:8]
        fire_ts = fire_dt.timestamp()
        fire_str = fire_dt.strftime("%Y-%m-%d %H:%M:%S")

        r = {
            "message": message,
            "fire_at_ts": fire_ts,
            "fire_at_str": fire_str,
            "target_type": target_type,
            "target_id": target_id,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self._lock:
            self._reminders[rid] = r

        timer = threading.Timer(delay, self._fire, args=(rid,))
        timer.daemon = True
        timer.start()
        with self._lock:
            self._timers[rid] = timer

        self._save_persisted()
        logger.info(
            f"[ReminderPlugin] Scheduled {rid} at {fire_str} "
            f"(delay={delay:.1f}s, target={target_type}/{target_id or 'agent'})"
        )
        return {
            "success": True,
            "reminder_id": rid,
            "fire_at": fire_str,
            "delay_total_seconds": int(delay),
        }

    @tool(name="reminder", level="extended", auto_register=True)
    def set_recurring(
        self,
        message: str,
        recur_type: str = "daily",
        time: str = "09:00",
        weekdays: str = "0,1,2,3,4,5,6",
        interval_seconds: int = 0,
        interval_minutes: int = 0,
        interval_hours: int = 0,
        interval_days: int = 0,
        target_type: str = "agent",
        target_id: str = "",
    ) -> dict[str, Any]:
        """
        Create a recurring reminder that reschedules itself automatically after firing.

        Args:
            message:          Reminder text content.
            recur_type:       Recurrence type, one of:
                                "daily"    — fires every day at the specified time;
                                "weekly"   — fires every week on the specified day(s) and time;
                                "interval" — fires repeatedly at a fixed interval.
            time:             Fire time ("HH:MM" or "HH:MM:SS");
                                only valid for daily/weekly; default "09:00".
            weekdays:         Comma-separated list of weekdays; only valid for weekly;
                                0=Mon … 6=Sun; default "0,1,2,3,4,5,6" (every day).
                                Example: "0,2,4" means Mon/Wed/Fri.
            interval_seconds: interval type: seconds (combinable).
            interval_minutes: interval type: minutes (combinable).
            interval_hours:   interval type: hours (combinable).
            interval_days:    interval type: days (combinable).
            target_type:      Delivery channel, same as set().
            target_id:        Target ID, same as set().

        Returns:
            {
              "success": true,
              "reminder_id": "abc12345",
              "recur_type": "daily",
              "first_fire_at": "2026-02-20 09:00:00"
            }

        Examples:
            reminder.set_recurring(message="Daily briefing", recur_type="daily", time="09:00")
            reminder.set_recurring(message="Weekly meeting", recur_type="weekly",
                                   time="09:30", weekdays="0,2,4")
            reminder.set_recurring(message="Drink water", recur_type="interval",
                                   interval_minutes=60)
        """
        # ── Build recurrence rule ─────────────────────────────────────────────
        if recur_type == "daily":
            recurrence: dict[str, Any] = {"type": "daily", "time": time}

        elif recur_type == "weekly":
            # Validate weekdays format
            try:
                wdays = [int(d.strip()) for d in weekdays.split(",")]
                if not wdays or any(d < 0 or d > 6 for d in wdays):
                    raise ValueError
            except ValueError:
                return {
                    "success": False,
                    "error": "Invalid weekdays format; expected a comma-separated list of 0-6, e.g. '0,2,4'",
                }
            recurrence = {"type": "weekly", "time": time, "weekdays": weekdays}

        elif recur_type == "interval":
            total = interval_days * 86400 + interval_hours * 3600 + interval_minutes * 60 + interval_seconds
            if total <= 0:
                return {
                    "success": False,
                    "error": (
                        "interval type requires at least one interval_* parameter "
                        "and the total duration must be greater than 0 seconds"
                    ),
                }
            recurrence = {"type": "interval", "total_seconds": total}

        else:
            return {
                "success": False,
                "error": f"Unsupported recur_type='{recur_type}'; options: daily / weekly / interval",
            }

        if target_type in ("group", "dm") and not target_id:
            return {
                "success": False,
                "error": f"target_id cannot be empty when target_type='{target_type}'",
            }

        # ── Compute first fire time ───────────────────────────────────────────
        first_ts = self._compute_next_ts(recurrence)
        if first_ts is None:
            return {
                "success": False,
                "error": "Cannot compute the next fire time from the given rule; please check parameters",
            }
        first_str = datetime.fromtimestamp(first_ts).strftime("%Y-%m-%d %H:%M:%S")
        first_delay = max(first_ts - datetime.now().timestamp(), 0.1)

        rid = uuid.uuid4().hex[:8]
        r: dict[str, Any] = {
            "message": message,
            "fire_at_ts": first_ts,
            "fire_at_str": first_str,
            "target_type": target_type,
            "target_id": target_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "recurrence": recurrence,
        }
        with self._lock:
            self._reminders[rid] = r

        timer = threading.Timer(first_delay, self._fire, args=(rid,))
        timer.daemon = True
        timer.start()
        with self._lock:
            self._timers[rid] = timer

        self._save_persisted()
        logger.info(
            f"[ReminderPlugin] Recurring {rid} ({recur_type}): "
            f"first_fire={first_str}, target={target_type}/{target_id or 'agent'}"
        )
        return {
            "success": True,
            "reminder_id": rid,
            "recur_type": recur_type,
            "first_fire_at": first_str,
        }

    @tool(name="reminder", level="extended", auto_register=True)
    def cancel(self, reminder_id: str) -> dict[str, Any]:
        """
        Cancel a pending reminder.

        Args:
            reminder_id: Reminder ID (returned by set / set_at).

        Returns:
            {"success": true, "reminder_id": "abc12345", "message": "original reminder text"}
        """
        with self._lock:
            timer = self._timers.pop(reminder_id, None)
            removed = self._reminders.pop(reminder_id, None)

        if timer:
            timer.cancel()

        if not removed:
            return {
                "success": False,
                "error": f"reminder_id='{reminder_id}' not found; it may have already fired or does not exist",
            }

        self._save_persisted()
        logger.info(f"[ReminderPlugin] Cancelled reminder {reminder_id}")
        return {
            "success": True,
            "reminder_id": reminder_id,
            "message": removed.get("message", ""),
        }

    @tool(name="reminder", level="extended", auto_register=True)
    def list_reminders(self) -> dict[str, Any]:
        """
        List all pending reminders (sorted by fire time, ascending).

        Returns:
            {
              "count": 2,
              "reminders": [
                {
                  "id": "abc12345",
                  "message": "Meeting reminder",
                  "next_fire_at": "2026-02-19 10:30:00",
                  "recurring": false,
                  "target_type": "agent",
                  "target_id": "",
                  "created_at": "2026-02-19 09:00:00"
                },
                {
                  "id": "def67890",
                  "message": "Daily briefing",
                  "next_fire_at": "2026-02-20 09:00:00",
                  "recurring": true,
                  "recur_type": "daily",
                  "recur_detail": "Every day at 09:00",
                  "target_type": "agent",
                  "target_id": "",
                  "created_at": "2026-02-19 08:00:00"
                },
                ...
              ]
            }
        """
        with self._lock:
            snapshot = list(self._reminders.items())

        items = []
        for rid, r in snapshot:
            recurrence = r.get("recurrence")
            item: dict[str, Any] = {
                "id": rid,
                "message": r.get("message", ""),
                "next_fire_at": r.get("fire_at_str", ""),
                "recurring": recurrence is not None,
                "target_type": r.get("target_type", "agent"),
                "target_id": r.get("target_id", ""),
                "created_at": r.get("created_at", ""),
            }
            if recurrence:
                rtype = recurrence.get("type", "")
                item["recur_type"] = rtype
                if rtype == "daily":
                    item["recur_detail"] = f"Every day at {recurrence.get('time', '09:00')}"
                elif rtype == "weekly":
                    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                    raw = recurrence.get("weekdays", "0,1,2,3,4,5,6")
                    try:
                        days_str = "/".join(
                            day_names[int(d.strip())] for d in raw.split(",") if 0 <= int(d.strip()) <= 6
                        )
                    except (ValueError, IndexError):
                        days_str = raw
                    item["recur_detail"] = f"Every week on {days_str} at {recurrence.get('time', '09:00')}"
                elif rtype == "interval":
                    total = recurrence.get("total_seconds", 0)
                    if total >= 86400 and total % 86400 == 0:
                        detail = f"Every {total // 86400} day(s)"
                    elif total >= 3600 and total % 3600 == 0:
                        detail = f"Every {total // 3600} hour(s)"
                    elif total >= 60 and total % 60 == 0:
                        detail = f"Every {total // 60} minute(s)"
                    else:
                        detail = f"Every {total} second(s)"
                    item["recur_detail"] = detail
            items.append(item)

        items.sort(key=lambda x: x["next_fire_at"])
        return {"count": len(items), "reminders": items, "pending_restart_tasks": list(self._pending_restart_tasks)}

    # ── Restart-gated reminder ──────────────────────────────────────────────

    @tool(name="reminder", level="extended", auto_register=True)
    def set_on_next_restart(
        self,
        message: str = "Continue the previous task.",
        target_type: str = "agent",
        target_id: str = "",
    ) -> dict[str, Any]:
        """
        Schedule a reminder that fires ONCE immediately after the next agent restart.
        Unlike set()/set_at() which use wall-clock timers, this guarantee survives
        any number of restarts — the message is delivered on the first boot after
        being registered and then automatically cleared.

        Use this BEFORE calling agent_factory.restart_agent() to resume interrupted work:
          1. reminder.set_on_next_restart(message="Continue the task analysis")
          2. agent_factory.restart_agent("my_agent")
          3. After restart, agent immediately receives: "[Reminder] Continue the task analysis"

        Args:
            message:     Reminder text (default "Continue the previous task.").
            target_type: "agent" (default) — push to agent input queue;
                         "group" or "dm"   — send via im.send_message (target_id required).
            target_id:   Required when target_type is group/dm.

        Returns:
            {"success": true, "message": "...", "expires_on_next_restart": true}
        """
        if target_type in ("group", "dm") and not target_id:
            return {
                "success": False,
                "error": f"target_id cannot be empty when target_type='{target_type}'",
            }
        task = {
            "message": message,
            "target_type": target_type,
            "target_id": target_id,
        }
        with self._lock:
            self._pending_restart_tasks.append(task)
        self._save_persisted()
        logger.info(f"[ReminderPlugin] Registered restart task: msg={message[:60]}")
        return {
            "success": True,
            "message": message,
            "expires_on_next_restart": True,
        }
