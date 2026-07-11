"""
State machine module -- handles idle / working / sleeping transitions and the
idle wait loop.

Extracted from runner.py.  Manages:
- Wake prompt generation
- Idle polling (hot-reload checks, message queue drains, auto-sleep)
- Sleep / wake lifecycle
- The "never stop" inner wait loop that listens for pipeline events
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from opensquad.tool import logger

if TYPE_CHECKING:
    from opensquad.events import EventBus
    from opensquad.input_hub import InputHub
    from opensquad.message_queue import MessageQueue
    from opensquad.sleep_controller import SleepController
    from opensquad.state_manager import AIStateManager

__all__ = ["StateMachine"]


class StateMachine:
    """
    Manages idle / working / sleeping transitions and the idle wait loop.

    This class is instantiated by ``AgentRunner`` and holds no persistent
    state of its own -- all state lives on the Runner instance passed to
    each method.
    """

    @staticmethod
    def generate_wake_prompt(
        sleep_controller: SleepController,
    ) -> str:
        """Build the prompt injected when the agent wakes from sleep."""
        wake_info = sleep_controller._wake_reason  # type: ignore[attr-defined]
        if not wake_info:
            return "[Wake]"

        reason: str = str(wake_info) if wake_info else "unknown"
        time_str: str = datetime.now().strftime("%H:%M:%S")

        if reason == "Sleep duration ended":
            return f"[Wake-{time_str}-Sleep duration ended]"
        return f"[Wake-{time_str}-{reason}]"

    async def idle_wait(
        self,
        runner: Any,
        input_hub: InputHub,
        message_queue: MessageQueue,
        state_manager: AIStateManager,
        event_bus: EventBus,
        sleep_controller: SleepController,
        emit: Callable[[str, Any], Any],
        get_state: Callable[[], Any],
        set_state: Callable[[str], Any],
        get_session_manager: Callable[[], Any],
        get_current_sid: Callable[[], str],
        on_wake_callback: Callable[[str], None] | None = None,
    ) -> tuple[str, list[Any]]:
        """
        The idle wait phase: blocks until input arrives or the agent is woken.

        Performs periodic checks for:
        - Plugin / config hot-reload signals
        - Orphaned group messages in the message queue
        - Auto-sleep when awaiting a group reply

        Args:
            runner:              The AgentRunner instance (for hot-reload access).
            input_hub:          The global InputHub singleton.
            message_queue:      The global MessageQueue singleton.
            state_manager:      The AIStateManager singleton.
            event_bus:          The global EventBus singleton.
            sleep_controller:   The global SleepController singleton.
            emit:               runner._emit -- async event emitter with sid injection.
            get_state:          await state_manager.get_state().
            set_state:          await state_manager.set_state().
            get_session_manager: Returns the SessionManager singleton.
            get_current_sid:    Returns the current session ID string.
            on_wake_callback:   Optional callback(str initial_query) invoked on wake.

        Returns:
            A (initial_query, pending_group_messages) tuple.
        """
        pending_group_messages: list[Any] = []
        initial_query: str | None = None

        while initial_query is None:
            # -- 1. Hot-reload: plugins ------------------------------------
            if runner._plugin_manager and runner._plugin_manager.check_reload_needed():
                reload_result = runner._plugin_manager.reload_plugins(
                    registry=runner.tool_registry,
                    agent_id=runner._agent_id,
                    agent_tool_names=runner._agent_tool_names,
                )
                if reload_result["loaded"] or reload_result["unloaded"]:
                    logger.info(
                        "[StateMachine] Plugin hot-reload: loaded=%s, unloaded=%s",
                        reload_result["loaded"],
                        reload_result["unloaded"],
                    )

            # -- 2. Hot-reload: config.json --------------------------------
            config_path = runner._config_path
            if config_path:
                import os

                if os.path.isfile(config_path):
                    try:
                        import json as _json

                        mtime = os.path.getmtime(config_path)
                        if mtime > runner._config_mtime:
                            runner._config_mtime = mtime
                            with open(config_path, encoding="utf-8") as _f:
                                _new_cfg = _json.load(_f)
                            new_tools = _new_cfg.get("tools", [])
                            new_levels = _new_cfg.get("tool_levels", {})
                            tools_changed = new_tools != runner._agent_tool_names
                            levels_changed = new_levels != runner._agent_tool_levels
                            if tools_changed or levels_changed:
                                runner._agent_tool_names = new_tools
                                runner._agent_tool_levels = new_levels
                                self._apply_config_tools_reload(runner, _new_cfg)
                            # Model hot-reload
                            new_model = _new_cfg.get("model", {})
                            if new_model != runner._model_config:
                                await self._apply_model_reload(runner, new_model)
                    except Exception as _e:
                        logger.warning("[StateMachine] Config reload error: %s", _e)

            # -- 3. Drain message queue if messages accumulated ---------------
            if message_queue.size > 0:
                pending_group_messages = message_queue.get_all()
                logger.info(
                    "[StateMachine] Drained %d orphaned messages from queue",
                    len(pending_group_messages),
                )

            # -- 4. Auto-sleep while awaiting group reply --------------------
            elif message_queue.size == 0:
                from opensquad.message_router import get_message_router

                message_router = get_message_router()

                if message_router.awaiting_reply:
                    sleep_seconds = message_router._await_reply_seconds
                    logger.info(
                        "[StateMachine] Auto-sleep %ds while awaiting reply",
                        sleep_seconds,
                    )
                    await emit("status", "sleeping")
                    await emit(
                        "info",
                        f"Group message sent, waiting for reply ({sleep_seconds}s timeout)...",
                    )
                    await set_state("sleeping")
                    message_router.clear_await_reply()
                    wake_info = await sleep_controller.sleep(int(sleep_seconds))
                    await set_state("idle")
                    await emit("status", "idle")
                    wake_prompt = self.generate_wake_prompt(sleep_controller)
                    if on_wake_callback:
                        on_wake_callback(wake_prompt)
                    logger.info(
                        "[StateMachine] Auto-sleep ended: %s",
                        wake_info.get("wake_type"),
                    )
                    continue

            # -- 5. Log idle status ----------------------------------------
            current_state = await get_state()
            logger.debug(
                "[StateMachine] Idle: state=%s, waiting for input...",
                current_state,
            )
            await emit("status", f"State: {current_state}, waiting...")

            # -- 6. Wait on input_hub with 5s timeout -----------------------
            try:
                user_input_data = await asyncio.wait_for(
                    input_hub.get_user_response(),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                runner._cancel_count = 0
                continue

            # -- 7. CancelledError recovery ---------------------------------
            except asyncio.CancelledError:
                runner._cancel_count += 1
                if runner._cancel_count > 3:
                    runner._cancel_count = 0
                    current_task = asyncio.current_task()
                    if current_task and hasattr(current_task, "uncancel"):
                        while current_task.uncancel() > 0:
                            pass
                    logger.warning("[StateMachine] CancelledError safety net (uncancel called), continuing...")
                continue

            # -- 8. Got input ----------------------------------------------
            initial_query = user_input_data.get("content", "")

        return initial_query, pending_group_messages

    def _apply_config_tools_reload(self, runner: Any, new_cfg: dict) -> None:
        """Apply tool-level changes from a reloaded config.json."""
        if not runner._agent_dir:
            return
        try:
            from opensquad.agents_boot import register_builtin_tools_sync

            register_builtin_tools_sync(new_cfg, runner.tool_registry, runner._agent_dir)
            logger.info("[StateMachine] Config reload: built-in tools re-registered")
        except Exception as _e:
            logger.warning("[StateMachine] Built-in tool re-registration failed: %s", _e)

        if runner._plugin_manager:
            runner._plugin_manager.reload_plugins(
                registry=runner.tool_registry,
                agent_id=runner._agent_id,
                agent_tool_names=runner._agent_tool_names,
            )
            runner._plugin_manager.register_tools_to_agent(
                registry=runner.tool_registry,
                agent_id=runner._agent_id,
                agent_tool_names=runner._agent_tool_names,
                agent_tool_levels=runner._agent_tool_levels,
            )
            logger.info("[StateMachine] Config reload: plugin tools re-registered")

    async def _apply_model_reload(self, runner: Any, new_model: dict) -> None:
        """Apply model-level changes from a reloaded config.json.

        Delegates to opensquad.model_switch.apply_model_reload so the poll path
        and the event-driven switch path share one implementation (and the
        async reload_model is awaited correctly in both).
        """
        logger.info("[StateMachine] Model config changed, hot-reloading...")
        try:
            from opensquad.model_switch import apply_model_reload

            await apply_model_reload(runner, new_model)
        except Exception as _e:
            logger.warning("[StateMachine] Model hot-reload failed: %s", _e)

    async def wait_for_events(
        self,
        runner: Any,
        input_hub: InputHub,
        message_queue: MessageQueue,
        emit: Callable[[str, Any], Any],
        set_state: Callable[[str], Any],
        get_session_manager: Callable[[], Any],
    ) -> tuple[bool, str | None]:
        """
        The "never stop" inner wait loop: after the LLM replies with
        went_to_sleep=True, the agent blocks here waiting for events
        (pipeline messages, user input, timers, etc.) and resumes the turn
        loop when something arrives.

        Returns:
            (task_finished, next_input) -- task_finished is True when
            the outer task should exit (e.g. user requested stop); next_input
            is the string to pass as the next user message.
        """
        poll_interval = 1.0

        while True:
            # P0 perf: event-driven wait instead of pure polling
            # Wait for input_hub or message_queue to signal, with 1s fallback
            # for hot-reload / config checks
            input_event = input_hub.get_input_event()
            msg_event = message_queue.get_message_event()
            # Clear events before checking (avoid missed signals)
            input_event.clear()
            msg_event.clear()

            # Check immediately first (fast path)
            if input_hub.is_stop_requested():
                input_hub.clear_stop_request()
                return True, None

            for cmd in input_hub.check_urgent_commands():
                content = cmd.get("content", "")
                result = await self._handle_urgent_command(runner, cmd, content, emit, set_state, get_session_manager)
                if result is not None:
                    return result

            supplements = input_hub.get_all_pending()
            if supplements:
                return False, None

            pending = message_queue.get_all()
            if pending:
                queue_images = [img for msg in pending if msg.images for img in msg.images]
                if queue_images:
                    runner._current_images.extend(queue_images)
                return False, None

            # Nothing available — wait for signal or timeout
            done, _ = await asyncio.wait(
                [asyncio.create_task(input_event.wait()), asyncio.create_task(msg_event.wait())],
                timeout=poll_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Continue loop to check again after wakeup

    # ------------------------------------------------------------------
    # Urgent command handler (shared between idle_wait and wait_for_events)
    # ------------------------------------------------------------------

    async def _handle_urgent_command(
        self,
        runner: Any,
        cmd: dict,
        content: str,
        emit: Callable[[str, Any], Any],
        set_state: Callable[[str], Any],
        get_session_manager: Callable[[], Any],
    ) -> tuple[bool, str | None] | None:
        """
        Handle a single urgent command. Returns None if not handled,
        or a (task_finished, next_input) tuple if the command consumed the event.
        """
        if content == "__STOP__":
            logger.info("[StateMachine] Stop command received")
            emit("status", "Task stopped by user")
            return True, None

        elif content == "__NEW_SESSION__":
            logger.info("[StateMachine] New session requested")
            runner._reset_session_stats()
            get_session_manager().start_new_session()
            runner._turn_sid = get_session_manager().get_current_session_id()
            runner._load_history()
            from opensquad.events import get_event_bus

            emit("turn_start", 0)
            emit("info", "New session started")
            return True, None

        elif content == "__COMPRESS_CONTEXT__":
            logger.info("[StateMachine] Compress context command received")
            return True, "__COMPRESS_CONTEXT__"

        elif content.startswith("__LOAD_SESSION__:"):
            sid = content.split(":", 1)[1]
            logger.info("[StateMachine] Load session %s", sid)
            sm = get_session_manager()
            if sm.load_history_session(sid):
                runner._turn_sid = sid
                runner._load_history()
                emit("turn_start", 0)
                from opensquad.events import get_event_bus

                await get_event_bus().emit_async("current_session", {"id": sid, "title": "Current Session"})
                emit("info", f"Session loaded: {sid}")
            return True, None

        elif content.startswith("__SWITCH_AND_REPLY__:"):
            parts = content.split(":", 2)
            if len(parts) >= 3:
                sid, reply_content = parts[1], (parts[2] or "").strip()
                logger.info("[StateMachine] Switch and reply: session=%s", sid)
                sm = get_session_manager()
                if sid and sid != sm.get_current_session_id():
                    if sm.load_history_session(sid):
                        runner._turn_sid = sid
                        runner._load_history()
                        if reply_content:
                            emit("turn_start", 0)
                        from opensquad.events import get_event_bus

                        await get_event_bus().emit_async("current_session", {"id": sid, "title": "Current Session"})
                if not reply_content:
                    return True, None
                return False, reply_content

        return None
