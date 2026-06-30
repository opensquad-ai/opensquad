from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WaitLoopResult:
    task_finished: bool
    next_query: str | None
    current_input: str
    should_continue_turn_loop: bool


class RunnerWaitLoop:
    """Extracts wait-mode polling loop from `AgentRunner.run()`."""

    def __init__(self, runner: Any):
        self.runner = runner

    async def wait_for_events(self, initial_query: str | None, current_input: str) -> WaitLoopResult:
        self.runner._session_manager.add_event(
            "info",
            {"text": "Agent entering wait mode - listening for events"},
            turn_id=self.runner._current_turn,
            round_id=self.runner._current_round,
        )
        await self.runner._emit("status", "Waiting for events...")

        wait_poll_interval = 1.0
        wait_turn_count = 0
        task_finished = False

        while True:
            wait_turn_count += 1

            if self.runner._input_hub.is_stop_requested():
                self.runner._input_hub.clear_stop_request()
                await self.runner._emit("status", "Task stopped")
                return WaitLoopResult(True, None, current_input, False)

            urgent_result = await self._handle_urgent_commands(initial_query)
            if urgent_result is not None:
                return urgent_result

            supplements_result = await self._handle_supplements(initial_query)
            if supplements_result is not None:
                return supplements_result

            pipeline_result = await self._handle_pipeline_events(initial_query)
            if pipeline_result is not None:
                return pipeline_result

            self._flush_message_queue_to_pipeline()
            await asyncio.sleep(wait_poll_interval)

            if task_finished:
                return WaitLoopResult(True, None, current_input, False)

    async def _handle_urgent_commands(self, initial_query: str | None) -> WaitLoopResult | None:
        urgent_commands = self.runner._input_hub.check_urgent_commands()
        if not urgent_commands:
            return None

        for cmd in urgent_commands:
            content = cmd.get("content", "")
            if content == "__STOP__":
                self.runner._input_hub.clear_stop_request()
                await self.runner._emit("status", "Task stopped by user")
                return WaitLoopResult(True, None, "", False)
            if content == "__NEW_SESSION__":
                await self.runner._command_dispatcher._handle_new_session()
                return WaitLoopResult(True, None, "", False)
            if content == "__COMPRESS_CONTEXT__":
                return WaitLoopResult(True, "__COMPRESS_CONTEXT__", "", False)
            if content.startswith("__LOAD_SESSION__:"):
                sid = content.split(":", 1)[1]
                await self.runner._command_dispatcher._handle_load_session(sid)
                return WaitLoopResult(True, None, "", False)
            if content.startswith("__SWITCH_AND_REPLY__:"):
                parts = content.split(":", 2)
                if len(parts) >= 3:
                    sid, reply_content = parts[1], parts[2]
                    cmd_images = cmd.get("images", [])
                    if cmd_images:
                        self.runner._current_images = cmd_images
                    cmd_attachments = cmd.get("attachments", [])
                    if cmd_attachments:
                        self.runner._current_attachments = cmd_attachments
                    current_sid = self.runner._session_manager.get_current_session_id()
                    if sid != current_sid and self.runner._session_manager.load_history_session(sid):
                        self.runner._turn_sid = sid
                        self.runner._load_history()
                        await self.runner._emit("turn_start", 0)
                        await self.runner._bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                        await self.runner._bus.emit_async(
                            "session_list", self.runner._session_manager.get_session_list()
                        )
                    return WaitLoopResult(True, reply_content, "", False)
        return None

    async def _handle_supplements(self, initial_query: str | None) -> WaitLoopResult | None:
        last_was_tool_call = False
        if self.runner.chat_api.req:
            last_message = self.runner.chat_api.req[-1]
            if (last_message.get("role") == "assistant" and last_message.get("tool_calls")) or last_message.get(
                "role"
            ) == "tool":
                last_was_tool_call = True

        supplements = self.runner._input_hub.get_all_pending()
        if not supplements:
            return None

        await self.runner._emit("status", "working")
        await self.runner._setup_prompt()

        if last_was_tool_call:
            for item in supplements:
                content = item.get("content", "")
                if content and content.strip():
                    self.runner._session_manager.add_message("user", content)
                    await self.runner._emit("user_msg", content)
        else:
            for item in supplements:
                content = item.get("content", "")
                if content and content.strip():
                    self.runner.chat_api.add_user_message(content)
                    self.runner._session_manager.add_message("user", content)
                    await self.runner._emit("user_msg", content)

        self.runner._inner_loop_count = 1
        self.runner._turn_start_time = time.perf_counter()
        return WaitLoopResult(False, initial_query, "", True)

    async def _handle_pipeline_events(self, initial_query: str | None) -> WaitLoopResult | None:
        from opensquad.event_pipeline import get_event_pipeline

        event_pipeline = get_event_pipeline()

        if event_pipeline.size <= 0:
            return None

        await self.runner._emit("status", "working")
        await self.runner._setup_prompt()
        raw_events = event_pipeline.drain_sync()
        if raw_events:
            for event in raw_events:
                if event.source in ("web", "gateway", "group", "dm") and event.content and event.content.strip():
                    self.runner._session_manager.add_message("user", event.content)
                    await self.runner._emit("user_msg", event.content)

            lines = ["", "--- External Events (arrived during processing) ---"]
            for event in raw_events:
                lines.append(event.format_for_llm())
            lines.append("--- End External Events ---")
            pipeline_events = "\n".join(lines)
            self.runner.chat_api.add_pipeline_events(pipeline_events)

        self.runner._inner_loop_count = 1
        self.runner._turn_start_time = time.perf_counter()
        return WaitLoopResult(False, initial_query, "", True)

    def _flush_message_queue_to_pipeline(self) -> None:
        # Messages are already pushed to event_pipeline at MessageQueue.put()
        # time (see message_queue.py). Flushing here previously caused every
        # group/dm message to be injected twice. Now we only drain the queue
        # so the messages are not re-processed on the next get_all().
        pending_msgs = self.runner._message_queue.get_all()
        if pending_msgs:
            logger.debug(
                "[WaitLoop] Drained %d messages from queue (already in pipeline)",
                len(pending_msgs),
            )
