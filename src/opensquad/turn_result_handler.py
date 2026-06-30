from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from opensquad.parser import ResponseParser
from opensquad.sleep_controller import sleep_controller
from opensquad.task_logger import task_logger


@dataclass
class ResponseTagState:
    thought_text: str
    plan_text: str
    new_state: str | None
    new_wake: str | None
    sleep_seconds: str | None
    sys_cmd: str | None
    task_start: str | None


@dataclass
class UserFacingMessage:
    user_msg: str
    user_msg_from_tag: str | None
    saved_msg: str | None
    saved_output_media: list[Any] | None


class TurnResultHandler:
    """Extracts non-tool portions of `AgentRunner._handle_turn_result()`."""

    def __init__(self, runner: Any):
        self.runner = runner

    async def parse_and_persist_tags(self, full_response: str) -> ResponseTagState:
        thought_text = (
            ResponseParser.extract_tag(full_response, "thought")
            or ResponseParser.extract_tag(full_response, "think")
            or ""
        )
        if thought_text:
            self.runner._session_manager.add_event(
                "thought",
                {"text": thought_text},
                turn_id=self.runner._current_turn,
                round_id=self.runner._current_round,
            )

        plan_text = ResponseParser.extract_tag(full_response, "plan") or ""
        if plan_text:
            plan_id = f"plan_{datetime.now().strftime('%M%S')}"
            await self.runner._emit("plan", {"id": plan_id, "text": plan_text})
            self.runner._session_manager.add_event(
                "plan",
                {"id": plan_id, "text": plan_text},
                turn_id=self.runner._current_turn,
                round_id=self.runner._current_round,
            )
            self.runner.task_manager.update(plan_text)

        import re

        option_matches = re.findall(r"<option>(.*?)</option>", full_response, re.DOTALL)
        for option_text in option_matches:
            await self.runner._emit("option", option_text.strip())
            self.runner._session_manager.add_event(
                "option",
                {"text": option_text.strip()},
                turn_id=self.runner._current_turn,
                round_id=self.runner._current_round,
            )

        return ResponseTagState(
            thought_text=thought_text,
            plan_text=plan_text,
            new_state=self.runner._extract_tag(full_response, "state"),
            new_wake=self.runner._extract_tag(full_response, "wake"),
            sleep_seconds=self.runner._extract_tag(full_response, "sleep"),
            sys_cmd=self.runner._extract_tag(full_response, "to_system"),
            task_start=self.runner._extract_tag(full_response, "task_start"),
        )

    async def apply_state_transitions(self, tag_state: ResponseTagState) -> tuple[bool, str, bool]:
        if tag_state.task_start:
            self.runner._in_task = True
            self.runner._auto_continue_retries = 0
            task_name = tag_state.task_start.strip()
            if task_name:
                self.runner._session_manager.set_title(task_name)
                await self.runner._emit(
                    "current_session",
                    {"id": self.runner._session_manager.get_current_session_id(), "title": task_name},
                )
                await self.runner._bus.emit_async("session_list", self.runner._session_manager.get_session_list())
                await self.runner._emit(
                    "session_title",
                    {"id": self.runner._session_manager.get_current_session_id(), "title": task_name},
                )

        if tag_state.sys_cmd in ["task_complete", "task_failed"]:
            self.runner._in_task = False
            self.runner._awaiting_user_reply = False
            self.runner._last_user_msg_from_to_user = False
            self.runner._auto_continue_retries = 0

        if tag_state.new_state:
            await self.runner._state_manager.set_state(tag_state.new_state)
            await self.runner._emit("state", tag_state.new_state)
            if tag_state.new_state == "working" and not task_logger.has_active_task():
                task_req = self.runner._last_user_input[:200]
                task_id = task_logger.start_task(task_req, "working")
                if self.runner._plugin_manager:
                    await self.runner._plugin_manager.run_hook(
                        "on_task_start",
                        {
                            "task_id": task_id,
                            "requirement": task_req,
                            "source": self.runner._current_input_source,
                            "agent_id": self.runner._agent_id,
                        },
                    )

        if tag_state.new_wake:
            await self.runner._state_manager.set_wake_mode(tag_state.new_wake)
            await self.runner._emit("wake", tag_state.new_wake)

        if tag_state.sleep_seconds and tag_state.sleep_seconds.isdigit():
            seconds = int(tag_state.sleep_seconds)
            await self.runner._emit("sleep", seconds)
            await self.runner._state_manager.set_state("sleeping")
            await self.runner._emit("state", "sleeping")
            await sleep_controller.sleep(seconds)
            await self.runner._state_manager.set_state("idle")
            await self.runner._emit("state", "idle")
            return False, "", True

        return False, "", False

    def extract_user_facing_message(self, full_response: str) -> UserFacingMessage:
        streamed = "".join(getattr(self.runner, "_streamed_user_text", []))
        user_msg_from_tag = None
        self.runner._last_user_msg_from_to_user = False
        if streamed.strip():
            user_msg = streamed.strip()
            user_msg_from_tag = getattr(self.runner, "_streamed_user_tag", None) or "to_user"
            self.runner._last_user_msg_from_to_user = user_msg_from_tag == "to_user"
        else:
            interfering_tags = [
                "thought",
                "think",
                "plan",
                "tool_call",
                "tool_result",
                "to_system",
                "state",
                "wake",
                "sleep",
                "option",
                "title",
                "func",
            ]
            clean_context = self.runner._remove_tags(full_response, interfering_tags)
            user_msg = self.runner._extract_tag(clean_context, "to_user_reply")
            if user_msg:
                user_msg_from_tag = "to_user_reply"
            else:
                user_msg = self.runner._extract_tag(clean_context, "to_user")
                if user_msg:
                    user_msg_from_tag = "to_user"
                    self.runner._last_user_msg_from_to_user = True
            if not user_msg:
                user_msg = self.runner._remove_all_tags(clean_context)
            else:
                user_msg = self.runner._remove_all_tags(user_msg)

        if user_msg_from_tag == "to_user_reply":
            self.runner._awaiting_user_reply = True

        return UserFacingMessage(
            user_msg=user_msg,
            user_msg_from_tag=user_msg_from_tag,
            saved_msg=None,
            saved_output_media=None,
        )

    async def emit_user_facing_message(
        self, user_message: UserFacingMessage, output_media: list[Any] | None
    ) -> UserFacingMessage:
        saved_msg = None
        saved_output_media = None
        print(
            f"[DIAG_EMIT] emit_user_facing_message ENTER: user_msg={user_message.user_msg!r}, strip={user_message.user_msg.strip()!r}",
            flush=True,
        )
        if user_message.user_msg.strip():
            send_msg = user_message.user_msg
            if self.runner._plugin_manager:
                hook_ctx = await self.runner._plugin_manager.run_hook(
                    "on_before_send",
                    {"message": send_msg, "agent_id": self.runner._agent_id},
                )
                send_msg = hook_ctx.get("message", send_msg)
                if hook_ctx.get("__stop__"):
                    send_msg = None
            if send_msg and send_msg.strip():
                event_type = "to_user_reply" if user_message.user_msg_from_tag == "to_user_reply" else "to_user_final"
                print(f"[DIAG_EMIT] CALLING _emit(event_type={event_type}, send_msg={send_msg!r})", flush=True)
                await self.runner._emit(event_type, send_msg)
                print("[DIAG_EMIT] _emit DONE", flush=True)
                if output_media:
                    await self.runner._emit("output_media", output_media)
                saved_msg = send_msg
                saved_output_media = output_media
                if self.runner._plugin_manager:
                    await self.runner._plugin_manager.run_hook(
                        "on_after_send",
                        {"message": send_msg, "agent_id": self.runner._agent_id},
                    )

        return UserFacingMessage(
            user_msg=user_message.user_msg,
            user_msg_from_tag=user_message.user_msg_from_tag,
            saved_msg=saved_msg,
            saved_output_media=saved_output_media,
        )

    async def finalize_without_tools(
        self,
        full_response: str,
        user_message: UserFacingMessage,
        sys_cmd: str | None,
        finish_reason: str | None,
        stream_error: bool,
        tool_data_from_api: Any,
    ) -> tuple[bool, str, bool] | None:
        clean_full = self.runner._remove_all_tags(full_response).strip()
        needs_tool = clean_full.endswith(":") or clean_full.endswith("：")
        if needs_tool and not tool_data_from_api:
            if finish_reason == "stop" and not stream_error:
                if (
                    self.runner._max_auto_continue_retries is None
                    or self.runner._auto_continue_retries < self.runner._max_auto_continue_retries
                ):
                    self.runner._auto_continue_retries += 1
                    return (
                        False,
                        "[System Prompt] You ended with a trailing colon, which usually means you intended to call a tool next. Continue immediately by calling the appropriate tool.",
                        False,
                    )
            elif stream_error:
                return None

        if user_message.saved_msg:
            extra = {}
            if self.runner.chat_api and self.runner.chat_api._prev_reasoning_content:
                extra["reasoning_content"] = self.runner.chat_api._prev_reasoning_content
            self.runner._session_manager.add_message("assistant", user_message.saved_msg, **extra)
            elapsed_ms = int(datetime.now().timestamp() * 1000) - int(self.runner._workflow_started_ms)
            self.runner._session_manager.update_last_message_elapsed_ms(elapsed_ms)

        if user_message.user_msg.strip():
            if self.runner._awaiting_user_reply:
                self.runner._awaiting_user_reply = False
            return False, "", True

        if sys_cmd in ["task_complete", "task_failed"]:
            completed = None
            if task_logger.has_active_task():
                completed = task_logger.complete_task(
                    completion_status="completed" if sys_cmd == "task_complete" else "failed",
                    result_summary="Task finished",
                )
            if completed and self.runner._plugin_manager:
                await self.runner._plugin_manager.run_hook(
                    "on_task_complete",
                    {
                        "task_id": completed.get("task_id", ""),
                        "completion_status": completed.get("completion_status", ""),
                        "tools_used": completed.get("tools_used", []),
                        "turns": completed.get("turns", 0),
                        "agent_id": self.runner._agent_id,
                    },
                )
            await self.runner._state_manager.set_state("idle")
            await self.runner._emit("state", "idle")
            return True, "", False

        return False, "Error: No output produced", False
