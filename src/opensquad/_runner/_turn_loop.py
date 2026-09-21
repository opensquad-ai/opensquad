"""
Turn-loop module -- per-turn result handling for AgentRunner.

Extracted from runner.py.  Follows the StateMachine pattern: ``TurnLoop``
holds no persistent state of its own; all state lives on the AgentRunner
instance passed to the constructor.  This makes the turn logic testable with
a minimal fake runner (see tests/test_turn_loop.py).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any

from opensquad import bus
from opensquad.input_hub import input_hub
from opensquad.log_setup import get_tool_call_debug_logger
from opensquad.messages import parse_tool_calls
from opensquad.parser import ResponseParser

# Consecutive format_error replies (leak guard) before we stop auto-retrying.
# Dots / OpenRouter free models re-emit the same template; without a cap the
# turn loop feeds the XML hint back forever.
FORMAT_ERROR_MAX_STREAK = 3
FORMAT_ERROR_STOP_HINT = (
    "该模型未返回原生 Function Calling，且正文里的工具调用无法按协议执行。"
    "已停止自动重试。请更换支持 tools 的模型，或检查 tool_call_mode。"
)

# Turns that produced neither visible text nor an executable tool call, per user
# turn. The retry prompt is appended to the conversation before we ask again, so
# a small budget is enough to let the model correct itself — and the cap is what
# keeps a stuck model from re-sending identical history until max_turns.
NO_OUTPUT_RETRY_MAX = 2
NO_OUTPUT_STOP_HINT = "模型连续多轮未产出可执行内容（既无正文也无可用工具调用），已停止本轮自动重试。"

# A response that is nothing but tool-call markup which we failed to parse into a
# call. Used to give the model an actionable reason instead of a generic "no
# output", which it cannot act on.
_TOOL_MARKUP_RE = re.compile(
    r"<(?:tool_call|tool_calls|invoke|function_call|dots_function_call)\b[^>]*>(.*?)(?:</(?:tool_call|tool_calls|invoke|function_call|dots_function_call)\s*>|$)",
    re.DOTALL | re.IGNORECASE,
)


def _unparsed_tool_name(raw: str) -> str:
    """Best-effort name of the tool inside tool-call markup we could not parse."""
    body = ""
    m = _TOOL_MARKUP_RE.search(raw or "")
    if m:
        body = m.group(1)
    if not body:
        body = raw or ""
    nm = re.search(r'name\s*=\s*"([^"]{1,120})"', body, re.IGNORECASE)
    if nm:
        return nm.group(1).strip()
    nm = re.search(r"<func\s*>([^<]{1,120}?)(?:</func\s*>|$)", body, re.IGNORECASE | re.DOTALL)
    if nm:
        return nm.group(1).strip()
    for line in body.strip().splitlines():
        cand = line.strip()
        if not cand or cand.startswith("<") or any(ch.isspace() for ch in cand):
            continue
        return cand[:120]
    return ""


# Helpers that still live on runner.py; imported lazily at call time so the
# module can be imported independently of runner state.
from opensquad._runner import _repeat_guard
from opensquad._runner._result_formatter import format_result_for_llm, is_failure_result
from opensquad.runner import _get_session_manager, _get_state_manager
from opensquad.sleep_controller import sleep_controller
from opensquad.task_logger import task_logger
from opensquad.task_supervisor import task_supervisor
from opensquad.tool import logger

__all__ = ["FORMAT_ERROR_MAX_STREAK", "NO_OUTPUT_RETRY_MAX", "TurnLoop"]


def _is_tool_markup_only(text: str) -> bool:
    """True when visible text is leftover tool XML, not a real chat reply."""
    s = (text or "").strip()
    if not s:
        return True
    low = s.lower()
    if "dots_function_call" in low or "<tool_call" in low or low.lstrip().startswith("invoke="):
        cleaned = re.sub(r"<[^>]+>", "", s).strip()
        return len(cleaned) < 80
    return False


class TurnLoop:
    """Per-turn execution logic. Instantiate with the owning AgentRunner."""

    def __init__(self, runner: Any) -> None:
        self.runner = runner

    async def _emit_format_error(self, user_msg: str) -> tuple[bool, str, bool]:
        """Report a leaked-parameter format_error; stop after FORMAT_ERROR_MAX_STREAK."""
        preview = user_msg.strip()[:120]
        streak = int(getattr(self.runner, "_format_error_streak", 0) or 0) + 1
        self.runner._format_error_streak = streak
        logger.warning(
            "[Runner] Detected leaked tool parameters in user_msg "
            "(len=%d, preview=%r, streak=%d/%d) -- sending format error back to model",
            len(user_msg.strip()),
            preview,
            streak,
            FORMAT_ERROR_MAX_STREAK,
        )
        now_str = datetime.now().strftime("%M%S")
        fe_call_id = f"call_{now_str}_format_error"
        fe_name = "format_error"
        fe_detail = (
            "Detected tool call parameters appearing directly in the response body; "
            "this indicates a <tool_call> tag was not properly closed or is malformed.\n"
            "Please strictly follow the XML format and re-output the tool call:\n"
            "<tool_call>\n"
            "    <func>tool_name</func>\n"
            "    <param1>value1</param1>\n"
            "    <param2>value2</param2>\n"
            "</tool_call>\n"
            "Strict rule: all XML tags must come in pairs; never omit the </tool_call> closing tag."
        )
        await self.runner._emit("tool_call", {"id": fe_call_id, "name": fe_name, "args": preview})
        await self.runner._emit(
            "tool_result", {"id": fe_call_id, "name": fe_name, "args": preview, "result": f"Error: {fe_detail}"}
        )
        _get_session_manager().add_event(
            "tool_call",
            {"id": fe_call_id, "name": fe_name, "args": preview},
            turn_id=self.runner._current_turn,
            round_id=self.runner._current_round,
        )
        _get_session_manager().add_event(
            "tool_result",
            {"id": fe_call_id, "name": fe_name, "args": preview, "result": f"Error: {fe_detail}"},
            turn_id=self.runner._current_turn,
            round_id=self.runner._current_round,
        )
        if streak >= FORMAT_ERROR_MAX_STREAK:
            logger.warning("[Runner] format_error streak=%d — stopping automatic retries", streak)
            await self.runner._emit("to_user_final", FORMAT_ERROR_STOP_HINT)
            await self.runner._emit("info", FORMAT_ERROR_STOP_HINT)
            _get_session_manager().add_event(
                "info",
                {"text": FORMAT_ERROR_STOP_HINT},
                turn_id=self.runner._current_turn,
                round_id=self.runner._current_round,
            )
            return True, "", False
        return False, self.runner._summarize_result(fe_name, f"Error: {fe_detail}"), False

    def _deliver_correction(self, prompt: str) -> bool:
        """Put a synthetic correction INTO the conversation.

        Returning one as ``next_input`` does not deliver it: the parallel turn
        loop calls ``chat(current_input, skip_add_user=not is_first_turn)``, so
        from turn 2 onward the string is dropped and the model is asked the exact
        same question again. Measured on 2026-09-17: 198 requests carrying a
        byte-identical 5-message history. Appending to the request history is what
        makes a retry a retry rather than a re-roll of the same dice.
        """
        api = getattr(self.runner, "chat_api", None)
        add = getattr(api, "add_user_message", None)
        if not callable(add):
            return False
        try:
            add(prompt)
            return True
        except Exception as e:  # bookkeeping must never break the turn
            logger.warning("[Runner] Could not deliver synthetic correction to the model: %s", e)
            return False

    async def _handle_no_output(self, full_response: str) -> tuple[bool, str, bool]:
        """The turn produced neither visible text nor an executable tool call.

        This used to return the bare string ``"Error: No output produced"`` as
        ``next_input`` — unbounded, and (see ``_deliver_correction``) never
        actually shown to the model. A model that answers every request with the
        same unparsable tool call then re-sends identical history until
        ``max_turns`` (default 200) runs out. Send an actionable reason, deliver
        it, and stop once the retry budget is spent.
        """
        runner = self.runner
        tries = int(getattr(runner, "_no_output_retries", 0) or 0)
        raw_name = _unparsed_tool_name(full_response)

        if tries >= NO_OUTPUT_RETRY_MAX:
            logger.error(
                "[Runner] No usable output for %d consecutive turns (last tool name=%r) — stopping",
                tries,
                raw_name,
            )
            await runner._emit("error", {"message": NO_OUTPUT_STOP_HINT})
            await runner._emit("to_user_final", NO_OUTPUT_STOP_HINT)
            _get_session_manager().add_event(
                "info",
                {"text": NO_OUTPUT_STOP_HINT},
                turn_id=runner._current_turn,
                round_id=runner._current_round,
            )
            return True, "", False

        runner._no_output_retries = tries + 1
        if raw_name:
            prompt = (
                "[System Prompt] Nothing happened: your last reply carried a tool call that could not "
                f"be resolved to a runnable tool ({raw_name!r}), so it was never executed.\n"
                "Pick a tool that actually exists in the list you were given — an MCP server that is "
                "disabled or not connected contributes no tools — or answer in plain text if no tool "
                "is needed. Do not repeat the same call."
            )
        else:
            prompt = (
                "[System Prompt] Your last reply produced neither visible text nor a tool call, so "
                "nothing happened. Either call a tool using the exact XML format, or reply to the "
                "user in plain text."
            )
        logger.warning(
            "[Runner] No usable output (attempt %d/%d, tool=%r) — sending correction back to model",
            runner._no_output_retries,
            NO_OUTPUT_RETRY_MAX,
            raw_name,
        )
        self._deliver_correction(prompt)
        # Non-empty so the caller keeps looping; it is dropped by skip_add_user,
        # which is exactly why the correction was appended above instead.
        return False, prompt, False

    async def handle_turn_result(
        self,
        full_response: str,
        tool_data_from_api=None,
        output_media=None,
        finish_reason: str | None = None,
        stream_error: bool = False,
    ) -> tuple[bool, str, bool]:
        """
        Handle one turn's result.

        Args:
            full_response: LLM response text
            tool_data_from_api: Tool call data parsed by strategy (tool_name, tool_args) or None
            output_media: Media list generated by the model [{"type": "audio"/"image", "url": ..., "mime": ...}]

        Returns: (should_stop, next_input, went_to_sleep)
        """
        # --- 1. Extract all interaction tags and persist them (ensure no loss on restart) ---

        # Thinking process (supports both 'thought' and 'think')
        # Note: during streaming, stream_parser already pushed thought events to the frontend in real time.
        # Here we only persist (write to session history); do not re-emit to avoid the frontend displaying duplicates.
        thought_text = ResponseParser.extract_tag(full_response, "thought") or ResponseParser.extract_tag(
            full_response, "think"
        )
        if thought_text:
            _get_session_manager().add_event(
                "thought",
                {"text": thought_text},
                turn_id=self.runner._current_turn,
                round_id=self.runner._current_round,
            )

        # Task plan
        plan_text = ResponseParser.extract_tag(full_response, "plan")
        if plan_text:
            plan_id = f"plan_{datetime.now().strftime('%M%S')}"
            await self.runner._emit("plan", {"id": plan_id, "text": plan_text})
            _get_session_manager().add_event(
                "plan",
                {"id": plan_id, "text": plan_text},
                turn_id=self.runner._current_turn,
                round_id=self.runner._current_round,
            )
            # Write back to TaskManager so the next turn's {{TASK_STATE}} includes the AI's own plan
            self.runner.task_manager.update(plan_text)

        # Option buttons
        import re

        option_matches = re.findall(r"<option>(.*?)</option>", full_response, re.DOTALL)
        for option_text in option_matches:
            await self.runner._emit("option", option_text.strip())
            _get_session_manager().add_event(
                "option",
                {"text": option_text.strip()},
                turn_id=self.runner._current_turn,
                round_id=self.runner._current_round,
            )

        # --- 2. State and sleep tags ---
        new_state = self.runner._extract_tag(full_response, "state")
        new_wake = self.runner._extract_tag(full_response, "wake")
        sleep_seconds = self.runner._extract_tag(full_response, "sleep")
        sys_cmd = self.runner._extract_tag(full_response, "to_system")

        # --- 2.1 Task supervision tags ---
        task_start = self.runner._extract_tag(full_response, "task_start")
        if task_start:
            self.runner._in_task = True
            self.runner._auto_continue_retries = 0
            task_name = task_start.strip()
            if task_name:
                _title_sid = getattr(self.runner, "_turn_sid", "") or _get_session_manager().get_current_session_id()
                _get_session_manager().set_title(task_name, sid=_title_sid)
                await self.runner._emit("current_session", {"id": _title_sid, "title": task_name})
                await bus.emit_async("session_list", _get_session_manager().get_session_list())
                await self.runner._emit("session_title", {"id": _title_sid, "title": task_name})

        # Agent-chosen session subject via <title>...</title>
        title_tag = self.runner._extract_tag(full_response, "title")
        if title_tag and title_tag.strip():
            title_name = title_tag.strip()
            _title_sid = getattr(self.runner, "_turn_sid", "") or _get_session_manager().get_current_session_id()
            _get_session_manager().set_title(title_name, sid=_title_sid)
            await self.runner._emit("current_session", {"id": _title_sid, "title": title_name})
            await bus.emit_async("session_list", _get_session_manager().get_session_list())
            await self.runner._emit("session_title", {"id": _title_sid, "title": title_name})

        if sys_cmd in ["task_complete", "task_failed"]:
            self.runner._in_task = False
            self.runner._awaiting_user_reply = False
            self.runner._last_user_msg_from_to_user = False
            self.runner._auto_continue_retries = 0

        # --- 3. Execute state update logic ---
        if new_state:
            logger.info(f"[Runner] Applying AI state change: {new_state}")
            await _get_state_manager().set_state(new_state)
            await self.runner._emit("state", new_state)  # Explicitly emit state change event

            if new_state == "working" and not task_logger.has_active_task():
                task_req = self.runner._last_user_input[:200]
                task_id = task_logger.start_task(task_req, "working")
                logger.info(f"[Runner] Task recording started: {task_id}")
                # --- Plugin Hook: on_task_start ---
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

        if new_wake:
            logger.info(f"[Runner] Applying wake mode change: {new_wake}")
            await _get_state_manager().set_wake_mode(new_wake)
            await self.runner._emit("wake", new_wake)

        # --- 3. Handle sleep command ---
        if sleep_seconds and sleep_seconds.isdigit():
            seconds = int(sleep_seconds)
            logger.info(f"[Runner] AI entering sleep for {seconds}s")
            await self.runner._emit("sleep", seconds)
            await _get_state_manager().set_state("sleeping")
            await self.runner._emit("state", "sleeping")
            wake_info = await sleep_controller.sleep(seconds)
            await _get_state_manager().set_state("idle")
            await self.runner._emit("state", "idle")
            logger.info(f"[Runner] Sleep ended: {wake_info.get('wake_type')}, reason: {wake_info.get('wake_reason')}")
            return False, "", True

        # --- 5. Text content persistence (regardless of whether tools are called) ---
        # Prefer streamed text accumulated by stream_parser during streaming.
        # stream_parser correctly identifies to_user content in real-time,
        # so its output is the authoritative source. This avoids the bug where
        # _remove_all_tags() would incorrectly strip tag names appearing as
        # explanatory text (e.g. AI explains "<to_user>" in a markdown table).
        # When the model leaves the real body *outside* <to_user> and only a
        # coda inside, upgrade to compose_user_visible_message (preamble+coda).
        from opensquad._runner._tag_utils import compose_user_visible_message

        streamed = "".join(getattr(self.runner, "_streamed_user_text", []) or [])
        user_msg_from_tag = None
        self.runner._last_user_msg_from_to_user = False
        composed, composed_tag = compose_user_visible_message(full_response or "")
        if streamed.strip():
            user_msg = streamed.strip()
            user_msg_from_tag = getattr(self.runner, "_streamed_user_tag", None) or "to_user"
            # Prefer explicit end_task tag in the raw response over stream-tag race.
            end_only = self.runner._extract_tag(full_response, "to_user_end_task")
            if end_only is not None:
                user_msg_from_tag = "to_user_end_task"
                cleaned_end = self.runner._remove_all_tags(end_only).strip()
                if cleaned_end:
                    user_msg = cleaned_end
            if composed and len(composed) > len(user_msg) + 80:
                user_msg = composed
                if composed_tag:
                    user_msg_from_tag = composed_tag
            self.runner._last_user_msg_from_to_user = user_msg_from_tag == "to_user"
        else:
            user_msg = composed or ""
            user_msg_from_tag = composed_tag
            self.runner._last_user_msg_from_to_user = user_msg_from_tag == "to_user"

        if not isinstance(user_msg, str):
            user_msg = "" if user_msg is None else str(user_msg)

        if user_msg_from_tag == "to_user_reply":
            self.runner._awaiting_user_reply = True

        # Parse tools on the *raw* response first. Native FC data wins; otherwise
        # XML / DSML / dots_function_call / invoke. The leak guard used to run
        # on streamed leftovers (hollow <dots_function_call> after invoke/parameter
        # were stripped) and return format_error before this parser could fire.
        _parsed_tool_calls = parse_tool_calls(full_response, tool_data_from_api)
        if _parsed_tool_calls:
            self.runner._format_error_streak = 0
            # A runnable call is progress — reset the no-output budget.
            self.runner._no_output_retries = 0
            if _is_tool_markup_only(user_msg) and user_msg_from_tag in (None, "to_user"):
                user_msg = ""
                user_msg_from_tag = None
                self.runner._last_user_msg_from_to_user = False
        elif self.runner._is_leaked_tool_params(user_msg):
            return await self._emit_format_error(user_msg)

        # Guard: detect repetitive output (stuttering) from lower-quality models.
        # Only run if enable_repetition_check is True in model config
        is_repetitive = False
        if getattr(self.runner.chat_api, "enable_repetition_check", False):
            # Check both user-visible message and internal thought process
            is_repetitive = self.runner._is_repeated_content(user_msg) or self.runner._is_repeated_content(thought_text)

        if is_repetitive:
            rewind_count = getattr(self.runner, "_repetition_rewind_count", 0)
            if rewind_count >= 1:
                logger.warning(
                    "[Runner] Repetition rewind already used once this turn, allowing it through to avoid infinite loop"
                )
            else:
                self.runner._repetition_rewind_count = rewind_count + 1
                logger.warning(
                    "[Runner] Detected repetitive output (stuttering) -- performing context rewind and requesting re-output"
                )

                # CRITICAL: Break the loop by removing the repetitive message from model history
                # This 'Context Rewind' prevents the model from being biased by its own recent mistake.
                if hasattr(self.runner.chat_api, "pop_last_assistant_message"):
                    self.runner.chat_api.pop_last_assistant_message()

                re_name = "repetition_error"
                re_detail = (
                    "Detected repetitive output (stuttering / loop). "
                    "CRITICAL: System has removed your last repetitive message from history. "
                    "Do NOT repeat your previous phrases. Do NOT explain why you looped. "
                    "Immediately take a DIFFERENT approach or skip directly to the tool call."
                )
                # Notify frontend
                hint = "检测到模型输出内容重复（复读机行为），系统已自动回退上下文并要求模型修正。"
                await self.runner._emit("info", hint)
                await self.runner._emit("status", "Repetition loop detected, rewinding and retrying...")

                _get_session_manager().add_event(
                    "info", {"text": hint}, turn_id=self.runner._current_turn, round_id=self.runner._current_round
                )

                return False, self.runner._summarize_result(re_name, f"Error: {re_detail}"), False

        # Auto-filter out raw conversational text when entering/exiting a task without a to_user wrapper
        if (
            (task_start or sys_cmd in ["task_complete", "task_failed"])
            and "<to_user>" not in full_response
            and "<to_user_reply>" not in full_response
            and "<to_user_end_task>" not in full_response
        ):
            logger.info("[Runner] Auto-filtering bare conversational text during task start/complete")
            user_msg = ""

        _saved_msg = None
        _saved_output_media = None
        if user_msg.strip():
            # --- Plugin Hook: on_before_send ---
            _send_msg = user_msg
            if self.runner._plugin_manager:
                _hook_ctx = await self.runner._plugin_manager.run_hook(
                    "on_before_send",
                    {
                        "message": _send_msg,
                        "agent_id": self.runner._agent_id,
                    },
                )
                _send_msg = _hook_ctx.get("message", _send_msg)
                if _hook_ctx.get("__stop__"):
                    logger.info("[Runner] on_before_send: send cancelled by plugin hook")
                    _send_msg = None
            if _send_msg and _send_msg.strip():
                # Emit to frontend immediately for real-time display,
                # but defer session persistence until after tool execution
                # so events (thought, tool_call, tool_result) appear before
                # the assistant message in current_session.json
                if user_msg_from_tag == "to_user_end_task":
                    event_type = "to_user_end_task"
                elif user_msg_from_tag == "to_user_reply":
                    event_type = "to_user_reply"
                else:
                    event_type = "to_user_final"
                await self.runner._emit(event_type, _send_msg)
                if output_media:
                    await self.runner._emit("output_media", output_media)
                _saved_msg = _send_msg
                _saved_output_media = output_media
                # A visible reply IS output — the no-output budget starts over.
                self.runner._no_output_retries = 0
                if user_msg_from_tag == "to_user_end_task":
                    _get_session_manager().mark_last_assistant_end_task(
                        sid=getattr(self.runner, "_turn_sid", "") or None
                    )
                # --- Plugin Hook: on_after_send ---
                if self.runner._plugin_manager:
                    await self.runner._plugin_manager.run_hook(
                        "on_after_send",
                        {
                            "message": _send_msg,
                            "agent_id": self.runner._agent_id,
                        },
                    )

        # --- 6. Tool call logic (placed after text is saved) ---
        tc_log = get_tool_call_debug_logger()
        tc_log.debug("[runner] full_response len=%d, first 500 chars: %s", len(full_response), full_response[:500])

        # Reuse the parse from above (native-FC data wins, else strategy parser).
        tool_calls = [(tc.name, tc.args) for tc in _parsed_tool_calls]
        if tool_calls:
            tc_log.info(
                "[runner] [OK] %d tool call(s) from %s",
                len(tool_calls),
                "Native FC data" if tool_data_from_api else "parser",
            )

        if tool_calls:
            tc_log.info("[runner] [tool] Executing %d parallel tool call(s)", len(tool_calls))

            # Housekeeping-only batch: the visible answer was already emitted
            # (to_user_final above) and the ONLY tool calls are pure side-channel
            # UI tools (suggest_followups — its receipt carries no information the
            # model needs). Forcing another LLM round after committing them used to
            # leave the web UI frozen on "进行中" for minutes with zero events
            # (seen 2026-09-21: full summary on screen, counters frozen, follow-up
            # chips hidden because the session still looked busy, until the user
            # pressed Stop). End the turn instead — same contract as a plain
            # text-only reply. A suggest_followups call WITHOUT visible text still
            # continues normally: the model owes the user the answer.
            _housekeeping_only_turn = bool(user_msg.strip()) and all(
                t_name.endswith("suggest_followups") for t_name, _ in tool_calls
            )

            # Phase 1: Execute ALL tools and collect results (no add_tool_result yet)
            _tool_results = []  # List of dicts with tool metadata for batch commit
            _control_flow_return = None  # If a control tool requests immediate return
            _stopped_by_user = False

            def _turn_stop_requested() -> bool:
                try:
                    sid = getattr(self.runner, "_turn_sid", "") or ""
                    if sid and input_hub.is_session_stop_requested(sid):
                        return True
                    return bool(input_hub.is_stop_requested())
                except Exception:
                    return False

            for call_index, (t_name, t_args_dict) in enumerate(tool_calls):
                if _turn_stop_requested():
                    _stopped_by_user = True
                    for rest_index in range(call_index, len(tool_calls)):
                        rest_name, _ = tool_calls[rest_index]
                        rid = f"call_stop_{rest_index}"
                        await self.runner._emit("tool_call", {"id": rid, "name": rest_name, "args": "{}"})
                        await self.runner._emit(
                            "tool_result",
                            {"id": rid, "name": rest_name, "result": "Cancelled: stopped by user"},
                        )
                    break

                tc_log.info("[runner] [tool] #%d: name=%r, args=%r", call_index, t_name, t_args_dict)
                call_id = f"call_{datetime.now().strftime('%M%S')}_{t_name}_{call_index}"
                _sanitized = {k: ("..." if v is ... else v) for k, v in t_args_dict.items()} if t_args_dict else {}
                t_args_json = json.dumps(_sanitized, ensure_ascii=False, indent=2) if _sanitized else "{}"

                tc_log.info("[runner] [emit] Emitting tool_call event: id=%s, name=%s", call_id, t_name)
                # Skip internal synthetic event_pipeline — hidden from frontend & session
                if t_name in ("system__event_pipeline", "system.event_pipeline"):
                    tc_log.debug("[runner] [emit] Skipping system__event_pipeline from frontend & session")
                else:
                    await self.runner._emit("tool_call", {"id": call_id, "name": t_name, "args": t_args_json})
                    _get_session_manager().add_event(
                        "tool_call",
                        {"id": call_id, "name": t_name, "args": t_args_json},
                        turn_id=self.runner._current_turn,
                        round_id=self.runner._current_round,
                    )

                # --- Plugin Hook: on_before_tool ---
                _skip_tool = False
                if self.runner._plugin_manager:
                    _hook_ctx = await self.runner._plugin_manager.run_hook(
                        "on_before_tool",
                        {
                            "tool_name": t_name,
                            "arguments": t_args_dict,
                            "agent_id": self.runner._agent_id,
                        },
                    )
                    t_name = _hook_ctx.get("tool_name", t_name)
                    t_args_dict = _hook_ctx.get("arguments", t_args_dict)
                    _skip_tool = _hook_ctx.get("skip", False)

                # Extract limit_token meta-parameter (stripped from args, not passed to tool)
                _limit_token = None
                if isinstance(t_args_dict, dict) and "limit_token" in t_args_dict:
                    _raw = t_args_dict.pop("limit_token")
                    try:
                        _v = int(_raw)
                        _limit_token = _v if _v > 0 else 0
                    except (ValueError, TypeError):
                        pass

                if _skip_tool:
                    result = _hook_ctx.get("result", "Tool call skipped by plugin hook.")
                    tc_log.info("[runner] [skip] Tool execution skipped by plugin hook")
                else:
                    tc_log.info("[runner] [run] Executing tool: %s", t_name)
                    # Bind call_id/sid so background Jobs can stream stdout to the CMD panel
                    from opensquad.tools.system import reset_tool_call_context, set_tool_call_context

                    _ctx_token = set_tool_call_context(
                        sid=getattr(self.runner, "_turn_sid", "") or "",
                        call_id=call_id,
                        tool_name=t_name,
                    )
                    try:
                        result = await self.runner.tool_registry.call(t_name, t_args_dict)
                    finally:
                        reset_tool_call_context(_ctx_token)
                    task_supervisor.report_activity()

                    if _turn_stop_requested():
                        _stopped_by_user = True

                    # Mid-tool drain: voice/web supplements arriving during a long tool
                    # should enter event_pipeline ASAP (not wait for the whole batch).
                    try:
                        _mid_sup = input_hub.get_all_pending()
                    except Exception:
                        _mid_sup = []
                    if _mid_sup:
                        from opensquad.event_pipeline import event_pipeline as _ep_mid

                        for _item in _mid_sup:
                            _c = (_item.get("content") or "").strip()
                            if not _c or _c == "[wakeup-urgent-command]":
                                continue
                            logger.info(
                                "[Runner] Mid-tool supplement from input_hub: %s",
                                _c[:80],
                            )
                            _imgs = _item.get("images") or []
                            if _imgs:
                                self.runner._current_images.extend(_imgs)
                            _atts = _item.get("attachments") or []
                            if _atts:
                                self.runner._current_attachments = list(self.runner._current_attachments or []) + list(
                                    _atts
                                )
                            _ep_mid.push_nowait(
                                source=_item.get("source", "web"),
                                content=_c,
                                metadata={
                                    "sender_name": _item.get("sender_name", ""),
                                    "channel": _item.get("channel", ""),
                                    "source": "input_hub",
                                    "images": _imgs,
                                    "attachments": _atts,
                                },
                                session_id=getattr(self.runner, "_turn_sid", "") or None,
                            )

                # Collaboration board auto-sync
                try:
                    import os as _os

                    from opensquad.collab_board import update_latest_tool as _cb_update_latest_tool

                    _agent_dir = getattr(self.runner, "_agent_dir", "") or ""
                    _agent_id = _os.path.basename(_agent_dir) if _agent_dir else "unknown_agent"
                    from opensquad.collab_board import list_tasks as _cb_list_tasks

                    _tasks = _cb_list_tasks()
                    _active_task_id = ""
                    for _t in _tasks:
                        if _t.get("status") == "active":
                            _active_task_id = str(_t.get("task_id") or "")
                            break
                    if _active_task_id:
                        _sensitive_tools = {
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
                        if t_name.startswith("collaboration.") or t_name.startswith("agent_setup."):
                            _sensitive_tools.add(t_name)
                        _should_sync = t_name not in _sensitive_tools
                        if _should_sync:
                            _cb_update_latest_tool(
                                collab_id=_active_task_id,
                                task_name="",
                                agent_id=_agent_id,
                                tool_name=t_name,
                                tool_result=result,
                            )
                except Exception:
                    pass

                result_preview = str(result)[:300] if result else ""
                if isinstance(result, str) and result.startswith("Error:"):
                    tc_log.warning("[runner] [FAIL] Tool %r returned ERROR: %s", t_name, result_preview)
                else:
                    tc_log.info("[runner] [OK] Tool %r returned OK (result length=%d chars)", t_name, len(str(result)))

                # --- Plugin Hook: on_after_tool ---
                if self.runner._plugin_manager:
                    _hook_ctx = await self.runner._plugin_manager.run_hook(
                        "on_after_tool",
                        {
                            "tool_name": t_name,
                            "arguments": t_args_dict,
                            "result": result,
                            "agent_id": self.runner._agent_id,
                            "model": getattr(self.runner.chat_api, "model", ""),
                        },
                    )
                    result = _hook_ctx.get("result", result)

                # TTS / media tools may return __output_media__ for chat bubble playback
                if isinstance(result, dict) and result.get("__output_media__"):
                    try:
                        await self.runner._emit("output_media", result["__output_media__"])
                        tc_log.info(
                            "[runner] Emitted output_media from tool %r: %d item(s)",
                            t_name,
                            len(result["__output_media__"]),
                        )
                    except Exception as _om_err:
                        tc_log.warning("[runner] Failed to emit tool output_media: %s", _om_err)

                # --- Plugin Hook: on_tool_error ---
                if self.runner._plugin_manager and isinstance(result, str) and result.startswith("Error:"):
                    _hook_ctx = await self.runner._plugin_manager.run_hook(
                        "on_tool_error",
                        {
                            "tool_name": t_name,
                            "arguments": t_args_dict,
                            "error": result,
                            "agent_id": self.runner._agent_id,
                        },
                    )
                    result = _hook_ctx.get("error", result)

                # Drain event pipeline (per-tool, may contain events that arrived during execution)
                from opensquad.event_pipeline import event_pipeline
                from opensquad.session_parallel import get_turn_local

                # Per-coroutine sid FIRST: `runner._turn_sid` is a shared attr
                # that every concurrent parallel turn overwrites on start, so
                # with 2+ live turns a tool execution could drain ANOTHER
                # session's pipeline bucket (cross-talk) — and drops this
                # session's mid-turn supplements instead.
                _tl_here = get_turn_local()
                _tool_sid = (_tl_here.sid if _tl_here and _tl_here.sid else "") or str(
                    getattr(self.runner, "_turn_sid", "") or ""
                )

                _raw_events = event_pipeline.drain_sync(session_id=_tool_sid or None)

                for evt in _raw_events:
                    if evt.source in ("web", "gateway", "group", "dm") and evt.content and evt.content.strip():
                        _get_session_manager().add_message(
                            "user",
                            evt.content,
                            sid=_tool_sid or None,
                        )
                        await self.runner._emit("user_msg", evt.content)
                        # Steer（引导注入）消费回执：该用户插话已随本轮工具结果
                        # 进入模型上下文。携带 client_id 供前端把引导气泡挪进
                        # 时间线（steer_consumed 在 protocol_version 注册）。
                        _steer_cid = str(evt.metadata.get("client_id") or "")
                        if _steer_cid:
                            await self.runner._emit(
                                "steer_consumed",
                                {"message_id": _steer_cid, "content": evt.content},
                            )
                    if evt.source == "vision_tool" and evt.metadata.get("action") == "inject_images":
                        img_paths = evt.metadata.get("image_paths", [])
                        if img_paths:
                            already = set(self.runner._current_images)
                            new_img_paths = [p for p in img_paths if p not in already]
                            if new_img_paths:
                                self.runner._current_images.extend(new_img_paths)
                            try:
                                _ipf = (
                                    os.path.join(self.runner._agent_dir, "img_path.txt")
                                    if self.runner._agent_dir
                                    else "img_path.txt"
                                )
                                with open(_ipf, "w", encoding="utf-8") as _f:
                                    _f.write(str(img_paths))
                            except Exception:
                                pass

                if _raw_events:
                    lines = ["", "--- External Events (arrived during processing) ---"]
                    for evt in _raw_events:
                        lines.append(evt.format_for_llm())
                    lines.append("--- End External Events ---")
                    _pipeline_events = "\n".join(lines)
                else:
                    _pipeline_events = ""

                # Prefer human message for LLM history; keep diff_* for UI emit
                _ui_extras: dict = {}
                if isinstance(result, dict):
                    # MCP Playwright screenshots etc. — stash base64 for next LLM turn
                    if result.get("__mcp_multimodal__"):
                        _mcp_imgs = result.get("images") or []
                        if _mcp_imgs:
                            _existing = list(getattr(self.runner, "_tool_result_images", None) or [])
                            _existing.extend(_mcp_imgs)
                            self.runner._tool_result_images = _existing
                            logger.info(
                                "[Runner] Queued %d MCP screenshot(s) for next vision turn",
                                len(_mcp_imgs),
                            )
                        _tool_result_text = result.get("text") or "Tool executed successfully."
                        if _mcp_imgs:
                            _tool_result_text = f"{_tool_result_text} [+{len(_mcp_imgs)} screenshot(s) attached]"
                    else:
                        for _k in ("diff_old", "diff_new", "diff_start_line"):
                            if _k in result and result[_k] is not None:
                                _ui_extras[_k] = result[_k]
                        # Never collapse a result down to `message` alone. A
                        # shell that died mid-command says "Command aborted
                        # (...)" and the discarded partial_data / reason /
                        # return_code were exactly what the model needed to tell
                        # "retry" from "switch strategy" (see _result_formatter).
                        _tool_result_text = format_result_for_llm(result)
                        # vision.read_image returns image_paths — inject even if
                        # event_pipeline push was skipped / drained elsewhere.
                        _vision_paths = result.get("image_paths")
                        if isinstance(_vision_paths, list) and _vision_paths:
                            already = set(self.runner._current_images or [])
                            new_paths = [p for p in _vision_paths if p and p not in already]
                            if new_paths:
                                self.runner._current_images = list(self.runner._current_images or []) + new_paths
                                logger.info(
                                    "[Runner] Injected %d vision.read_image path(s) for next turn",
                                    len(new_paths),
                                )
                else:
                    _tool_result_text = str(result) if result else "(empty result)"

                # Apply limit_token override or config-based truncation
                if _limit_token is not None:
                    _max_len = _limit_token if _limit_token > 0 else None
                else:
                    _max_len = self.runner._get_tool_output_max_chars()
                    _max_len = _max_len if _max_len > 0 else None
                _tool_result_text = self.runner._truncate_result_text(_tool_result_text, _max_len)

                # --- system.wait: special control flow (immediate return) ---
                if t_name in ("system.wait", "wait", "system__wait"):
                    if isinstance(result, dict) and result.get("status") == "success":
                        _ckpt_dir = getattr(self.runner, "_agent_dir", "") or ""
                        if _ckpt_dir:
                            try:
                                from opensquad import checkpoint as _ckpt2

                                _ckpt2.clear_checkpoint(_ckpt_dir)
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

                        # Merge any user messages that arrived during sleep so the
                        # next LLM turn sees real user content (text + image notice),
                        # not only the wake marker.
                        try:
                            _sleep_pending = input_hub.get_all_pending()
                        except Exception:
                            _sleep_pending = []
                        _user_parts: list[str] = []
                        for _item in _sleep_pending or []:
                            _c = (_item.get("content") or "").strip()
                            if not _c or _c == "[wakeup-urgent-command]":
                                continue
                            _user_parts.append(_c)
                            _imgs = _item.get("images") or []
                            if _imgs:
                                self.runner._current_images.extend(_imgs)
                            _atts = _item.get("attachments") or []
                            if _atts:
                                self.runner._current_attachments = list(self.runner._current_attachments or []) + list(
                                    _atts
                                )
                        if _user_parts:
                            wake_msg = (
                                wake_msg
                                + "\n\n[Messages received while sleeping — treat these as the user's real input; "
                                "do not dismiss them as wake notifications]\n" + "\n---\n".join(_user_parts)
                            )
                            logger.info(
                                f"[Runner] system.wait merged {len(_user_parts)} pending user msg(s) "
                                f"and {len(self.runner._current_images)} image(s) into wake context"
                            )

                        _wait_result_text = wake_msg

                        self.runner.chat_api.add_tool_result(
                            tool_name=t_name,
                            tool_args=t_args_dict,
                            result=_wait_result_text,
                            tool_call_id=call_id,
                        )
                        if _pipeline_events and hasattr(self.runner.chat_api, "add_pipeline_events"):
                            self.runner.chat_api.add_pipeline_events(_pipeline_events)

                        _get_session_manager().add_event(
                            "tool_result",
                            {"id": call_id, "name": t_name, "args": t_args_json, "result": _wait_result_text},
                            turn_id=self.runner._current_turn,
                            round_id=self.runner._current_round,
                        )
                        await self.runner._emit(
                            "tool_result",
                            {"id": call_id, "name": t_name, "args": t_args_json, "result": _wait_result_text},
                        )
                        logger.info(f"[Runner] system.wait finished: {wake_msg[:120]}")

                        _control_flow_return = (False, wake_msg, False)
                    continue

                # --- system.set_state: also special, emit state change immediately ---
                if t_name in ("system.set_state", "set_state", "system__set_state"):
                    if isinstance(result, dict) and result.get("status") == "success":
                        msg = result.get("message", "")
                        import re as _re

                        _m = _re.search(r"'(\w+)'", msg)
                        if _m:
                            actual_state = _m.group(1)
                            await self.runner._emit("state", actual_state)
                            if actual_state == "working" and not task_logger.has_active_task():
                                task_req = self.runner._last_user_input[:200]
                                tid = task_logger.start_task(task_req, "working")
                                logger.info(f"[Runner] Task recording started via set_state: {tid}")

                # --- Collect result for batch commit ---
                _tool_results.append(
                    {
                        "name": t_name,
                        "args": t_args_dict,
                        "args_json": t_args_json,
                        "result_text": _tool_result_text,
                        "call_id": call_id,
                        "pipeline_events": _pipeline_events,
                        "ui_extras": _ui_extras,
                        # Raw-signal flag for the repeated-action guard: "this
                        # call did not do its job" (aborted / timed out / error).
                        "failed": is_failure_result(result),
                    }
                )
                if _stopped_by_user or _turn_stop_requested():
                    _stopped_by_user = True
                    break

            if _control_flow_return:
                logger.info(f"[Runner] [DIAG] _control_flow_return set: {_control_flow_return}")
                return _control_flow_return

            # Phase 2: Batch-commit ALL tool results to chat_api history (one batch)
            tc_log.info("[runner] [tool] Batch-committing %d tool result(s) to chat_api history", len(_tool_results))
            for entry in _tool_results:
                self.runner.chat_api.add_tool_result(
                    tool_name=entry["name"],
                    tool_args=entry["args"],
                    result=entry["result_text"],
                    tool_call_id=entry["call_id"],
                )
                if entry["pipeline_events"] and hasattr(self.runner.chat_api, "add_pipeline_events"):
                    self.runner.chat_api.add_pipeline_events(entry["pipeline_events"])

                _get_session_manager().add_event(
                    "tool_result",
                    {
                        "id": entry["call_id"],
                        "name": entry["name"],
                        "args": entry["args_json"],
                        "result": entry["result_text"],
                        **(entry.get("ui_extras") or {}),
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
                        **(entry.get("ui_extras") or {}),
                    },
                )

                if task_logger.has_active_task():
                    task_logger.increment_turn(entry["name"])

            # ── Repeated-action guard: break runaway tool loops ──
            # Two independent signals, both decided by the pure core in
            # `_runner/_repeat_guard.py`:
            #   STRICT  — same call AND same result (the original read_file loop)
            #   FAILURE — same failure text with *changing* args, which is how a
            #             dead shell laundered the strict signal for 48 rounds
            #             (measured 2026-09-21, session 20260921_084718_9l88).
            try:
                _sid_key = str(getattr(self.runner, "_turn_sid", "") or "")
                _st = self.runner._tool_repeat_state.setdefault(_sid_key, _repeat_guard.new_state())
                _decision = _repeat_guard.evaluate(_st, _tool_results)

                if _decision["action"] == "abort":
                    _abort_msg = _repeat_guard.abort_message(_decision)
                    logger.warning(f"[Runner] {_abort_msg}")
                    await self.runner._emit("error", {"message": _abort_msg})
                    await self.runner._emit("to_user_final", f"[Error] {_abort_msg}")
                    _get_session_manager().add_event(
                        "info",
                        {
                            "event": "repeated_action_guard",
                            "signal": _decision["signal"],
                            "rounds": _decision["rounds"],
                            "text": _abort_msg,
                        },
                        turn_id=self.runner._current_turn,
                        round_id=self.runner._current_round,
                    )
                    return True, "", False

                if _decision["action"] == "hint":
                    _guard_text = _repeat_guard.hint_message(_decision)
                    logger.warning(f"[Runner] {_guard_text}")
                    self.runner.chat_api.add_tool_result(
                        tool_name="__repeated_action_guard__",
                        tool_args={},
                        result=_guard_text,
                        tool_call_id=f"guard_{datetime.now().strftime('%H%M%S')}",
                    )
                    await self.runner._emit(
                        "info",
                        {"message": "⚠️ 检测到重复工具调用，已提示模型调整策略（Repeated-Action Guard）"},
                    )
            except Exception as _g_e:
                logger.debug(f"[Runner] repeated-action guard skipped: {_g_e}")

            if _stopped_by_user or _turn_stop_requested():
                tc_log.info(
                    "[runner] [tool] Stopped by user after %d result(s) — ending turn",
                    len(_tool_results),
                )
                if _saved_msg:
                    _elapsed_ms = int(datetime.now().timestamp() * 1000) - int(self.runner._workflow_started_ms)
                    _get_session_manager().update_last_message_elapsed_ms(
                        _elapsed_ms, sid=getattr(self.runner, "_turn_sid", "") or None
                    )
                return True, "", False

            tc_log.info(
                "[runner] [tool] Batch commit complete: %d result(s), returning False,'',False", len(_tool_results)
            )
            # Signal the parallel turn loop that a tool executed this turn, so it
            # keeps looping (instead of relying on chat_api tool_data, which is
            # None for DSML/XML tool calls) to produce the follow-up final reply.
            self.runner._tool_result_generated = True
            if _saved_msg:
                # Update elapsed_ms on the assistant message that ChatAPI already saved
                _elapsed_ms = int(datetime.now().timestamp() * 1000) - int(self.runner._workflow_started_ms)
                _get_session_manager().update_last_message_elapsed_ms(
                    _elapsed_ms, sid=getattr(self.runner, "_turn_sid", "") or None
                )
                logger.info(
                    "[Runner] Tool turn complete: saved_msg_len=%d, elapsed_ms=%d",
                    len(_saved_msg),
                    _elapsed_ms,
                )
            if _housekeeping_only_turn:
                logger.info(
                    "[Runner] Answer already delivered + suggest_followups-only batch -> ending turn "
                    "(skipping the extra LLM round that used to freeze the UI after the final summary)"
                )
                return True, "", False
            return False, "", False

        # Check for auto-continue (trailing colon indicating tool intent)
        # We check the original full_response (after tag removal) to see if it ends with a colon,
        # even if user_msg was filtered out.
        clean_full = self.runner._remove_all_tags(full_response).strip()
        needs_tool = clean_full.endswith(":") or clean_full.endswith("：")

        if needs_tool and not tool_data_from_api:
            if finish_reason == "stop" and not stream_error:
                if (
                    self.runner._max_auto_continue_retries is None
                    or self.runner._auto_continue_retries < self.runner._max_auto_continue_retries
                ):
                    self.runner._auto_continue_retries += 1
                    auto_continue_prompt = (
                        "[System Prompt] You ended with a trailing colon, which usually means you intended to call a tool next. "
                        "Continue immediately by calling the appropriate tool."
                    )
                    logger.info(
                        "[Runner] Auto-continuing due to trailing colon (limit: %s/%s)",
                        self.runner._auto_continue_retries,
                        self.runner._max_auto_continue_retries,
                    )
                    # Same delivery rule as _handle_no_output: a prompt that only
                    # travels as `next_input` is dropped by skip_add_user and the
                    # model never sees the hint it is supposed to act on.
                    self._deliver_correction(auto_continue_prompt)
                    return False, auto_continue_prompt, False
                logger.warning("[Runner] Max auto-continue retries reached")
            elif stream_error:
                logger.warning("[Runner] Stream interrupted; skip auto-continue for tool-intent")

        # The assistant message itself is now persisted by ChatAPI.add_assistant_message()
        # (chat_api.py:1717) during streaming — always, not only for reasoning_content.
        # Here we only need to update elapsed_ms on that message.
        if _saved_msg:
            _elapsed_ms = int(datetime.now().timestamp() * 1000) - int(self.runner._workflow_started_ms)
            _get_session_manager().update_last_message_elapsed_ms(
                _elapsed_ms, sid=getattr(self.runner, "_turn_sid", "") or None
            )
            sess = _get_session_manager().session_data
            msgs = sess.get("messages", [])
            evts = sess.get("events", [])
            logger.info(
                "[Runner] Session after turn: %d messages (roles=%s), %d events (types=%s)",
                len(msgs),
                [m.get("role") for m in msgs[-5:]],
                len(evts),
                [e.get("type") for e in evts[-5:]],
            )

        if user_msg.strip():
            # to_user_reply expects a user response: keep waiting
            if self.runner._awaiting_user_reply:
                self.runner._awaiting_user_reply = False

            # KEY CHANGE: Don't mark workflow as ended.
            # The LLM has produced output and is now waiting for more events.
            # In the 'never stop' architecture, the LLM calls system.wait after replying,
            # so we should enter waiting state, not exit the loop entirely.

            # Don't add tool results since there are no tools in this branch
            # Return: continue loop, enter waiting state
            # Fix: return stop=True to exit inner LLM loop (turn_elapsed + state:idle)
            return True, "", False

        # Handle task status change
        if sys_cmd:
            logger.info(f"[Runner] System command received: {sys_cmd}")
            if sys_cmd in ["task_complete", "task_failed"]:
                completed = None
                if task_logger.has_active_task():
                    completed = task_logger.complete_task(
                        completion_status="completed" if sys_cmd == "task_complete" else "failed",
                        result_summary="Task finished",
                    )
                # --- Plugin Hook: on_task_complete ---
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
                await _get_state_manager().set_state("idle")
                await self.runner._emit("state", "idle")
                return True, "", False

        # Neither visible text nor a runnable tool call. Bounded + delivered —
        # see _handle_no_output for why the old bare-string return looped.
        return await self._handle_no_output(full_response)
