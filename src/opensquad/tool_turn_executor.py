from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from opensquad.event_pipeline import get_event_pipeline
from opensquad.log_setup import get_tool_call_debug_logger
from opensquad.parser import ResponseParser
from opensquad.task_logger import task_logger
from opensquad.task_supervisor import task_supervisor


@dataclass
class ToolExecutionResult:
    handled: bool
    return_value: tuple[bool, str, bool] | None = None


class ToolTurnExecutor:
    """Extracts tool execution branch from `AgentRunner._handle_turn_result()`."""

    def __init__(self, runner: Any):
        self.runner = runner

    async def execute(self, full_response: str, tool_data_from_api: Any, saved_msg: str | None) -> ToolExecutionResult:
        from opensquad.structured_log import perf_event

        t0 = __import__("time").perf_counter()
        tc_log = get_tool_call_debug_logger()
        tc_log.debug("[runner] full_response len=%d, first 500 chars: %s", len(full_response), full_response[:500])

        tool_calls = self._resolve_tool_calls(full_response, tool_data_from_api, tc_log)
        if not tool_calls:
            return ToolExecutionResult(handled=False)

        tc_log.info("[runner] [tool] Executing %d parallel tool call(s)", len(tool_calls))
        tool_results: list[dict[str, Any]] = []
        control_flow_return: tuple[bool, str, bool] | None = None

        for call_index, (tool_name, tool_args_dict) in enumerate(tool_calls):
            # Stop between tools so a partial batch can finish quickly after abort.
            try:
                from opensquad.input_hub import input_hub

                if input_hub.is_stop_requested():
                    for rest_index in range(call_index, len(tool_calls)):
                        rest_name, _ = tool_calls[rest_index]
                        rid = f"call_stop_{rest_index}"
                        await self.runner._emit(
                            "tool_call",
                            {"id": rid, "name": rest_name, "args": "{}"},
                        )
                        await self.runner._emit(
                            "tool_result",
                            {
                                "id": rid,
                                "name": rest_name,
                                "result": "Cancelled: stopped by user",
                            },
                        )
                    break
            except Exception:
                pass

            entry = await self._execute_single_tool(call_index, tool_name, tool_args_dict, tc_log)
            if entry.get("control_flow_return") is not None:
                control_flow_return = entry["control_flow_return"]
                continue
            if entry.get("skip_batch_commit"):
                continue
            tool_results.append(entry)

        if control_flow_return:
            return ToolExecutionResult(handled=True, return_value=control_flow_return)

        await self._batch_commit(tool_results)

        tc_log.info("[runner] [tool] Batch commit complete: %d result(s), returning False,'',False", len(tool_results))
        if saved_msg:
            self.runner.logger.info(
                "[Runner] FIX-ACTIVE: Deferring assistant message persistence (tool calls in progress, saved_msg_len=%d)",
                len(saved_msg),
            )
        perf_event(
            "runner",
            "tool_batch_complete",
            agent_id=getattr(self.runner, "_agent_id", ""),
            tool_count=len(tool_results),
            elapsed_ms=int((__import__("time").perf_counter() - t0) * 1000),
        )
        return ToolExecutionResult(handled=True, return_value=(False, "", False))

    def _resolve_tool_calls(
        self, full_response: str, tool_data_from_api: Any, tc_log: Any
    ) -> list[tuple[str, dict[str, Any]]]:
        if tool_data_from_api:
            tool_calls = tool_data_from_api
            tc_log.info("[runner] [OK] Using tool_data from Native FC strategy: %d tool(s)", len(tool_calls))
            return tool_calls

        tool_calls = ResponseParser.parse_tool_calls(full_response)
        if tool_calls:
            tc_log.info("[runner] [OK] Using tool_data from XML parser: %d tool(s)", len(tool_calls))
            return tool_calls
        return []

    async def _execute_single_tool(
        self, call_index: int, tool_name: str, tool_args_dict: dict[str, Any], tc_log: Any
    ) -> dict[str, Any]:
        from opensquad.structured_log import perf_event

        t_tool = __import__("time").perf_counter()
        tc_log.info("[runner] [tool] #%d: name=%r, args=%r", call_index, tool_name, tool_args_dict)
        call_id = f"call_{datetime.now().strftime('%M%S')}_{tool_name}_{call_index}"
        sanitized = {k: ("..." if v is ... else v) for k, v in tool_args_dict.items()} if tool_args_dict else {}
        args_json = json.dumps(sanitized, ensure_ascii=False, indent=2) if sanitized else "{}"

        if tool_name in ("system__event_pipeline", "system.event_pipeline"):
            tc_log.debug("[runner] [emit] Skipping system__event_pipeline from frontend & session")
        else:
            await self.runner._emit("tool_call", {"id": call_id, "name": tool_name, "args": args_json})
            self.runner._session_manager.add_event(
                "tool_call",
                {"id": call_id, "name": tool_name, "args": args_json},
                turn_id=self.runner._current_turn,
                round_id=self.runner._current_round,
            )

        tool_name, tool_args_dict, skip_tool, hook_ctx = await self._run_before_tool_hook(tool_name, tool_args_dict)
        limit_token = self._pop_limit_token(tool_args_dict)

        if skip_tool:
            result = hook_ctx.get("result", "Tool call skipped by plugin hook.")
            tc_log.info("[runner] [skip] Tool execution skipped by plugin hook")
        else:
            tc_log.info("[runner] [run] Executing tool: %s", tool_name)
            result = await self.runner.tool_registry.call(tool_name, tool_args_dict)
            task_supervisor.report_activity()

        perf_event(
            "runner",
            "tool_executed",
            agent_id=getattr(self.runner, "_agent_id", ""),
            tool_name=tool_name,
            elapsed_ms=int((__import__("time").perf_counter() - t_tool) * 1000),
        )

        self._sync_collaboration_board(tool_name, result)
        result = await self._run_after_tool_hooks(tool_name, tool_args_dict, result)

        raw_events = get_event_pipeline().drain_sync()
        pipeline_events = await self._drain_pipeline_events(raw_events)
        tool_result_text = self.runner._truncate_result_text(
            str(result) if result else "(empty result)",
            self._resolve_tool_result_limit(limit_token),
        )

        control_flow_return = await self._handle_special_tool_results(
            tool_name=tool_name,
            tool_args_dict=tool_args_dict,
            args_json=args_json,
            call_id=call_id,
            result=result,
            pipeline_events=pipeline_events,
        )
        if control_flow_return is not None:
            return {"control_flow_return": control_flow_return, "skip_batch_commit": True}

        await self._handle_set_state_side_effect(tool_name, result)

        from opensquad.structured_log import perf_event

        perf_event(
            "runner",
            "tool_executed",
            agent_id=getattr(self.runner, "_agent_id", ""),
            tool_name=tool_name,
            elapsed_ms=int((__import__("time").perf_counter() - t_tool) * 1000),
        )

        return {
            "name": tool_name,
            "args": tool_args_dict,
            "args_json": args_json,
            "result_text": tool_result_text,
            "call_id": call_id,
            "pipeline_events": pipeline_events,
        }

    async def _run_before_tool_hook(
        self, tool_name: str, tool_args_dict: dict[str, Any]
    ) -> tuple[str, dict[str, Any], bool, dict[str, Any]]:
        skip_tool = False
        hook_ctx: dict[str, Any] = {}
        if self.runner._plugin_manager:
            hook_ctx = await self.runner._plugin_manager.run_hook(
                "on_before_tool",
                {
                    "tool_name": tool_name,
                    "arguments": tool_args_dict,
                    "agent_id": self.runner._agent_id,
                },
            )
            tool_name = hook_ctx.get("tool_name", tool_name)
            tool_args_dict = hook_ctx.get("arguments", tool_args_dict)
            skip_tool = hook_ctx.get("skip", False)
        return tool_name, tool_args_dict, skip_tool, hook_ctx

    async def _run_after_tool_hooks(self, tool_name: str, tool_args_dict: dict[str, Any], result: Any) -> Any:
        if self.runner._plugin_manager:
            hook_ctx = await self.runner._plugin_manager.run_hook(
                "on_after_tool",
                {
                    "tool_name": tool_name,
                    "arguments": tool_args_dict,
                    "result": result,
                    "agent_id": self.runner._agent_id,
                    "model": getattr(self.runner.chat_api, "model", ""),
                },
            )
            result = hook_ctx.get("result", result)

        if self.runner._plugin_manager and isinstance(result, str) and result.startswith("Error:"):
            hook_ctx = await self.runner._plugin_manager.run_hook(
                "on_tool_error",
                {
                    "tool_name": tool_name,
                    "arguments": tool_args_dict,
                    "error": result,
                    "agent_id": self.runner._agent_id,
                },
            )
            result = hook_ctx.get("error", result)
        return result

    def _pop_limit_token(self, tool_args_dict: dict[str, Any]) -> int | None:
        limit_token = None
        if isinstance(tool_args_dict, dict) and "limit_token" in tool_args_dict:
            raw = tool_args_dict.pop("limit_token")
            try:
                value = int(raw)
                limit_token = value if value > 0 else 0
            except (ValueError, TypeError):
                pass
        return limit_token

    def _resolve_tool_result_limit(self, limit_token: int | None) -> int | None:
        if limit_token is not None:
            return limit_token if limit_token > 0 else None
        max_len = self.runner._get_tool_output_max_chars()
        return max_len if max_len > 0 else None

    def _sync_collaboration_board(self, tool_name: str, result: Any) -> None:
        try:
            from opensquad.collab_board import list_tasks as cb_list_tasks
            from opensquad.collab_board import update_latest_tool as cb_update_latest_tool

            agent_dir = getattr(self.runner, "_agent_dir", "") or ""
            agent_id = os.path.basename(agent_dir) if agent_dir else "unknown_agent"
            active_task_id = ""
            for task in cb_list_tasks():
                if task.get("status") == "active":
                    active_task_id = str(task.get("task_id") or "")
                    break
            if not active_task_id:
                return

            sensitive_tools = {
                "read_related_files",
                "glob",
                "grep",
                "rg",
                "filesystem__read",
                "filesystem__write",
                "filesystem__edit",
                "bash",
                "subprocess",
                "delegate_task",
                "system__send_file_to_web",
                "execute_command",
                "view_source_code",
                "find_files",
            }
            if tool_name.startswith("collaboration.") or tool_name.startswith("agent_setup."):
                sensitive_tools.add(tool_name)
            if tool_name not in sensitive_tools:
                cb_update_latest_tool(
                    collab_id=active_task_id,
                    task_name="",
                    agent_id=agent_id,
                    tool_name=tool_name,
                    tool_result=result,
                )
        except Exception:
            pass

    async def _drain_pipeline_events(self, raw_events: list[Any]) -> str:
        for evt in raw_events:
            if evt.source in ("web", "gateway", "group", "dm") and evt.content and evt.content.strip():
                self.runner._session_manager.add_message("user", evt.content)
                await self.runner._emit("user_msg", evt.content)
            if evt.source == "vision_tool" and evt.metadata.get("action") == "inject_images":
                img_paths = evt.metadata.get("image_paths", [])
                if img_paths:
                    already = set(self.runner._current_images)
                    new_img_paths = [p for p in img_paths if p not in already]
                    if new_img_paths:
                        self.runner._current_images.extend(new_img_paths)
                    try:
                        img_path_file = (
                            os.path.join(self.runner._agent_dir, "img_path.txt")
                            if self.runner._agent_dir
                            else "img_path.txt"
                        )
                        with open(img_path_file, "w", encoding="utf-8") as handle:
                            handle.write(str(img_paths))
                    except Exception:
                        pass

        if not raw_events:
            return ""

        lines = ["", "--- External Events (arrived during processing) ---"]
        for evt in raw_events:
            lines.append(evt.format_for_llm())
        lines.append("--- End External Events ---")
        return "\n".join(lines)

    async def _handle_special_tool_results(
        self,
        tool_name: str,
        tool_args_dict: dict[str, Any],
        args_json: str,
        call_id: str,
        result: Any,
        pipeline_events: str,
    ) -> tuple[bool, str, bool] | None:
        if tool_name not in ("system.wait", "wait", "system__wait"):
            return None
        if not isinstance(result, dict) or result.get("status") != "success":
            return None

        agent_dir = getattr(self.runner, "_agent_dir", "") or ""
        if agent_dir:
            try:
                from opensquad import checkpoint

                checkpoint.clear_checkpoint(agent_dir)
            except Exception:
                pass

        wake_type = result.get("wake_type", "natural")
        wake_reason = result.get("wake_reason", "")
        actual_seconds = result.get("actual_seconds", 0)
        wake_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        wake_msg = f"[Wake-{wake_time}-Slept {actual_seconds}s ({wake_type})"
        if wake_reason and wake_reason != "Sleep duration ended":
            wake_msg += f", reason: {wake_reason}"
        wake_msg += "]"

        self.runner.chat_api.add_tool_result(
            tool_name=tool_name,
            tool_args=tool_args_dict,
            result=wake_msg,
            tool_call_id=call_id,
        )
        if pipeline_events:
            self.runner.chat_api.add_pipeline_events(pipeline_events)

        self.runner._session_manager.add_event(
            "tool_result",
            {"id": call_id, "name": tool_name, "args": args_json, "result": wake_msg},
            turn_id=self.runner._current_turn,
            round_id=self.runner._current_round,
        )
        await self.runner._emit(
            "tool_result", {"id": call_id, "name": tool_name, "args": args_json, "result": wake_msg}
        )
        return False, wake_msg, False

    async def _handle_set_state_side_effect(self, tool_name: str, result: Any) -> None:
        if tool_name not in ("system.set_state", "set_state", "system__set_state"):
            return
        if not isinstance(result, dict) or result.get("status") != "success":
            return

        import re

        message = result.get("message", "")
        match = re.search(r"'(\w+)'", message)
        if not match:
            return

        actual_state = match.group(1)
        await self.runner._emit("state", actual_state)
        if actual_state == "working" and not task_logger.has_active_task():
            task_req = self.runner._last_user_input[:200]
            task_logger.start_task(task_req, "working")

    async def _batch_commit(self, tool_results: list[dict[str, Any]]) -> None:
        tc_log = get_tool_call_debug_logger()
        tc_log.info("[runner] [tool] Batch-committing %d tool result(s) to chat_api history", len(tool_results))
        for entry in tool_results:
            self.runner.chat_api.add_tool_result(
                tool_name=entry["name"],
                tool_args=entry["args"],
                result=entry["result_text"],
                tool_call_id=entry["call_id"],
            )
            if entry["pipeline_events"]:
                self.runner.chat_api.add_pipeline_events(entry["pipeline_events"])

            self.runner._session_manager.add_event(
                "tool_result",
                {
                    "id": entry["call_id"],
                    "name": entry["name"],
                    "args": entry["args_json"],
                    "result": entry["result_text"],
                },
                turn_id=self.runner._current_turn,
                round_id=self.runner._current_round,
            )
            await self.runner._emit(
                "tool_result",
                {
                    "id": entry["call_id"],
                    "name": entry["name"],
                    "args": entry["args_json"],
                    "result": entry["result_text"],
                },
            )

            if task_logger.has_active_task():
                task_logger.increment_turn(entry["name"])
