from __future__ import annotations

import asyncio
import json
import os
import os as _os
import re
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from opensquad.system_config import syscfg

from . import session_manager as _session_module
from . import state_manager as _state_module
from .chat_api import ChatAPI
from .claude_api import ClaudeAPI
from .context_builder import ContextBuilder
from .events import bus
from .input_hub import input_hub
from .log_setup import get_tool_call_debug_logger
from .message_queue import message_queue
from .parser import ResponseParser
from .registry import ToolRegistry
from .sleep_controller import sleep_controller
from .task import TaskManager
from .task_logger import task_logger
from .task_supervisor import task_supervisor
from .tool import logger
from .tool_call_strategy import ToolCallStrategySelector
from .utils import extract_and_remove_first_tag


# Dynamic accessor: always gets the latest instance after reinit
def _get_session_manager():
    """Return injected session_manager if current runner has one, else global singleton."""
    try:
        runner = getattr(_active_runner, "_injected_session_manager", None)
        if runner is not None:
            return runner
    except Exception:
        pass
    return _session_module.session_manager


def _get_state_manager():
    """Return injected state_manager if current runner has one, else global singleton."""
    try:
        runner = getattr(_active_runner, "_injected_state_manager", None)
        if runner is not None:
            return runner
    except Exception:
        pass
    return _state_module.state_manager


def _build_summary_payload(
    previous_summary: str,
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
    keep_last: int | None = None,
) -> str:
    lines: list[str] = []
    if previous_summary and previous_summary.strip():
        lines.append("[Previous Context Summary]")
        lines.append(previous_summary.strip())
    else:
        lines.append("[Previous Context Summary]\n(none)")

    lines.append("\n[Conversation Messages to Compress]")
    msgs_to_compress = messages[:-keep_last] if keep_last is not None and len(messages) > keep_last else messages
    for msg in msgs_to_compress:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        short = str(content).strip()
        if len(short) > 800:
            short = short[:800] + "..."
        lines.append(f"- {role}: {short}")

    lines.append("\n[Workflow Events to Compress]")
    for evt in events:
        etype = evt.get("type", "")
        data = evt.get("data", evt.get("content", ""))
        text = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data or "")
        text = text.strip()
        if not text:
            continue
        if len(text) > 800:
            text = text[:800] + "..."
        lines.append(f"- {etype}: {text}")

    lines.append("\n[Compression Rules]")
    lines.append(
        "Use the exact summary template with these sections: Current Task, Original Goal, Completed, Current State, Key Parameters, Unresolved Issues."
    )
    lines.append(
        "Current Task MUST describe what the agent is working on RIGHT NOW — the most recent user request in detail."
    )
    lines.append("Original Goal is the very first user request in this session, in one sentence.")
    lines.append(
        "Current State is the most important section — include open files, current directory, last tool executed."
    )
    lines.append("Preserve all file paths, IDs, ports, version numbers, config values, and error messages verbatim.")
    lines.append(
        "You MUST consider full workflow context: thought, plan, tool_call, tool_result, and info/status events."
    )
    lines.append("Completed must include Done/In progress/Todo sub-bullets with specific file paths.")
    if keep_last is not None:
        lines.append(
            f"Keep only the last {keep_last} messages in live chat history; everything above must be summarized into CONTEXT_SUMMARY."
        )
    else:
        lines.append(
            "Compress ALL messages and events into CONTEXT_SUMMARY. Keep only the newest 10% of content as live context."
        )
    return "\n".join(lines)


async def _run_external_summarizer(
    summary_payload: str,
    base_url: str,
    api_key: str,
    model: str,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    # Delegated summarizer uses the same runtime model endpoint as current agent,
    # unless model override is explicitly provided.
    model = syscfg.get("summarizer", "model") or model or "gpt-4o-mini"

    system_prompt = (
        "You are a summarizer agent. Return ONLY the summary in the specified template. "
        "Do not add commentary or extra sections."
    )

    try:
        # Use async OpenAI client to avoid blocking the event loop
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=60)
        logger.info(
            "[Runner] Calling external summarizer LLM (async): model=%s, payload_len=%d", model, len(summary_payload)
        )
        if on_chunk is None:
            response = await client.chat.completions.create(
                model=model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": summary_payload},
                ],
            )
            content = response.choices[0].message.content or ""
            return content.strip()

        stream = await client.chat.completions.create(
            model=model,
            temperature=0.2,
            stream=True,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": summary_payload},
            ],
        )

        parts: list[str] = []
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content or ""
            except Exception:
                delta = ""
            if not delta:
                continue
            parts.append(delta)
            try:
                await on_chunk(delta)
            except Exception:
                # Streaming callback failure must not break compression.
                pass

        result = "".join(parts).strip()
        if not result:
            logger.warning(
                "[Runner] External summarizer returned empty result, model=%s, payload_len=%d",
                model,
                len(summary_payload),
            )
        return result
    except Exception as e:
        logger.error(f"[Runner] External summarizer failed: {e}")
        return ""


# Module-level runner reference for tool hot-reload (each agent is an independent process, singleton-safe)
_active_runner: AgentRunner | None = None


def do_plugin_reload() -> dict:
    """
    Immediately reload plugins: re-read config.json to get the latest tools list,
    register newly added tools, and unload removed tools.
    Can be called by a tool mid-workflow without waiting for the agent to be idle.
    """
    if _active_runner is None:
        return {"success": False, "error": "Runner not initialized"}
    pm = _active_runner._plugin_manager
    if pm is None:
        return {"success": False, "error": "Plugin manager not available"}

    # Re-read config.json to get the latest tools list
    config_path = _active_runner._config_path
    new_tool_names = _active_runner._agent_tool_names
    new_cfg: dict = {}
    if config_path and _os.path.isfile(config_path):
        try:
            import json as _json

            with open(config_path, encoding="utf-8") as f:
                new_cfg = _json.load(f)
            new_tool_names = new_cfg.get("tools", _active_runner._agent_tool_names)
            _active_runner._agent_tool_names = new_tool_names
            _active_runner._agent_tool_levels = new_cfg.get("tool_levels", {})
            _active_runner._config_mtime = _os.path.getmtime(config_path)
        except Exception as e:
            logger.warning(f"[Runner] do_plugin_reload: failed to read config.json: {e}")

    # Re-register built-in core tools (im, collaboration, etc.) that are not managed by plugin_manager
    if new_cfg and _active_runner._agent_dir:
        try:
            from opensquad.agents_boot import register_builtin_tools_sync as _reg_builtin

            _reg_builtin(new_cfg, _active_runner.tool_registry, _active_runner._agent_dir)
            logger.info("[Runner] do_plugin_reload: built-in tools re-registered")
        except Exception as _bt_e:
            logger.warning(f"[Runner] do_plugin_reload: built-in tool re-registration failed: {_bt_e}")

    # Unload disabled plugins / load newly enabled plugins
    reload_result = pm.reload_plugins(
        registry=_active_runner.tool_registry,
        agent_id=_active_runner._agent_id,
        agent_tool_names=new_tool_names,
    )
    # Ensure all plugin tools in the tools list are registered (including loaded but not yet registered)
    pm.register_tools_to_agent(
        registry=_active_runner.tool_registry,
        agent_id=_active_runner._agent_id,
        agent_tool_names=new_tool_names,
        agent_tool_levels=_active_runner._agent_tool_levels,
    )
    logger.info(
        f"[Runner] Immediate plugin reload: loaded={reload_result.get('loaded')}, "
        f"unloaded={reload_result.get('unloaded')}"
    )
    return {
        "success": True,
        "loaded": reload_result.get("loaded", []),
        "unloaded": reload_result.get("unloaded", []),
        "active_tools": new_tool_names,
    }


class AgentRunner:
    """
    AgentRunner v3.5: Continuous conversation mode
    - Single session, no concept of end/new session
    - Auto-saves conversation history
    - Auto-loads on startup
    """

    def __init__(
        self,
        chat_api: ChatAPI | ClaudeAPI,
        tool_registry: ToolRegistry,
        hooks: dict | None = None,
        vision_config: dict | None = None,
        memory_manager=None,
        plugin_manager=None,
        agent_id: str = "",
        agent_tool_names: list[str] | None = None,
        config_path: str = "",
        session_manager=None,
        state_manager=None,
        agent_context=None,
    ):
        global _active_runner
        _active_runner = self
        # Use injected session/state manager if provided
        if agent_context is not None:
            if session_manager is None and hasattr(agent_context, "session_manager"):
                session_manager = agent_context.session_manager
            if state_manager is None and hasattr(agent_context, "state_manager"):
                state_manager = agent_context.state_manager
        self._injected_session_manager = session_manager
        self._injected_state_manager = state_manager
        self.chat_api = chat_api
        self.tool_registry = tool_registry
        self.task_manager = TaskManager()
        self._memory_manager = memory_manager  # Long-term memory manager (optional)
        self._plugin_manager = plugin_manager  # Plugin system manager (optional)
        self._agent_id = agent_id  # Agent ID (used by hooks and EventBus events)

        # Bridge state machine transitions to on_state_change plugin hook.
        # Uses asyncio.create_task so the hook fires outside the state lock,
        # preventing deadlock if a plugin handler reads state.
        # The task is tracked in _state_change_tasks to prevent GC and to log
        # exceptions via done-callback (asyncio otherwise swallows them).
        self._state_change_tasks: set[asyncio.Task] = set()

        if self._plugin_manager:
            _pm = self._plugin_manager
            _aid = self._agent_id
            _tasks_ref = self._state_change_tasks

            def _log_task_exception(t: asyncio.Task) -> None:
                if t.cancelled():
                    return
                exc = t.exception()
                if exc:
                    logger.warning("[Runner] on_state_change hook task raised: %r", exc, exc_info=exc)
                # Drop from the tracking set once done.
                _tasks_ref.discard(t)

            async def _on_state_change_hook(old_state: str, new_state: str):
                task = asyncio.create_task(
                    _pm.run_hook(
                        "on_state_change",
                        {
                            "old_state": old_state,
                            "new_state": new_state,
                            "agent_id": _aid,
                        },
                    )
                )
                _tasks_ref.add(task)
                task.add_done_callback(_log_task_exception)

            _get_state_manager().add_listener(_on_state_change_hook)
        self._agent_tool_names = agent_tool_names or []  # Tool names from agent config (for plugin hot-reload)
        self._agent_tool_levels: dict[str, str] = {}  # Per-tool level overrides from agent config
        self._config_path = config_path  # Path to config.json (for hot-reload watching)
        self._agent_dir = (
            _os.path.dirname(_os.path.abspath(config_path)) if config_path else ""
        )  # Agent's own directory
        self._config_mtime: float = 0.0  # Last known config.json mtime
        self._plugin_dir_mtime: float = 0.0  # Last known plugin dir mtime (for hot-reload caching)
        self._last_config_check: float = 0.0  # Last time config was checked (for throttling syscalls)
        if config_path and _os.path.isfile(config_path):
            self._config_mtime = _os.path.getmtime(config_path)
        self._current_input_source = "unknown"
        self._current_channel = ""  # Specific message channel (feishu/telegram/web/api)
        self._current_source_chat_id = ""  # Source chat_id (feishu/telegram)
        self._current_group_id = ""  # ChatPro group id for the current turn (if any)
        self._current_user_id = ""  # User ID for the current turn (set by GatewayAdapter)
        self._last_user_input = ""
        self.delegation_depth = 0  # Current delegation depth (used by SubAgentRunner); always 0 for parent agents
        self._current_images = []  # Image path list for the current turn
        self._current_attachments = []  # Attachment list for the current turn (includes audio/video/file)
        self._tool_result_images = []  # Base64 image list returned by MCP tools (e.g. Playwright screenshots)
        self._tool_result_image_paths = []  # Local image paths returned by vision plugin
        self._turn_sid = (
            _get_session_manager().get_current_session_id()
        )  # Session ID for the current turn (closure-safe)
        self._current_turn = 0  # Current turn index (1-based), reset on each handle call
        self._current_round = 0  # Message round (1-based), monotonically increasing, does not reset across messages; used to precisely attribute events to messages
        self._turn_started_ms: float = 0.0  # Current turn start time (ms), reset on each LLM call
        self._workflow_started_ms: float = 0.0  # Entire workflow start time (ms), set once when user sends a message

        # Task supervision state
        self._in_task = False
        self._awaiting_user_reply = False
        self._last_user_msg_from_to_user = False
        self._auto_continue_retries = 0
        self._max_auto_continue_retries = None

        # Streaming metadata (tracks tag that produced the streamed user text)
        self._streamed_user_tag = None

        # Vision config: {"is_img_mode": bool}
        # is_img_mode=true: main model supports images natively (controlled by config.json model.is_image), passed directly
        # is_img_mode=false: skip image input
        self._vision_config = vision_config or {}
        self._is_img_mode = self._vision_config.get("is_img_mode", False)

        # Agent lifecycle hooks
        # hooks = {"before_input": callable(agent_context) -> dict}
        self._hooks = hooks or {}

        # Dynamic context prefix (updated each round in _setup_prompt, prepended to user message)
        self._dynamic_context_prefix = ""

        # Tool Call Strategy Selection — must be initialized BEFORE ContextBuilder
        # Load config to determine tool call mode (XML vs Native Function Calling)
        _tool_strategy_config = {}
        if config_path and _os.path.isfile(config_path):
            try:
                with open(config_path, encoding="utf-8") as f:
                    _tool_strategy_config = json.load(f)
            except Exception as e:
                logger.warning(f"[Runner] Failed to load config from {config_path}: {e}")
        self._model_config: dict = _tool_strategy_config.get("model", {})
        self.tool_call_strategy = ToolCallStrategySelector.select(_tool_strategy_config, tool_registry)
        logger.info(f"[Runner] Tool call strategy: {self.tool_call_strategy.get_strategy_name()}")

        # Plan / Build mode (Cursor-style)
        from opensquad.agent_mode import (
            DEFAULT_MODE,
            normalize_mode,
            set_current_mode,
            set_mode_provider,
        )

        self.agent_mode = normalize_mode(_tool_strategy_config.get("agent_mode", DEFAULT_MODE))
        set_current_mode(self.agent_mode)
        set_mode_provider(lambda: getattr(self, "agent_mode", DEFAULT_MODE))
        logger.info(f"[Runner] Agent mode: {self.agent_mode}")

        # P1-1: ContextBuilder extracts prompt-building logic from the runner
        self._context_builder = ContextBuilder(
            chat_api=self.chat_api,
            tool_call_strategy=self.tool_call_strategy,
            task_manager=self.task_manager,
            plugin_manager=self._plugin_manager,
            hooks=self._hooks,
            memory_manager=self._memory_manager,
            config_path=self._config_path,
        )

        # Store current tools and tool_choice (updated by _setup_prompt)
        self._current_tools = None
        self._current_tool_choice = "auto"

        # Inject sid_provider into ChatAPI so it can include the correct session_id when emitting events
        self.chat_api._sid_provider = lambda: self._turn_sid
        self.chat_api._user_id_provider = lambda: self._current_user_id

        # Historical cumulative token stats (across sessions/restarts), not written to chat_api
        # chat_api.total_* only records the current session (reset to 0 on new session)
        self._hist_input_tokens: int = 0
        self._hist_output_tokens: int = 0
        self._hist_requests: int = 0
        self._hist_cache_read_tokens: int = 0
        self._hist_cache_creation_tokens: int = 0

        # Load history session into chat_api
        self._load_history()

        # Restore last process's cumulative stats from disk on startup, preventing stats from resetting after restart
        self._restore_cumulative_stats()

        # Write token_stats.json once on startup (based on already-loaded history session)
        # This allows the Launcher management panel to immediately display the progress bar without waiting for the first conversation
        self._broadcast_token_stats_sync()

        # ── Startup readiness: buffer pre-ready messages ──
        self._agent_ready = False
        self._pending_buffer: list[dict] = []
        # Subscribe to agent_ready from boot (fires before run() starts)
        bus.subscribe("agent_ready", lambda _: self._replay_pending())

    async def _emit(self, etype, data):
        """Event push with session_id (uses the sid captured at turn start to avoid routing errors on session switch)"""
        sid = self._turn_sid
        await bus.emit_async(etype, {"sid": sid, "data": data})

    def _load_history(self):
        """Load history session"""
        logger.info("[Runner] _load_history() CALLED")

        # Preserve the system prompt
        system_msg = None
        if self.chat_api.req and self.chat_api.req[0].get("role") == "system":
            system_msg = self.chat_api.req[0]

        self.chat_api.req = []
        if system_msg:
            self.chat_api.req.append(system_msg)

        # Restore persisted summary for CONTEXT_SUMMARY prompt injection
        _sess = _get_session_manager().session_data or {}
        self.chat_api._latest_summary = _sess.get("latest_summary", "") or ""

        history = _get_session_manager().get_messages_for_chat_api(limit=50)
        if history:
            # Add history messages (limit count to avoid exceeding context)
            loaded_roles: list[str] = []
            skipped_info: list[str] = []
            for msg in history[-30:]:  # Last 30 messages
                role = msg.get("role", "")
                content = msg.get("content", "")
                has_tool_calls = isinstance(msg.get("tool_calls"), list) and msg["tool_calls"]

                # Keep any message that carries LLM-visible state.
                # - user/assistant/system/tool roles are always relevant
                # - assistant messages with tool_calls are kept even if content is empty/None
                # - content can be string, list (Claude multimodal), or None (tool-only assistant)
                keep = False
                if role in ("user", "assistant", "system", "tool"):
                    if (
                        (role == "assistant" and has_tool_calls)
                        or isinstance(content, str | list)
                        or (content is None and role in ("assistant", "tool"))
                        or (role == "user" and (isinstance(content, str) or content is None))
                    ):
                        keep = True

                if keep:
                    self.chat_api.req.append(msg)
                    loaded_roles.append(role)
                else:
                    skipped_info.append(f"{role}:{type(content).__name__}")

            logger.warning(
                "[Runner] _load_history: history=%d, loaded=%d (roles=%s), skipped=%s",
                len(history),
                len(self.chat_api.req) - (1 if system_msg else 0),
                loaded_roles,
                skipped_info,
            )

            # FIX B: Restore _prev_reasoning_content from last assistant message for DeepSeek V4
            # After session load, _prev_reasoning_content is always "". Without this fix,
            # _validate_and_fix_reasoning_content() skips all injection logic and the API returns 400.
            _last_asst = next((m for m in reversed(self.chat_api.req) if m.get("role") == "assistant"), None)
            if _last_asst and _last_asst.get("reasoning_content"):
                self.chat_api._prev_reasoning_content = _last_asst["reasoning_content"]
                logger.info(
                    f"[Runner] Restored _prev_reasoning_content ({len(_last_asst['reasoning_content'])} chars) from session history"
                )
            else:
                self.chat_api._prev_reasoning_content = ""

            # CRITICAL FIX: Validate and fix message sequence for DeepSeek compatibility
            # OpenAI/DeepSeek API requires: role=tool must follow role=assistant with tool_calls
            # After loading from session storage, tool_calls data may be lost, causing invalid sequence
            self._validate_message_sequence()

            logger.info(f"[Runner] Loaded {len(history)} messages from history")
        else:
            logger.info("[Runner] Session history is empty")

    def _validate_message_sequence(self):
        """Fix message sequence for DeepSeek/OpenAI compatibility.

        The OpenAI API requires that role=tool messages follow a role=assistant
        with tool_calls field. After loading from session storage (JSON),
        tool_calls data may be lost, causing invalid sequence errors.

        Also handles the case where role=tool appears without proper preceding
        tool_call_id linkage, which is invalid for streaming resume scenarios.
        """
        import uuid

        req = self.chat_api.req
        fixed = 0

        i = 0
        while i < len(req):
            msg = req[i]
            if msg.get("role") == "tool":
                # Check if previous message is assistant with tool_calls
                if i == 0:
                    # No previous message - remove orphan tool message
                    logger.warning("[Runner] Removing orphan role=tool at index 0")
                    req.pop(i)
                    fixed += 1
                    continue

                prev = req[i - 1]
                if prev.get("role") != "assistant" or not prev.get("tool_calls"):
                    # Previous message doesn't have tool_calls - inject synthetic assistant
                    synth_id = f"synth_{uuid.uuid4().hex[:8]}"
                    logger.info(f"[Runner] Injecting synthetic assistant before role=tool (index={i})")
                    # FIX A: Find the most recent assistant with reasoning_content BEFORE the tool message.
                    # prev here is NOT an assistant (that's why we entered this branch), so we need
                    # to scan backward to find the last assistant that had reasoning_content.
                    synth_reasoning = ""
                    for _lookback in reversed(req[:i]):
                        if _lookback.get("role") == "assistant" and _lookback.get("reasoning_content"):
                            synth_reasoning = _lookback["reasoning_content"]
                            break
                    synth_msg = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": synth_id,
                                "type": "function",
                                "function": {
                                    "name": "restored_session",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                    if synth_reasoning:
                        synth_msg["reasoning_content"] = synth_reasoning
                        logger.info(
                            f"[Runner] Copied reasoning_content ({len(synth_reasoning)} chars) to synthetic assistant"
                        )
                    req.insert(i, synth_msg)
                    # Update tool_call_id on the actual tool message (now at i+2 after insert)
                    if i + 2 < len(req):
                        if not req[i + 2].get("tool_call_id") or req[i + 2].get("tool_call_id", "").startswith(
                            "pipeline_events_"
                        ):
                            req[i + 2]["tool_call_id"] = synth_id
                    fixed += 1
                    i += 1  # Skip to after the inserted message
                    continue

            i += 1

        if fixed > 0:
            logger.info(f"[Runner] Fixed {fixed} message sequence issues for API compatibility")

    async def run(self, initial_query: str | None = None, **kwargs):
        """Start and run the agent - continuous conversation mode"""
        logger.info(f"[Runner] ===== run() STARTED, initial_query={'yes' if initial_query else 'None'} =====")

        # Mark agent ready and replay any buffered pre-boot messages
        self._agent_ready = True
        bus.emit("agent_ready", {"agent_id": self._agent_id})
        self._replay_pending()

        while True:
            # Check wake-up
            if sleep_controller._wake_reason:
                wake_prompt = self._generate_wake_prompt()
                initial_query = wake_prompt
                logger.info(f"[Runner] Processing wake up: {sleep_controller._wake_reason}")

            # Get input
            _pending_group_messages = []  # group messages drained from message_queue this iteration
            if not initial_query:
                # Prevent message loss: drain message_queue for orphaned messages before entering wait.
                # (group messages received while in working state only enter the queue; they are not pushed to input_hub and would be lost after the turn ends)
                # Instead of pushing __PROCESS_QUEUE__ to input_hub (which caused duplicate responses),
                # drain directly and merge with the next user input.
                if message_queue.size > 0:
                    _pending_group_messages = message_queue.get_all()
                    logger.info(f"[Runner] Drained {len(_pending_group_messages)} orphaned messages from queue")

                # Prevent infinite wait: if a group message was just sent (awaiting reply),
                # the agent is waiting for a group reply.
                # Enter a time-limited sleep instead of blocking indefinitely,
                # to avoid the agent getting stuck if the group never replies.
                # During sleep, group messages will wake it up via message_router -> sleep_controller.wake_up()
                elif message_queue.size == 0:
                    from opensquad.message_router import message_router as _message_router

                    if _message_router.awaiting_reply:
                        sleep_seconds = _message_router._await_reply_seconds
                        logger.info(
                            f"[Runner] Post-send idle detected (awaiting reply), auto-sleeping {sleep_seconds}s to await reply"
                        )
                        await self._emit("status", "sleeping")
                        await self._emit("info", f"Group message sent, waiting for reply ({sleep_seconds}s timeout)...")
                        await _get_state_manager().set_state("sleeping")
                        _message_router.clear_await_reply()  # Clear after entering sleep to avoid re-triggering on wake
                        wake_info = await sleep_controller.sleep(sleep_seconds)
                        await _get_state_manager().set_state("idle")
                        await self._emit("status", "idle")
                        # After waking: generate wake prompt for AI to process
                        wake_prompt = self._generate_wake_prompt()
                        initial_query = wake_prompt
                        self._current_input_source = "wake"
                        logger.info(
                            f"[Runner] Auto-sleep ended: {wake_info.get('wake_type')}, reason: {wake_info.get('wake_reason')}"
                        )
                        continue

                current_state = await _get_state_manager().get_state()
                logger.debug(f"[Runner] ===== IDLE: waiting for input (state={current_state}) =====")
                await self._emit("status", f"State: {current_state}, waiting...")

                # Hot-reload check: poll input_hub with 5s timeout,
                # check plugin reload signal between polls
                user_input_data = None
                while user_input_data is None:
                    # Check plugin hot-reload signal (.reload_ts)
                    if self._plugin_manager and self._plugin_manager.check_reload_needed():
                        reload_result = self._plugin_manager.reload_plugins(
                            registry=self.tool_registry,
                            agent_id=self._agent_id,
                            agent_tool_names=self._agent_tool_names,
                        )
                        if reload_result["loaded"] or reload_result["unloaded"]:
                            logger.info(
                                f"[Runner] Plugin hot-reload: loaded={reload_result['loaded']}, "
                                f"unloaded={reload_result['unloaded']}"
                            )

                    # Check config.json hot-reload (tools list may have changed)
                    if self._config_path and time.time() - self._last_config_check >= 5.0:
                        self._last_config_check = time.time()
                        try:
                            mtime = os.path.getmtime(self._config_path)
                            if mtime > self._config_mtime:
                                self._config_mtime = mtime
                                import json as _json

                                with open(self._config_path, encoding="utf-8") as _f:
                                    _new_cfg = _json.load(_f)
                                new_tools = _new_cfg.get("tools", [])
                                new_levels = _new_cfg.get("tool_levels", {})
                                tools_changed = new_tools != self._agent_tool_names
                                levels_changed = new_levels != self._agent_tool_levels
                                if tools_changed or levels_changed:
                                    if tools_changed:
                                        logger.info(
                                            f"[Runner] config.json changed: tools "
                                            f"{self._agent_tool_names} -> {new_tools}"
                                        )
                                    if levels_changed:
                                        logger.info(
                                            f"[Runner] config.json changed: tool_levels "
                                            f"{self._agent_tool_levels} -> {new_levels}"
                                        )
                                    self._agent_tool_names = new_tools
                                    self._agent_tool_levels = new_levels
                                    # Re-register built-in core tools (im, collaboration, etc.)
                                    # registry.register is idempotent -- safe to call for already-registered tools
                                    # Must re-register on both tools_changed AND levels_changed so
                                    # tool_levels overrides in config.json take effect immediately.
                                    if (tools_changed or levels_changed) and self._agent_dir:
                                        try:
                                            from opensquad.agents_boot import (
                                                register_builtin_tools_sync as _reg_builtin,
                                            )

                                            _reg_builtin(
                                                _new_cfg,
                                                self.tool_registry,
                                                self._agent_dir,
                                            )
                                            logger.info("[Runner] Config hot-reload: built-in tools re-registered")
                                        except Exception as _bt_e:
                                            logger.warning(
                                                f"[Runner] Config hot-reload: built-in tool re-registration failed: {_bt_e}"
                                            )
                                    if self._plugin_manager:
                                        # Load any newly enabled plugins
                                        self._plugin_manager.reload_plugins(
                                            registry=self.tool_registry,
                                            agent_id=self._agent_id,
                                            agent_tool_names=self._agent_tool_names,
                                        )
                                        # Re-register tools for already-loaded plugins
                                        # (registry.register is idempotent, safe to call again)
                                        self._plugin_manager.register_tools_to_agent(
                                            registry=self.tool_registry,
                                            agent_id=self._agent_id,
                                            agent_tool_names=self._agent_tool_names,
                                            agent_tool_levels=self._agent_tool_levels,
                                        )
                                        logger.info("[Runner] Config hot-reload complete: plugin tools re-registered")
                                # Check model config hot-reload
                                new_model = _new_cfg.get("model", {})
                                if new_model != self._model_config:
                                    logger.info("[Runner] config.json model changed, hot-reloading...")
                                    try:
                                        from opensquad.model_switch import apply_model_reload

                                        await apply_model_reload(self, new_model)
                                    except Exception as _me:
                                        logger.warning(f"[Runner] Model hot-reload failed: {_me}")
                        except Exception as _e:
                            logger.warning(f"[Runner] config.json hot-reload check failed: {_e}")

                    try:
                        logger.debug("[Runner] Polling input_hub (5s timeout)...")
                        # Log urgent queue state before polling (debug level to avoid spam)
                        _urgent_q = input_hub._get_urgent_queue()
                        logger.debug(
                            f"[Runner] Pre-poll state: urgent_queue_size={_urgent_q.qsize()}, normal_queue_size={input_hub._get_queue().qsize()}"
                        )
                        user_input_data = await asyncio.wait_for(input_hub.get_user_response(), timeout=5.0)
                        logger.info(
                            f"[Runner] ===== GOT INPUT from input_hub: source={user_input_data.get('source')}, content={str(user_input_data.get('content', ''))[:80]} ====="
                        )
                    except asyncio.TimeoutError:
                        # Bug 4 fix: after a cooldown period in idle state, there may be backlogged messages
                        # in the queue but no trigger. Check on every poll timeout:
                        # if message_queue has messages and we're not in a cooldown period,
                        # drain directly to avoid __PROCESS_QUEUE__ through input_hub (caused duplicate responses).
                        if message_queue.size > 0:
                            from opensquad.message_router import message_router

                            if not message_router.in_cooldown:
                                _drained = message_queue.get_all()
                                logger.info(
                                    f"[Runner] Idle drain: {len(_drained)} queued msg(s) found, merging with pending input"
                                )
                                _pending_group_messages = _drained
                                # Build a synthetic group-message-only input so the turn loop processes it
                                user_input_data = {"source": "group:idle_drain", "content": ""}
                        # Check urgent queue after timeout (items may have arrived during the wait)
                        _uq_after = input_hub._get_urgent_queue()
                        if _uq_after.qsize() > 0:
                            logger.info(
                                f"[Runner] Post-timeout: urgent queue has {_uq_after.qsize()} items, will retry on next poll"
                            )
                        continue  # loop back to check reload signal
                    except asyncio.CancelledError:
                        # Safety net: if a leaked anyio CancelledError still
                        # reaches the runner (despite the global anyio_patches),
                        # drain uncancel and recover. The global patch in
                        # anyio_patches.py prevents the infinite re-cancel loop
                        # at source.
                        current_task = asyncio.current_task()
                        if current_task and hasattr(current_task, "uncancel"):
                            while current_task.uncancel() > 0:
                                pass
                        logger.warning(
                            "[Runner] CancelledError caught (safety net recovered via uncancel), continuing..."
                        )
                        continue
                    else:
                        # Successful poll — reset cancel counter
                        self._cancel_count = 0
                initial_query = user_input_data["content"]
                # NOTE: Do NOT expand <user_send_skill> here. Expansion injects the
                # full SKILL.md body for the model, but that must not be persisted to
                # session history / user_msg (UI would show the entire skill after
                # compress/reload). Expand only when building current_input below.
                source = user_input_data.get("source", "unknown")
                self._current_input_source = source
                self._current_channel = user_input_data.get("channel", "")
                self._current_images = user_input_data.get("images", [])
                self._current_attachments = user_input_data.get("attachments", [])
                self._current_sender_name = user_input_data.get("sender_name", "")
                self._current_chat_name = user_input_data.get("chat_name", "")
                self._current_source_chat_id = user_input_data.get("source_chat_id", "")
                self._current_user_id = user_input_data.get("user_id", "")
                # Update shared runtime context for tools
                from opensquad import _runtime_ctx

                _runtime_ctx.update(
                    {
                        "channel": self._current_channel,
                        "sender_name": self._current_sender_name,
                        "chat_name": self._current_chat_name,
                        "source_chat_id": self._current_source_chat_id,
                        "input_source": self._current_input_source,
                        "attachments": self._current_attachments,
                    }
                )
                logger.info(
                    f"[Runner] Got input: source={source}, content_len={len(initial_query)}, raw_repr={initial_query[:80]!r}"
                )

                # --- Merge pending group messages (drained from message_queue earlier) ---
                if _pending_group_messages:
                    msg_parts = []
                    all_images = []
                    for msg in _pending_group_messages:
                        if msg.type == "group":
                            msg_parts.append(
                                f"[{msg.source_name} | group_id={msg.source_id}] {msg.sender_name}: {msg.content}"
                            )
                            if getattr(msg, "source_id", None):
                                self._current_group_id = str(msg.source_id)
                        elif msg.type == "dm":
                            msg_parts.append(f"[DM] {msg.sender_name}: {msg.content}")
                        if msg.images:
                            all_images.extend(msg.images)
                    if all_images:
                        self._current_images = (self._current_images or []) + all_images
                    formatted = "\n".join(msg_parts)
                    if initial_query:
                        initial_query += f"\n\n[Simultaneously received group messages]\n{formatted}"
                    else:
                        initial_query = (
                            "[Messages]\n"
                            + formatted
                            + "[Messages received, please decide how to reply based on the source]"
                        )
                        source = "chatpro"
                        self._current_input_source = source
                        self._current_channel = "chatpro_group"
                    logger.info(f"[Runner] Merged {len(_pending_group_messages)} pending group messages into input")
                    _pending_group_messages = []  # Clear after merge

                # --- Handle internal commands BEFORE plugin hook and group message append ---
                # These system commands must be intercepted before any plugin or message merging
                # can modify initial_query, otherwise the string comparison would fail.

                # __PROCESS_QUEUE__ drains the message queue and builds an AI input.
                # Must be handled HERE (before the on_message_received hook and the
                # "Simultaneously received group messages" append at line ~1058, which
                # would corrupt the command into "__PROCESS_QUEUE__\n\n[Simultaneously..."
                # and cause the startswith("__") re-inject check to loop forever).
                # Use startswith (not ==) in case any prior step already appended text.
                if isinstance(initial_query, str) and initial_query.startswith("__PROCESS_QUEUE__"):
                    pending = message_queue.get_all()
                    _extra_web = input_hub.get_all_pending()
                    logger.info(
                        f"[Runner] __PROCESS_QUEUE__ (early intercept), pending={len(pending)}, extra_web={len(_extra_web)}"
                    )
                    if not pending and not _extra_web:
                        initial_query = None
                        continue
                    if not pending and _extra_web:
                        _first_web = _extra_web[0]
                        initial_query = _first_web.get("content", "")
                        source = _first_web.get("source", "gateway")
                        self._current_input_source = source
                        self._current_channel = _first_web.get("channel", "web")
                        self._current_images = _first_web.get("images", [])
                        self._current_attachments = _first_web.get("attachments", [])
                        self._current_sender_name = _first_web.get("sender_name", "")
                        self._current_chat_name = _first_web.get("chat_name", "")
                        self._current_source_chat_id = _first_web.get("source_chat_id", "")
                        self._current_user_id = _first_web.get("user_id", "")
                        if len(_extra_web) > 1:
                            _extra_parts = [_wd.get("content", "") for _wd in _extra_web[1:] if _wd.get("content")]
                            if _extra_parts:
                                initial_query += "\n" + "\n".join(_extra_parts)
                        logger.info(
                            "[Runner] __PROCESS_QUEUE__ (early) with only web messages, processing as regular input"
                        )
                    else:
                        msg_parts = []
                        all_images = []
                        for msg in pending:
                            if msg.type == "group":
                                msg_parts.append(
                                    f"[{msg.source_name} | group_id={msg.source_id}] {msg.sender_name}: {msg.content}"
                                )
                                if getattr(msg, "source_id", None):
                                    self._current_group_id = str(msg.source_id)
                            elif msg.type == "dm":
                                msg_parts.append(f"[DM] {msg.sender_name}: {msg.content}")
                            if msg.images:
                                all_images.extend(msg.images)
                        if all_images:
                            self._current_images = all_images
                        initial_query = (
                            "[Messages]\n"
                            + "\n".join(msg_parts)
                            + "[Messages received, please decide how to reply based on the source]"
                        )
                        source = "chatpro"
                        self._current_input_source = source
                        self._current_channel = "chatpro_group"
                        if _extra_web:
                            _web_parts = []
                            for _wd in _extra_web:
                                _wc = _wd.get("content", "")
                                if _wc and not _wc.startswith("__PROCESS_QUEUE__"):
                                    _web_parts.append(f"[Web User] {_wc}")
                            if _web_parts:
                                initial_query += "\n\n[Simultaneously received web messages]\n" + "\n".join(_web_parts)

                # Ignore __STOP__ command (residual from request_stop() pushed into urgent queue)
                if initial_query == "__STOP__":
                    logger.info("[Runner] Ignoring __STOP__ command in main loop (already handled)")
                    input_hub.clear_stop_request()
                    initial_query = None
                    continue

                # New client connection: request immediate broadcast of current token stats
                if initial_query == "__REQUEST_TOKEN_STATS__":
                    logger.info("[Runner] Command: Request token stats broadcast")
                    await self._broadcast_token_stats()
                    initial_query = None
                    continue

                # Resume workflow after refresh if requested
                if initial_query == "__RESUME_WORKFLOW__":
                    logger.info("[Runner] Command: Resume workflow after refresh")
                    initial_query = "Continue the previous task from where you left off."
                    self._current_input_source = "wake"

                if initial_query == "__NEW_SESSION__":
                    logger.info("[Runner] Command: Start New Session")
                    self._reset_session_stats()  # Archive current session stats, reset chat_api counters
                    _drain_before = input_hub.get_all_pending()
                    if _drain_before:
                        self._pending_buffer.extend(
                            [
                                {
                                    "content": _d["content"],
                                    "source": _d.get("source", "web"),
                                    "images": _d.get("images"),
                                    "attachments": _d.get("attachments"),
                                    "channel": _d.get("channel", ""),
                                }
                                for _d in _drain_before
                            ]
                        )
                        logger.info(f"[Runner] Buffered {len(_drain_before)} msg(s) during session switch")
                    _get_session_manager().start_new_session()
                    self._turn_sid = _get_session_manager().get_current_session_id()
                    self._load_history()  # Reload (now empty)
                    await self._emit("turn_start", 0)  # Trigger frontend cleanup

                    # Update session list for frontend
                    await bus.emit_async("session_list", _get_session_manager().get_session_list())

                    # Send new current session info
                    await bus.emit_async(
                        "current_session",
                        {"id": _get_session_manager().get_current_session_id(), "title": "Current Session"},
                    )

                    await self._broadcast_token_stats()  # Immediately broadcast new session (session=0) stats
                    await self._emit("info", "New session started")
                    # Re-send turn_elapsed to close any workflow timer block that may exist in the frontend
                    _now_ms = int(datetime.now().timestamp() * 1000)
                    await self._emit("turn_elapsed", {"started_ms": _now_ms, "ended_ms": _now_ms})
                    initial_query = None
                    continue

                # Handle session load command
                if initial_query.startswith("__LOAD_SESSION__:"):
                    sid = initial_query.split(":", 1)[1]
                    if _get_session_manager().load_history_session(sid):
                        self._turn_sid = sid
                        self._load_history()
                        await self._emit("turn_start", 0)
                        history_data = {
                            "messages": _get_session_manager().get_messages(),
                            "events": _get_session_manager().get_events(),
                            "session_id": sid,
                            "is_working_session": True,
                        }
                        await bus.emit_async("history_sync", history_data)
                        await bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                        # Refresh session list immediately
                        await bus.emit_async("session_list", _get_session_manager().get_session_list())
                        await self._emit("info", f"Session loaded: {sid}")
                        # Re-send turn_elapsed to close any workflow timer block that may exist in the frontend
                        _now_ms = int(datetime.now().timestamp() * 1000)
                        await self._emit("turn_elapsed", {"started_ms": _now_ms, "ended_ms": _now_ms})
                    initial_query = None
                    continue

                # Handle manual context compression command
                if initial_query == "__COMPRESS_CONTEXT__":
                    logger.info("[Runner] Command: Manual context compression ENTER")
                    _start_ms = int(datetime.now().timestamp() * 1000)
                    _trace_id = f"cmp_{_start_ms}_{self._current_round}"
                    await self._emit("turn_start", {"turn": 0, "started_ms": _start_ms})
                    prev_summary = getattr(self.chat_api, "_latest_summary", "")

                    # Token-based compression: gather ALL messages and events for summarization
                    msgs_for_summary = _get_session_manager().get_messages(limit=500)
                    all_events_for_summary = _get_session_manager().get_events(limit=2000)
                    summary_payload = _build_summary_payload(
                        prev_summary,
                        msgs_for_summary,
                        all_events_for_summary,
                        keep_last=None,  # Token-based, no message count threshold
                    )

                    summary_stream_id = f"compress_{_trace_id}"
                    summary_text_chunks: list[str] = []

                    async def _on_summary_chunk(delta: str):
                        summary_text_chunks.append(delta)
                        await self._emit(
                            "summary_stream",
                            {
                                "id": summary_stream_id,
                                "delta": delta,
                                "trace_id": _trace_id,
                            },
                        )

                    # Call external summarizer LLM (separate from current agent) with streaming output
                    summary_text = await _run_external_summarizer(
                        summary_payload,
                        base_url=self.chat_api.base_url,
                        api_key=self.chat_api.api_key,
                        model=self.chat_api.model,
                        on_chunk=_on_summary_chunk,
                    )
                    if not summary_text and summary_text_chunks:
                        summary_text = "".join(summary_text_chunks).strip()
                    await self._emit(
                        "summary_stream",
                        {
                            "id": summary_stream_id,
                            "done": True,
                            "trace_id": _trace_id,
                        },
                    )

                    result = _get_session_manager().compress_current_session(
                        previous_summary=prev_summary, external_summary=summary_text
                    )
                    if result.get("compressed"):
                        self.chat_api._latest_summary = result.get("summary_content", "")
                    if summary_text:
                        _summary_evt = {
                            "event": "context_summary_generated",
                            "text": "Context summary generated",
                            "summary": summary_text,
                            "trace_id": _trace_id,
                        }
                        # Persist so refresh/history replay can still show the generated summary
                        _get_session_manager().add_event("info", _summary_evt, turn_id=0, round_id=self._current_round)
                        # Persist an explicit summary message as durable fallback for UI recovery
                        # when realtime WS events are dropped during reconnect.
                        _get_session_manager().add_message("system", summary_text, msg_type="context_summary")
                        await self._emit("info", _summary_evt)
                        # Archive into self_learn corpus (append-only learning material)
                        try:
                            from plugins.self_learn.archive import archive_compression_summary

                            _sm = _get_session_manager()
                            _title = ""
                            try:
                                _title = str((_sm.session_data or {}).get("title") or "")
                            except Exception:
                                pass
                            archive_compression_summary(
                                summary_text,
                                session_id=_sm.get_current_session_id() or "",
                                session_title=_title,
                                source="manual_compress",
                                agent_dir=getattr(self, "_agent_dir", None) or None,
                                agent_id=getattr(self, "_agent_id", "") or "",
                            )
                        except Exception:
                            logger.debug("[Runner] self_learn archive skipped", exc_info=True)
                    self._load_history()
                    self.chat_api.req = self.chat_api._prepare_messages()
                    sid = _get_session_manager().get_current_session_id()
                    history_data = {
                        "messages": _get_session_manager().get_messages(),
                        "events": _get_session_manager().get_events(),
                        "session_id": sid,
                        "is_working_session": True,
                        "reason": "compression",
                    }
                    await bus.emit_async("history_sync", history_data)
                    await bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                    await bus.emit_async("session_list", _get_session_manager().get_session_list())
                    # Force prompt refresh so CONTEXT_SUMMARY replacement takes effect immediately.
                    # Also force one prompt_update snapshot after compression even if textual diff
                    # is not detected by equality checks.
                    self._has_prompt_snapshot = False
                    await self._setup_prompt()
                    _end_ms = int(datetime.now().timestamp() * 1000)
                    await self._emit("turn_elapsed", {"started_ms": _start_ms, "ended_ms": _end_ms})
                    # Broadcast updated token stats after compression so frontend
                    # reflects the reduced context size immediately.
                    await self._broadcast_token_stats()
                    initial_query = None
                    continue

                # --- Plugin Hook: on_message_received ---
                if self._plugin_manager:
                    _hook_before = repr(initial_query)
                    logger.info(f"[Runner] Before on_message_received hook: {initial_query[:80]!r}")
                    _hook_ctx = await self._plugin_manager.run_hook(
                        "on_message_received",
                        {
                            "message": initial_query,
                            "channel": self._current_channel,
                            "sender_name": getattr(self, "_current_sender_name", ""),
                            "chat_name": getattr(self, "_current_chat_name", ""),
                            "source_chat_id": self._current_source_chat_id,
                            "input_source": self._current_input_source,
                        },
                    )
                    initial_query = _hook_ctx.get("message", initial_query)
                    _hook_after = repr(initial_query)
                    logger.info(f"[Runner] After on_message_received hook: {initial_query[:80]!r}")
                    if _hook_before != _hook_after:
                        logger.info(
                            f"[Runner] WARNING: on_message_received CHANGED message from {initial_query[:80]!r} to {initial_query[:80]!r}"
                        )
                    if _hook_ctx.get("__stop__"):
                        logger.info("[Runner] on_message_received: chain stopped by plugin, skipping message")
                        initial_query = None
                        continue

                # If input_hub received accumulated group messages while waiting, append them to initial_query
                if user_input_data.get("has_messages") and user_input_data.get("message_context"):
                    msg_ctx = user_input_data["message_context"]
                    initial_query += f"\n\n[Simultaneously received group messages - for reference only, do not auto-call im.send_message to reply]\n{msg_ctx}"
                    logger.info(
                        f"[Runner] Appended {user_input_data.get('message_count', 0)} pending messages to user input"
                    )

                # Handle switch-and-reply command
                if initial_query.startswith("__SWITCH_AND_REPLY__:"):
                    parts = initial_query.split(":", 2)
                    if len(parts) >= 3:
                        sid, reply_content = parts[1], (parts[2] or "").strip()
                        current_sid = _get_session_manager().get_current_session_id()
                        if sid != current_sid:
                            # Different session: need to switch context
                            logger.info(f"[Runner] Switch context: {current_sid} -> {sid}")
                            if _get_session_manager().load_history_session(sid):
                                self._turn_sid = sid
                                self._load_history()
                                # Only start a turn when there is actual reply content.
                                # Empty switch must not emit turn_start (would flip UI to thinking).
                                if reply_content:
                                    await self._emit("turn_start", 0)
                                await bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                                await bus.emit_async("session_list", _get_session_manager().get_session_list())
                            else:
                                # Switch failed (target session does not exist), stay on current session
                                logger.warning(f"[Runner] Session {sid} not found, staying on {current_sid}")
                        else:
                            # Same session: skip reload
                            logger.info(f"[Runner] Same session {sid}, skip context switch")
                        # Empty content = switch only (do not feed blank user turn to the LLM)
                        if not reply_content:
                            initial_query = None
                            await _get_state_manager().set_state("idle")
                            await self._emit("state", "idle")
                            continue
                        initial_query = reply_content
                # -----------------------

                # NOTE: __PROCESS_QUEUE__ handling used to live here, but the equality check
                # failed once the "Simultaneously received group messages" append at the top
                # of this block (or the urgent-path append) corrupted the string, causing an
                # infinite re-inject loop. It is now intercepted EARLY at line ~824, before
                # any plugin hook or message append can touch it. See that block for details.

                if source == "wake" or initial_query.startswith("[Wake-"):
                    logger.info(f"[Runner] Woken up by: {initial_query}")

            # --- Re-check system commands from urgent interrupt path ---
            # When a system command arrives via urgent queue during task execution,
            # the urgent handler sets initial_query (truthy), causing the
            # if not initial_query: block above to be skipped entirely.
            # We must intercept these commands here before they leak to the LLM.

            # NOTE: __PROCESS_QUEUE__ was previously re-handled here for the urgent path,
            # but the early intercept at line ~824 (using startswith, before any append)
            # already covers every delivery path. This redundant block used == and never
            # matched once the command was appended to, so it has been removed.

            if isinstance(initial_query, str) and initial_query.startswith("__"):
                logger.info(f"[Runner] System command from urgent path, re-injecting: {initial_query[:80]}")
                input_hub.push_urgent(initial_query, source="system")
                initial_query = None
                continue

            # For group message sources, do not store as user message in history or display
            if self._current_input_source == "chatpro":
                # Group messages are only passed to AI as context, not displayed in the chat box
                self._last_user_input = initial_query
                self._turn_sid = _get_session_manager().get_current_session_id()
                # Do not call _get_session_manager().add_message("user", ...)
                # Do not call self._emit('user_msg', ...)
            else:
                # Normal user message
                _get_session_manager().add_message("user", initial_query)
                self._last_user_input = initial_query
                self._turn_sid = _get_session_manager().get_current_session_id()
                await self._emit("user_msg", initial_query)

            # Initialize task
            initial_query, task_id = self._prepare_task(initial_query)

            # Add source label for AI (does not affect storage or frontend display)
            channel = getattr(self, "_current_channel", "") or ""
            sender_name = getattr(self, "_current_sender_name", "") or ""
            chat_name = getattr(self, "_current_chat_name", "") or ""
            if self._current_input_source in ("web", "gateway"):
                # Map channel to human-readable label
                _channel_labels = {
                    "web": "Web UI",
                    "feishu": "Feishu",
                    "feishu_group": "Feishu Group",
                    "feishu_private": "Feishu Private",
                    "telegram": "Telegram",
                    "telegram_group": "Telegram Group",
                    "telegram_private": "Telegram Private",
                    "api": "External API",
                    "external": "External Integration",
                    "external-ws": "External WebSocket",
                }
                label = _channel_labels.get(channel, channel if channel else "Web UI")
                # Build context parts
                ctx_parts = [f"Source: {label}"]
                if chat_name:
                    ctx_parts.append(f"Group: {chat_name}")
                if sender_name:
                    ctx_parts.append(f"Sender: {sender_name}")
                source_chat_id = getattr(self, "_current_source_chat_id", "") or ""
                if source_chat_id:
                    ctx_parts.append(f"chat_id: {source_chat_id}")
                f"[{', '.join(ctx_parts)}] {initial_query}"
            elif self._current_input_source == "cli":
                pass
            # chatpro group messages already prefixed in __PROCESS_QUEUE__ path

            # Broadcast token stats (after user input)
            # Note: user messages are uniformly appended by chat_api.chat() -> add_user_message(),
            # so we no longer manually append here to avoid two consecutive user messages per round.
            await self._broadcast_token_stats()

            await self._emit("status", f"Session continuous, State: {await _get_state_manager().get_state()}")

            # Task plan is managed by LLM via <plan> tags, not auto-injected
            # LLM decides when to use <plan> based on task complexity
            await self._setup_prompt()

            # Expand skill tags only for the model prompt — session already stored the
            # compact <user_send_skill> form for UI / history.
            _llm_user_text = initial_query
            if isinstance(_llm_user_text, str) and "<user_send_skill>" in _llm_user_text.lower():
                try:
                    from opensquad.skill_loader import expand_user_send_skill

                    _llm_user_text = expand_user_send_skill(_llm_user_text)
                except Exception as e:
                    logger.warning(f"[Runner] expand_user_send_skill failed: {e}")

            current_input = f"User input: {_llm_user_text}"
            if self._dynamic_context_prefix:
                current_input = self._dynamic_context_prefix + current_input
            if self._current_attachments:
                att_lines = []
                for att in self._current_attachments:
                    if isinstance(att, dict):
                        name = att.get("original_name") or att.get("filename") or att.get("name") or "unknown"
                        path = att.get("path") or att.get("url") or ""
                        media_type = att.get("type")
                        if not media_type:
                            if att.get("is_video"):
                                media_type = "video"
                            elif att.get("is_audio") or att.get("type") == "voice":
                                media_type = "audio"
                            else:
                                media_type = "file"
                        if media_type == "voice":
                            media_type = "audio"
                        if path:
                            att_lines.append(f"[{media_type}] {name} (path={path})")
                        else:
                            att_lines.append(f"[{media_type}] {name}")
                    else:
                        att_lines.append(str(att))
                if att_lines:
                    current_input += "\n\n[Attachments]\n" + "\n".join(att_lines)
            max_turns = kwargs.get("max_turns", 200)
            task_finished = False
            # round_id monotonically increasing: incremented once per new user message, never resets across the session
            # Used by the frontend to attribute all events from this response to the corresponding assistant message
            self._current_round += 1
            logger.debug(
                f"[Runner] ===== ENTERING TURN LOOP (max_turns={max_turns}, input_source={self._current_input_source}, round={self._current_round}) ====="
            )
            # Record the workflow start time (before all turns, set only once)
            self._workflow_started_ms = datetime.now().timestamp() * 1000
            # Persist a workflow start marker so refresh can reconstruct in-progress blocks
            # (including Working elapsed seconds via started_ms).
            _get_session_manager().add_event(
                "info",
                {"text": "Workflow started", "started_ms": int(self._workflow_started_ms)},
                turn_id=0,
                round_id=self._current_round,
            )

            for turn in range(max_turns):
                logger.debug(f"[Runner] --- Turn {turn + 1}/{max_turns} ---")
                self._current_turn = turn + 1
                self._turn_started_ms = datetime.now().timestamp() * 1000
                await self._emit("turn_start", {"turn": turn + 1, "started_ms": int(self._workflow_started_ms)})

                # Per-turn repetition rewind counter — only allow ONE rewind per turn.
                # Prevents infinite "detect → rewind → repeat → detect" loops when the
                # model keeps producing the same repetitive output.
                self._repetition_rewind_count = 0

                # ========== Safety interrupt checkpoint 1: Before turn starts ==========
                urgent_commands = input_hub.check_urgent_commands()
                if urgent_commands:
                    for cmd in urgent_commands:
                        content = cmd.get("content", "")
                        if content == "__STOP__":
                            logger.info("[Runner] Stop command received, safely stopping task flow")
                            input_hub.clear_stop_request()
                            task_finished = True
                            initial_query = None
                            await self._emit("status", "Task stopped by user")
                            break
                        elif content == "__NEW_SESSION__":
                            # Urgent session switch: start new session
                            logger.info("[Runner] Urgent: New session requested during task")
                            self._reset_session_stats()  # Archive current session stats, reset chat_api counters
                            _get_session_manager().start_new_session()
                            self._turn_sid = _get_session_manager().get_current_session_id()
                            self._load_history()
                            await self._emit("turn_start", 0)
                            await bus.emit_async("session_list", _get_session_manager().get_session_list())
                            await bus.emit_async(
                                "current_session",
                                {"id": _get_session_manager().get_current_session_id(), "title": "Current Session"},
                            )
                            await self._broadcast_token_stats()  # Immediately broadcast new session (session=0) stats
                            await self._emit("info", "New session started")
                            task_finished = True
                            initial_query = None
                            break
                        elif content == "__COMPRESS_CONTEXT__":
                            # Handle manual context compression command (urgent)
                            logger.info("[Runner] Urgent: Compress context command received during task")
                            initial_query = "__COMPRESS_CONTEXT__"  # Let the outer loop handle it
                            task_finished = True
                            break
                        elif content.startswith("__LOAD_SESSION__:"):
                            # Urgent session switch: load specified session
                            sid = content.split(":", 1)[1]
                            logger.info(f"[Runner] Urgent: Load session {sid} during task")
                            if _get_session_manager().load_history_session(sid):
                                self._turn_sid = sid
                                self._load_history()
                                await self._emit("turn_start", 0)
                                history_data = {
                                    "messages": _get_session_manager().get_messages(),
                                    "events": _get_session_manager().get_events(),
                                    "session_id": sid,
                                    "is_working_session": True,
                                }
                                await bus.emit_async("history_sync", history_data)
                                await bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                                await bus.emit_async("session_list", _get_session_manager().get_session_list())
                                await self._emit("info", f"Session loaded: {sid}")
                            task_finished = True
                            initial_query = None
                            break
                        elif content.startswith("__SWITCH_AND_REPLY__:"):
                            # Handle switch-and-reply command (urgent queue version)
                            parts = content.split(":", 2)
                            if len(parts) >= 3:
                                sid, reply_content = parts[1], (parts[2] or "").strip()
                                # Extract images attached to the urgent queue command
                                cmd_images = cmd.get("images", [])
                                if cmd_images:
                                    self._current_images = cmd_images
                                    logger.info(f"[Runner] Urgent SWITCH_AND_REPLY with {len(cmd_images)} images")
                                cmd_attachments = cmd.get("attachments", [])
                                if cmd_attachments:
                                    self._current_attachments = cmd_attachments
                                    logger.info(
                                        f"[Runner] Urgent SWITCH_AND_REPLY with {len(cmd_attachments)} attachments"
                                    )
                                current_sid = _get_session_manager().get_current_session_id()
                                if sid != current_sid:
                                    # Different session: need to switch context
                                    logger.info(f"[Runner] Urgent switch context: {current_sid} -> {sid}")
                                    if _get_session_manager().load_history_session(sid):
                                        self._turn_sid = sid
                                        self._load_history()
                                        if reply_content:
                                            await self._emit("turn_start", 0)
                                        await bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                                        await bus.emit_async("session_list", _get_session_manager().get_session_list())
                                else:
                                    # Same session: skip reload
                                    logger.info(f"[Runner] Urgent same session {sid}, skip context switch")
                                # Empty content = switch only; do not start an empty LLM turn
                                initial_query = reply_content or None
                                task_finished = True
                                break
                    if task_finished:
                        break

                # Check for supplementary input (web/gateway messages during working state)
                # These must be pushed into event_pipeline so they flow through
                # add_pipeline_events (role=tool) to the LLM on the next tool result.
                supplements = input_hub.get_all_pending()
                if supplements:
                    from opensquad.event_pipeline import event_pipeline

                    for item in supplements:
                        content = item.get("content", "") or ""
                        # Ignore synthetic wake sentinel left by older adapters
                        if content.strip() == "[wakeup-urgent-command]":
                            continue
                        logger.info(f"[Runner] Mid-work supplement from input_hub: {content[:80]}")
                        # Preserve images / attachments that arrived mid-turn
                        _sup_imgs = item.get("images") or []
                        if _sup_imgs:
                            self._current_images.extend(_sup_imgs)
                            logger.info(
                                f"[Runner] Mid-work supplement carried {len(_sup_imgs)} image(s) into _current_images"
                            )
                        _sup_atts = item.get("attachments") or []
                        if _sup_atts:
                            self._current_attachments = list(self._current_attachments or []) + list(_sup_atts)
                        # Push into event_pipeline so it gets delivered via role=tool
                        event_pipeline.push_nowait(
                            source=item.get("source", "web"),
                            content=content,
                            metadata={
                                "sender_name": item.get("sender_name", ""),
                                "channel": item.get("channel", ""),
                                "source": "input_hub",
                                "images": _sup_imgs,
                                "attachments": _sup_atts,
                            },
                        )

                # Check message pipeline
                # CRITICAL: External messages (group/DM) flow through event_pipeline → role=tool.
                # Do NOT append them to current_input — that would cause add_user_message() to
                # add them again as role=user, wasting tokens. They'll arrive via role=tool
                # through the tool result merge path (add_pipeline_events).
                pending = message_queue.get_all()
                if pending:
                    queue_images = []
                    for msg in pending:
                        if msg.images:
                            queue_images.extend(msg.images)

                    # Collect images only (text flows via event_pipeline/role=tool)
                    if queue_images:
                        self._current_images.extend(queue_images)
                        logger.info(f"[Runner] Collected {len(queue_images)} images from mid-turn queue")

                # ========== Safety interrupt checkpoint 2: Before sending request ==========
                if input_hub.is_stop_requested():
                    logger.info("[Runner] Stop requested before API call")
                    input_hub.clear_stop_request()
                    task_finished = True
                    initial_query = None
                    await self._emit("status", "Task stopped")
                    break

                # Execute AI conversation
                self._setup_event_dispatch()
                await self._emit("status", "working")  # Notify frontend of working state before starting

                # Check for image paths written by vision plugin (img_path.txt)
                _img_path_file = os.path.join(self._agent_dir, "img_path.txt") if self._agent_dir else "img_path.txt"
                if os.path.exists(_img_path_file):
                    try:
                        with open(_img_path_file, encoding="utf-8") as f:
                            content = f.read().strip()
                            if content:
                                import ast

                                try:
                                    paths = ast.literal_eval(content)
                                    if isinstance(paths, list):
                                        # Deduplicate: only add paths not already in _current_images
                                        already = set(self._current_images)
                                        new_paths = [p for p in paths if os.path.exists(p) and p not in already]
                                        if new_paths:
                                            self._current_images.extend(new_paths)
                                            logger.info(
                                                f"[Runner] Loaded {len(new_paths)} new images from img_path.txt"
                                            )
                                    elif isinstance(paths, str):
                                        if os.path.exists(paths):
                                            self._current_images.append(paths)
                                            logger.info("[Runner] Loaded 1 image from img_path.txt")
                                except Exception as e:
                                    logger.error(f"[Runner] Failed to parse img_path.txt: {e}")
                        # Clear after reading
                        with open(_img_path_file, "w", encoding="utf-8") as f:
                            f.write("")
                    except Exception as e:
                        logger.error(f"[Runner] Failed to read img_path.txt: {e}")

                logger.debug(
                    f"[Runner] ===== CALLING LLM (source: {self._current_input_source}, content_len={len(current_input)}) ====="
                )

                # === DEDUPLICATION FIX ===
                # External messages (web, group, DM) flow through event_pipeline → role=tool.
                # On the FIRST turn of a session, the user message is added via
                # chat() → add_user_message() → role=user, so we must drain event_pipeline
                # to prevent the same message from appearing twice (once as role=user,
                # once as role=tool).
                # On SUBSEQUENT turns, skip_add_user=True so external events only appear
                # as role=tool via add_pipeline_events.
                _is_first_turn = turn == 0
                if _is_first_turn:
                    from opensquad.event_pipeline import event_pipeline

                    drained = event_pipeline.drain_formatted_sync()
                    if drained:
                        logger.info(
                            f"[Runner] Pre-chat event_pipeline drain: {len(drained)} chars (prevents role=user + role=tool duplication)"
                        )
                # Image handling: is_img_mode=true (i.e. config.json model.is_image=true) passes directly to main model, false skips
                _native_images = None  # Images passed directly to chat_api in native mode (file paths)
                _b64_images = None  # Base64 images returned by MCP tools
                _audio_paths: list = []
                _video_paths: list = []
                if self._current_attachments:
                    for att in self._current_attachments:
                        if isinstance(att, dict):
                            media_type = att.get("type")
                            path = att.get("path") or att.get("url") or ""
                            if not media_type:
                                if att.get("is_video"):
                                    media_type = "video"
                                elif att.get("is_audio"):
                                    media_type = "audio"
                                else:
                                    media_type = "file"
                            if media_type in ("audio", "voice") and path:
                                _audio_paths.append(path)
                            elif media_type == "video" and path:
                                _video_paths.append(path)
                if self._current_images:
                    _images = self._current_images
                    self._current_images = []  # Process only once
                    logger.info(
                        f"[Runner] Processing {len(_images)} image(s), is_img_mode={self._is_img_mode}, paths={_images}"
                    )

                    # Verify files exist
                    import os as _os

                    for _ip in _images:
                        if not _os.path.exists(_ip):
                            logger.error(f"[Runner] Image file NOT FOUND: {_ip}")
                            await self._emit("info", f"Warning: image not found: {_os.path.basename(_ip)}")

                    if self._is_img_mode:
                        # Main model supports images natively, pass directly
                        _native_images = _images
                        logger.info(
                            f"[Runner] [VISION] Native mode: _native_images={_native_images}, is_img_mode={self._is_img_mode}"
                        )
                        await self._emit("info", f"Sending {len(_images)} image(s) to model")
                    else:
                        # Main model does not support native image input (is_image=false).
                        # Still tell the model that images arrived + text must be answered.
                        import os as _os

                        _basenames = [_os.path.basename(p) for p in _images]
                        _note = (
                            f"\n\n[System notice] The user sent {len(_images)} image(s) "
                            f"({', '.join(_basenames)}), but the current model does not support "
                            f"image input (model.is_image=false). You cannot see the pixels. "
                            f"You MUST still respond to the user's text, acknowledge that "
                            f"image(s) were received, and say vision is unavailable on this model."
                        )
                        current_input = (current_input or "") + _note
                        logger.warning(
                            f"[Runner] [VISION] Model doesn't support native vision (is_image=False), "
                            f"skipped {len(_images)} image(s) but injected text notice"
                        )
                        await self._emit(
                            "info",
                            f"Current model does not support image input; skipped {len(_images)} image(s). "
                            f"Text notice injected so the agent still sees that images arrived.",
                        )

                if _audio_paths or _video_paths:
                    if _audio_paths:
                        current_input += "\n\n[Audio attachment paths]\n" + "\n".join(_audio_paths)
                        await self._emit("info", f"Received {len(_audio_paths)} audio file(s)")
                    if _video_paths:
                        current_input += "\n\n[Video attachment paths]\n" + "\n".join(_video_paths)
                        await self._emit("info", f"Received {len(_video_paths)} video file(s)")

                if _audio_paths:
                    try:
                        from opensquad import agent_runtime_context as _arc

                        _has_asr = bool((_arc.agent_config.get("voice") or {}).get("asr_card"))
                    except Exception:
                        _has_asr = False
                    if _has_asr:
                        current_input += (
                            "\n[Tip] To transcribe audio, call step_voice.transcribe_audio_file(audio_path=...)."
                        )
                    else:
                        current_input += (
                            "\n[Tip] To transcribe audio, call step_voice.transcribe_audio_file(audio_path=...) "
                            "if configured, otherwise whisper_transcribe.transcribe_audio_file(audio_path=...)."
                        )
                if _video_paths:
                    current_input += "\n[Tip] To process video, use system.run_session_job to call ffmpeg to extract audio/keyframes first."

                # Base64 screenshots returned by MCP tools (e.g. Playwright browser_take_screenshot)
                if hasattr(self, "_tool_result_images") and self._tool_result_images:
                    if self._is_img_mode:
                        _b64_images = self._tool_result_images
                        # await self._emit('info', f"MCP tool returned {len(_b64_images)} screenshot(s), passing directly to model")  # Disabled: removed per user request
                        logger.info(f"[Runner] MCP tool images: {len(_b64_images)} image(s) to chat_api")
                    else:
                        # Models that don't support native images (is_image=False): ignore
                        logger.warning(
                            "[Runner] MCP tool returned images but model doesn't support native vision (is_image=False), skipping"
                        )
                    self._tool_result_images = []

                # --- Plugin Hook: on_before_llm ---
                if self._plugin_manager:
                    _hook_ctx = await self._plugin_manager.run_hook(
                        "on_before_llm",
                        {
                            "messages": self.chat_api.req if hasattr(self.chat_api, "req") else [],
                            "model": getattr(self.chat_api, "model", ""),
                            "agent_id": self._agent_id,
                        },
                    )
                    if _hook_ctx.get("__stop__"):
                        logger.info("[Runner] on_before_llm: chain stopped by plugin, skipping LLM call")
                        task_finished = True
                        initial_query = None
                        break

                _llm_timeout = getattr(self.chat_api, "timeout", 30.0)
                _asyncio_timeout = (
                    _llm_timeout + 15.0
                )  # asyncio-layer timeout slightly higher than API layer to ensure API timeout triggers first

                try:
                    logger.info(
                        f"[Runner] [VISION] >>> Calling chat() with image_path={_native_images}, turn={turn}, is_first_turn={_is_first_turn}"
                    )
                    ai_response = await asyncio.wait_for(
                        self.chat_api.chat(
                            current_input,
                            image_path=_native_images,
                            image_b64_list=_b64_images,
                            audio_path=_audio_paths if getattr(self.chat_api, "is_audio_model", False) else None,
                            video_path=_video_paths if getattr(self.chat_api, "is_video_model", False) else None,
                            tools=self._current_tools,
                            tool_choice=self._current_tool_choice,
                            tool_call_strategy=self.tool_call_strategy,
                            skip_add_user=not _is_first_turn,
                        ),
                        timeout=_asyncio_timeout,
                    )
                    # After chat_api.chat() adds the user message (first turn) and any
                    # pipeline tool events, broadcast updated stats so the frontend sees
                    # the full request breakdown instead of only the pre-chat history.
                    await self._broadcast_token_stats()
                except asyncio.TimeoutError:
                    logger.error(f"[Runner] LLM API call timed out after {_asyncio_timeout}s, aborting turn")
                    await self._emit("status", "LLM API response timed out, please try again later")
                    await self._emit(
                        "error",
                        {
                            "message": f"LLM API call timed out after {_asyncio_timeout}s. Please check your network or try again later.",
                        },
                    )
                    task_finished = True
                    initial_query = None
                    break
                except asyncio.CancelledError as _cancel_err:
                    # Safety net: anyio's CancelScope can leak CancelledError out of
                    # httpx/anyio.connect_tcp() (used by all LLM API calls). Without
                    # this catch, the CancelledError propagates to
                    # agent_boot_phases.await_runner_shutdown(), which treats it as
                    # "Runner task interrupted" and restarts the runner with
                    # initial_query=None — silently dropping the user's message.
                    # Drain the host task's uncancel counter (Python 3.12+) so
                    # subsequent awaits don't re-raise, then end this turn gracefully
                    # so the user can retry.
                    _current_task = asyncio.current_task()
                    if _current_task and hasattr(_current_task, "uncancel"):
                        while _current_task.uncancel() > 0:
                            pass
                    _cancel_msg = str(_cancel_err) or ""
                    logger.warning(
                        f"[Runner] CancelledError during chat() (recovered via safety net): {_cancel_msg[:200]}"
                    )
                    await self._emit("status", "LLM API call was interrupted, please retry")
                    await self._emit(
                        "error",
                        {
                            "message": "LLM API call was interrupted by an internal cancellation. Please retry your message.",
                        },
                    )
                    task_finished = True
                    initial_query = None
                    break
                except Exception as e:
                    err_msg = str(e)
                    # Classify common errors for user-friendly messages
                    if "401" in err_msg or "Unauthorized" in err_msg or "invalid api key" in err_msg.lower():
                        friendly = (
                            "LLM API authentication failed (HTTP 401). "
                            "Your api_key is invalid or expired. "
                            "Please update the api_key in model_cards/*.json and restart the agent."
                        )
                    elif "403" in err_msg or "Forbidden" in err_msg:
                        friendly = (
                            "LLM API access denied (HTTP 403). "
                            "Your api_key may not have permission for this model. "
                            "Check your API provider account settings."
                        )
                    elif "429" in err_msg or "rate limit" in err_msg.lower():
                        friendly = "LLM API rate limit exceeded (HTTP 429). Please wait a moment and try again."
                    elif "Connection" in err_msg or "connect" in err_msg.lower() or "refused" in err_msg.lower():
                        friendly = (
                            f"Unable to connect to LLM API: {err_msg[:200]}. "
                            "Please check your network and base_url configuration."
                        )
                    else:
                        friendly = f"LLM API call failed: {err_msg[:300]}"
                    logger.error(f"[Runner] LLM API call failed: {err_msg[:500]}")
                    await self._emit("status", "LLM API call failed")
                    await self._emit(
                        "error",
                        {
                            "message": friendly,
                        },
                    )
                    task_finished = True
                    initial_query = None
                    break

                # LLM call completed (success or error)

                # --- Auto-compression post-processing ---
                if getattr(self.chat_api, "_auto_compressed", False):
                    _summary = getattr(self.chat_api, "_latest_summary", "")
                    _stats = getattr(self.chat_api, "_auto_compress_stats", {}) or {}
                    _prev = (_stats.get("previous_summary") or "").strip()
                    # Align disk archive cut with chat_api recent_start when possible.
                    keep_from_ms = None
                    first_role = _stats.get("first_kept_role") or ""
                    first_content = (_stats.get("first_kept_content") or "").strip()
                    if first_role and first_content:
                        for m in _get_session_manager().get_messages() or []:
                            if (m.get("role") or "") != first_role:
                                continue
                            mc = str(m.get("content") or "").strip()
                            if not mc:
                                continue
                            if mc[:240] == first_content or first_content in mc or mc[:120] in first_content:
                                keep_from_ms = _get_session_manager()._item_timestamp_ms(m)
                                if keep_from_ms == float("inf"):
                                    keep_from_ms = None
                                break
                    from opensquad.system_config import syscfg as _syscfg

                    _get_session_manager().compress_current_session(
                        keep_ratio=_syscfg.ctx_keep_recent_fraction(),
                        previous_summary=_prev,
                        external_summary=_summary or "",
                        keep_from_timestamp_ms=keep_from_ms,
                    )
                    if _summary:
                        self.chat_api._latest_summary = _summary
                        _summary_evt = {
                            "event": "context_summary_generated",
                            "text": "Context auto-compacted",
                            "summary": _summary,
                        }
                        _get_session_manager().add_event(
                            "info", _summary_evt, turn_id=self._current_turn, round_id=self._current_round
                        )
                        _get_session_manager().add_message("system", _summary, msg_type="context_summary")
                        # Persist summary to session_data so _load_history() can restore it
                        _get_session_manager().session_data["latest_summary"] = _summary
                        await self._emit("info", _summary_evt)
                        # Archive into self_learn corpus (append-only learning material)
                        try:
                            from plugins.self_learn.archive import archive_compression_summary

                            _sm = _get_session_manager()
                            _title = ""
                            try:
                                _title = str((_sm.session_data or {}).get("title") or "")
                            except Exception:
                                pass
                            archive_compression_summary(
                                _summary,
                                session_id=_sm.get_current_session_id() or "",
                                session_title=_title,
                                source="auto_compress",
                                agent_dir=getattr(self, "_agent_dir", None) or None,
                                agent_id=getattr(self, "_agent_id", "") or "",
                            )
                        except Exception:
                            logger.debug("[Runner] self_learn archive skipped", exc_info=True)
                        # FIX 2: Also emit summary_stream so the frontend can display the summary in the workflow panel.
                        # Auto-compression has no per-chunk streaming, so emit the full summary in one delta with done=true.
                        _ss_id = f"auto_compress_{_get_session_manager().get_current_session_id()}"
                        await self._emit(
                            "summary_stream",
                            {
                                "id": _ss_id,
                                "delta": _summary,
                                "text": _summary,
                                "done": True,
                                "trace_id": "auto",
                            },
                        )

                    # Force prompt refresh so CONTEXT_SUMMARY replacement takes effect immediately
                    self._has_prompt_snapshot = False
                    await self._setup_prompt()

                    # History sync
                    sid = _get_session_manager().get_current_session_id()
                    history_data = {
                        "messages": _get_session_manager().get_messages(),
                        "events": _get_session_manager().get_events(),
                        "session_id": sid,
                        "is_working_session": True,
                    }
                    # Tag reason so the frontend can merge archive into the live
                    # timeline instead of fully replacing it (avoids tool-stream
                    # duplication and message reordering mid-turn).
                    history_data["reason"] = "compression"
                    await bus.emit_async("history_sync", history_data)
                    await bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                    await bus.emit_async("session_list", _get_session_manager().get_session_list())

                    # Do NOT emit turn_elapsed here. Auto-compression often fires
                    # mid tool-loop; closing the workflow timer would force the
                    # next tool_call into a new workflow block and make the UI
                    # look like the tool stream was duplicated.

                    # Broadcast updated token stats after compression so frontend
                    # reflects the reduced context size immediately.
                    await self._broadcast_token_stats()

                    # Reset flag so it doesn't fire again next turn
                    self.chat_api._auto_compressed = False

                # Handle dict response (new format with text and tool_data)
                if isinstance(ai_response, dict):
                    response_text = ai_response.get("text", "")
                    tool_data_from_api = ai_response.get("tool_data")
                    output_media = ai_response.get("output_media", [])
                    finish_reason = ai_response.get("finish_reason")
                    stream_error = ai_response.get("stream_error", False)
                    logger.info(f"[Runner] Received dict response with tool_data: {tool_data_from_api is not None}")
                    if tool_data_from_api:
                        logger.info(
                            f"[Runner] Tool data extracted: {len(tool_data_from_api)} tool(s): {[t[0] for t in tool_data_from_api]}"
                        )
                else:
                    # Fallback for old format (plain string)
                    response_text = ai_response
                    tool_data_from_api = None
                    output_media = []
                    finish_reason = None
                    stream_error = False
                    logger.warning(
                        f"[Runner] Received non-dict response (type={type(ai_response).__name__}), using fallback"
                    )

                logger.debug(
                    f"[Runner] ===== LLM RESPONDED, response_len={len(response_text) if response_text else 0} ====="
                )

                # --- Plugin Hook: on_after_llm ---
                if self._plugin_manager:
                    _hook_ctx = await self._plugin_manager.run_hook(
                        "on_after_llm",
                        {
                            "response": response_text,
                            "agent_id": self._agent_id,
                        },
                    )
                    response_text = _hook_ctx.get("response", response_text)

                # ========== Safety interrupt checkpoint 3: After API response ==========
                if input_hub.is_stop_requested():
                    logger.info("[Runner] Stop requested after API response")
                    input_hub.clear_stop_request()
                    # Save partially streamed content to session to ensure it's not lost on refresh.
                    # _streamed_user_text is accumulated by stream_parser in real time for to_user content;
                    # it is the most reliable source of user-visible text.
                    _partial = "".join(getattr(self, "_streamed_user_text", []))
                    if _partial.strip():
                        _get_session_manager().add_message("assistant", _partial.strip())
                        logger.info(f"[Runner] Saved partial response on stop ({len(_partial)} chars)")
                    task_finished = True
                    initial_query = None
                    await self._emit("status", "Task stopped")
                    break

                # Parse response (state changes, tool calls, history saving, etc. are all handled here)
                stop, next_input, went_to_sleep = await self._handle_turn_result(
                    response_text,
                    tool_data_from_api,
                    output_media=output_media,
                    finish_reason=finish_reason,
                    stream_error=stream_error,
                )
                logger.info(
                    f"[Runner] _handle_turn_result => stop={stop}, next_input_len={len(next_input) if next_input else 0}, went_to_sleep={went_to_sleep}"
                )

                if went_to_sleep:
                    # KEY CHANGE: LLM has called system.wait or finished replying.
                    # In the 'never stop' architecture, the LLM stays in the inner loop
                    # waiting for new events (web messages, group chat, DM, timer, etc.).
                    # Don't exit the loop - instead, wait for pipeline events.
                    _get_session_manager().add_event(
                        "info",
                        {"text": "Agent entering wait mode - listening for events"},
                        turn_id=self._current_turn,
                        round_id=self._current_round,
                    )
                    await self._emit("status", "Waiting for events...")

                    # Wait for pipeline events with periodic polling
                    _wait_poll_interval = 1.0  # Check every 1 second
                    _max_wait_turns = 0  # 0 = wait indefinitely
                    _wait_turn_count = 0

                    while _max_wait_turns == 0 or _wait_turn_count < _max_wait_turns:
                        _wait_turn_count += 1

                        # Check for stop command
                        if input_hub.is_stop_requested():
                            logger.info("[Runner] Stop requested while waiting")
                            input_hub.clear_stop_request()
                            task_finished = True
                            initial_query = None
                            await self._emit("status", "Task stopped")
                            break

                        # Check for urgent commands
                        urgent_commands = input_hub.check_urgent_commands()
                        if urgent_commands:
                            for cmd in urgent_commands:
                                content = cmd.get("content", "")
                                if content == "__STOP__":
                                    logger.info("[Runner] Stop command received while waiting")
                                    input_hub.clear_stop_request()
                                    task_finished = True
                                    initial_query = None
                                    await self._emit("status", "Task stopped by user")
                                    break
                                elif content == "__NEW_SESSION__":
                                    logger.info("[Runner] New session requested while waiting")
                                    self._reset_session_stats()
                                    _get_session_manager().start_new_session()
                                    self._turn_sid = _get_session_manager().get_current_session_id()
                                    self._load_history()
                                    await self._emit("turn_start", 0)
                                    await bus.emit_async("session_list", _get_session_manager().get_session_list())
                                    await bus.emit_async(
                                        "current_session",
                                        {
                                            "id": _get_session_manager().get_current_session_id(),
                                            "title": "Current Session",
                                        },
                                    )
                                    await self._broadcast_token_stats()
                                    await self._emit("info", "New session started")
                                    task_finished = True
                                    initial_query = None
                                    break
                                elif content == "__COMPRESS_CONTEXT__":
                                    # Urgent compress context - outer loop handler processes it
                                    logger.info("[Runner] Urgent: Compress context command received while waiting")
                                    initial_query = "__COMPRESS_CONTEXT__"
                                    task_finished = True
                                    break
                                elif content.startswith("__LOAD_SESSION__:"):
                                    sid = content.split(":", 1)[1]
                                    logger.info(f"[Runner] Load session {sid} while waiting")
                                    if _get_session_manager().load_history_session(sid):
                                        self._turn_sid = sid
                                        self._load_history()
                                        await self._emit("turn_start", 0)
                                        history_data = {
                                            "messages": _get_session_manager().get_messages(),
                                            "events": _get_session_manager().get_events(),
                                            "session_id": sid,
                                            "is_working_session": True,
                                        }
                                        await bus.emit_async("history_sync", history_data)
                                        await bus.emit_async("current_session", {"id": sid, "title": "Current Session"})
                                        await bus.emit_async("session_list", _get_session_manager().get_session_list())
                                        await self._emit("info", f"Session loaded: {sid}")
                                    task_finished = True
                                    initial_query = None
                                    break
                                elif content.startswith("__SWITCH_AND_REPLY__:"):
                                    parts = content.split(":", 2)
                                    if len(parts) >= 3:
                                        sid, reply_content = parts[1], (parts[2] or "").strip()
                                        cmd_images = cmd.get("images", [])
                                        if cmd_images:
                                            self._current_images = cmd_images
                                        cmd_attachments = cmd.get("attachments", [])
                                        if cmd_attachments:
                                            self._current_attachments = cmd_attachments
                                        current_sid = _get_session_manager().get_current_session_id()
                                        if sid != current_sid:
                                            logger.info(f"[Runner] Switch context: {current_sid} -> {sid}")
                                            if _get_session_manager().load_history_session(sid):
                                                self._turn_sid = sid
                                                self._load_history()
                                                if reply_content:
                                                    await self._emit("turn_start", 0)
                                                await bus.emit_async(
                                                    "current_session", {"id": sid, "title": "Current Session"}
                                                )
                                                await bus.emit_async(
                                                    "session_list", _get_session_manager().get_session_list()
                                                )
                                        else:
                                            logger.info(f"[Runner] Same session {sid}, skip context switch")
                                        initial_query = reply_content or None
                                        task_finished = True
                                        break
                            if task_finished:
                                break

                        # Check for user messages in input_hub
                        # CRITICAL: Determine whether the LLM is in "working mode" (waiting for
                        # tool results) or "idle mode" (just replied with plain text).
                        # - Working mode: LLM called tools → pipeline events merge into tool results
                        # - Idle mode: LLM replied as assistant text → new message should be role=user
                        _last_was_tool_call = False
                        if self.chat_api.req:
                            _last = self.chat_api.req[-1]
                            if (_last.get("role") == "assistant" and _last.get("tool_calls")) or _last.get(
                                "role"
                            ) == "tool":
                                _last_was_tool_call = True

                        supplements = input_hub.get_all_pending()
                        if supplements:
                            # Drop synthetic wake sentinel; keep real user payloads.
                            supplements = [
                                item
                                for item in supplements
                                if (item.get("content") or "").strip() != "[wakeup-urgent-command]"
                            ]
                        if supplements:
                            for item in supplements:
                                content = item.get("content", "")
                                logger.info(
                                    f"[Runner] User message detected during wait (source={item.get('source', 'web')}, working_mode={_last_was_tool_call}): {content[:80]}"
                                )
                                _imgs = item.get("images") or []
                                if _imgs:
                                    self._current_images.extend(_imgs)
                                _atts = item.get("attachments") or []
                                if _atts:
                                    self._current_attachments = list(self._current_attachments or []) + list(_atts)

                            await self._emit("status", "working")

                            # Setup for next turn
                            await self._setup_prompt()

                            if _last_was_tool_call:
                                # LLM is in working mode: pipeline events are sent separately
                                # via add_pipeline_events during tool execution. Supplements here
                                # are from input_hub (gateway messages) and need to be persisted
                                # for session_manager/frontend, but NOT re-injected via add_pipeline_events
                                # (the event_pipeline push from input_hub was removed).
                                for item in supplements:
                                    content = item.get("content", "")
                                    if content and content.strip():
                                        _get_session_manager().add_message("user", content)
                                        await self._emit("user_msg", content)
                                        logger.info(
                                            f"[Runner] Persisted supplement as user message (working mode): {content[:80]}"
                                        )
                                # Also feed text into the next LLM turn via current_input so
                                # the model does not only see a bare wake/tool marker.
                                _parts = [item.get("content", "") for item in supplements if item.get("content")]
                                current_input = "\n---\n".join(_parts) if _parts else ""
                            else:
                                # LLM replied with plain text (not working): new message should be
                                # injected as role=user so the conversation can continue naturally.
                                # DO NOT use add_pipeline_events — that would inject role=tool which
                                # requires a preceding assistant with tool_calls.
                                #
                                # CRITICAL: Must also persist to session_manager so the message
                                # survives a page refresh. Previously this was missing — the message
                                # existed only in chat_api.req (LLM context) and disappeared on reload.
                                for item in supplements:
                                    content = item.get("content", "")
                                    if content and content.strip():
                                        self.chat_api.add_user_message(content)
                                        # Persist to session history (survives page refresh)
                                        _get_session_manager().add_message("user", content)
                                        await self._emit("user_msg", content)
                                        logger.info(
                                            f"[Runner] Woke up from wait (idle mode), added message as role=user. req_len={len(self.chat_api.req)}"
                                        )
                                current_input = ""

                            # Reset counters for next LLM call
                            self._inner_loop_count = 1
                            self._turn_start_time = time.perf_counter()

                            break  # Break out of wait loop, continue with LLM call

                        # Pipeline events: break and process via add_pipeline_events.
                        # Do NOT drain here — events must remain in pipeline until
                        # the tool execution path drains them.
                        from opensquad.event_pipeline import event_pipeline

                        if event_pipeline.size > 0:
                            logger.debug(f"[Runner] Pipeline events pending during wait: {event_pipeline.size}")
                            # New message arrived via event_pipeline from message_queue
                            # (group/DM messages), not through input_hub queue.
                            # Break the wait loop and process it.
                            await self._emit("status", "working")

                            # Setup for next turn
                            await self._setup_prompt()

                            # Drain event_pipeline and send as role=tool
                            _raw_events = event_pipeline.drain_sync()
                            if _raw_events:
                                # CRITICAL: Persist user-originated events to session_manager
                                # so they survive a page refresh. Previously this was missing —
                                # events existed only in chat_api.req (LLM context) and vanished on reload.
                                for evt in _raw_events:
                                    if (
                                        evt.source in ("web", "gateway", "group", "dm")
                                        and evt.content
                                        and evt.content.strip()
                                    ):
                                        _get_session_manager().add_message("user", evt.content)
                                        await self._emit("user_msg", evt.content)
                                        logger.info(
                                            f"[Runner] Persisted event_pipeline event as user message (source={evt.source}): {evt.content[:80]}"
                                        )

                                    # Format for LLM
                                    lines = ["", "--- External Events (arrived during processing) ---"]
                                    for evt in _raw_events:
                                        lines.append(evt.format_for_llm())
                                    lines.append("--- End External Events ---")
                                    _pipeline_events = "\n".join(lines)
                                    if hasattr(self.chat_api, "add_pipeline_events"):
                                        self.chat_api.add_pipeline_events(_pipeline_events)
                                    else:
                                        logger.warning(
                                            f"[Runner] chat_api has no add_pipeline_events; pipeline events ({len(_pipeline_events)} chars) dropped"
                                        )
                                    logger.info(
                                        f"[Runner] Woke up from wait via event_pipeline, drained {len(_raw_events)} events ({len(_pipeline_events)} chars)"
                                    )

                            # Reset counters for next LLM call
                            self._inner_loop_count = 1
                            self._turn_start_time = time.perf_counter()

                            current_input = ""
                            break  # Break out of wait loop, continue with LLM call

                        # Message queue: push to event_pipeline, don't break
                        pending_msgs = message_queue.get_all()
                        if pending_msgs:
                            for msg in pending_msgs:
                                if msg.type == "group":
                                    msg_text = f"[{msg.source_name} | group_id={msg.source_id}] {msg.sender_name}: {msg.content}"
                                    if getattr(msg, "source_id", None):
                                        self._current_group_id = str(msg.source_id)
                                        self._current_channel = "chatpro_group"
                                elif msg.type == "dm":
                                    msg_text = f"[DM] {msg.sender_name}: {msg.content}"
                                else:
                                    msg_text = f"[{msg.type}] {msg.sender_name}: {msg.content}"
                                evt_source = msg.type
                                event_pipeline.push_nowait(
                                    evt_source,
                                    msg_text,
                                    {
                                        "group_name": msg.source_name if msg.type == "group" else "",
                                        "sender_name": msg.sender_name,
                                    },
                                )
                            logger.debug(
                                f"[Runner] Message queue pushed to pipeline: {len(pending_msgs)} messages (flow through role=tool)"
                            )

                        # Sleep before next poll
                        await asyncio.sleep(_wait_poll_interval)

                    if task_finished:
                        break

                    # If we exited wait loop WITHOUT task_finished, we have new user input.
                    # CRITICAL FIX: Instead of 'continue' (which goes back to outer while loop
                    # and just enters idle wait), we need to call the LLM with the updated
                    # chat_api.req (pipeline events already added as role=tool).
                    # Fall through to _setup_prompt + chat() by continuing the turn loop.
                    # Set current_input to empty since events are already in role=tool.
                    # The next LLM call will see the role=tool message and respond appropriately.
                    logger.info("[Runner] Exiting wait loop with new input, calling LLM with updated context")
                    # Skip the normal _setup_prompt + chat() flow — instead, go directly
                    # to the chat call by setting up the same state that would be there
                    # at the start of a normal turn. We do this by jumping to the chat
                    # section via a controlled flow: increment turn counter and continue.
                    self._current_turn += 1
                    await self._emit(
                        "turn_start", {"turn": self._current_turn, "started_ms": int(self._workflow_started_ms)}
                    )
                    continue  # Goes back to for turn loop → _setup_prompt → chat()

                await self._setup_prompt()

                if stop:
                    await (
                        self._broadcast_token_stats()
                    )  # Write cumulative stats immediately after conversation completes
                    # Workflow ended (normal completion): send turn_elapsed to ensure frontend closes the workflow timer block
                    _wf_ended_ms = int(datetime.now().timestamp() * 1000)
                    await self._emit(
                        "turn_elapsed", {"started_ms": int(self._workflow_started_ms), "ended_ms": _wf_ended_ms}
                    )
                    await self._emit("status", "Response complete")
                    # Notify frontend that agent is idle so send button is restored
                    await _get_state_manager().set_state("idle")
                    await self._emit("state", "idle")
                    task_finished = True
                    initial_query = None  # Message has been processed, clear to prevent repetition
                    break

                await self._broadcast_token_stats()
                current_input = next_input

            # Continue loop to wait for next input
            if task_finished:
                logger.debug(
                    f"[Runner] ===== TASK FINISHED, looping back to wait for next input (initial_query={'set' if initial_query else 'None'}) ====="
                )
                self._current_images = []  # Clear images to avoid repeating in next round
                self._current_attachments = []  # Clear attachments to avoid repeating in next round
                # Notify frontend that agent is idle so send button is restored.
                # This covers the common path where _handle_turn_result returns went_to_sleep=True
                # Also emit turn_elapsed for the went_to_sleep path (safety fallback)
                _wf_ended_ms = int(datetime.now().timestamp() * 1000)
                await self._emit(
                    "turn_elapsed", {"started_ms": int(self._workflow_started_ms), "ended_ms": _wf_ended_ms}
                )
                # (response complete, agent sleeps) and never reaches the `if stop:` block above.
                await _get_state_manager().set_state("idle")
                await self._emit("state", "idle")
                await self._emit("status", f"Continuous mode - State: {await _get_state_manager().get_state()}")
                # Note: when __SWITCH_AND_REPLY__ urgent handling sets initial_query = reply_content,
                # do NOT clear it, otherwise the user message will be lost
                if not initial_query:
                    initial_query = None

    def _generate_wake_prompt(self) -> str:
        """Generate wake-up prompt"""
        wake_info = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reason": sleep_controller._wake_reason or "Sleep duration ended",
            "planned": sleep_controller._planned_duration,
            "actual": round((datetime.now() - sleep_controller._start_time).total_seconds(), 1)
            if sleep_controller._start_time
            else 0,
        }
        sleep_controller._wake_reason = None

        if wake_info["reason"] == "Sleep duration ended":
            return f"[Wake-{wake_info['time']}-Sleep duration ended]"
        else:
            return f"[Wake-{wake_info['time']}-{wake_info['reason']}]"

    @staticmethod
    def _filter_native_tokens(text: str) -> str:
        """Filter leaked native tool call text from various models:
        1. <|...|> format (Qwen3/DeepSeek, etc.)
        2. functions.<name>:<id>{...} format (Kimi/Moonshot, etc.)
        """
        if not text:
            return text

        # --- Format 1: <|...|> format (Qwen3/DeepSeek) ---
        if "<|" in text:
            # First remove the entire tool_calls_section block
            text = re.sub(r"<\|tool_calls_section_begin\|>.*?<\|tool_calls_section_end\|>", "", text, flags=re.DOTALL)
            # Fallback: remove all remaining <|...|> tokens
            text = re.sub(r"<\|[^|>]*\|>", "", text)

        # --- Format 2: functions.<name>:<id>{...} format (Kimi/Moonshot) ---
        # Match functions.tool_name:index{...}, supporting at most one level of nested JSON
        if "functions." in text:
            text = re.sub(r"\bfunctions\.[a-zA-Z0-9_]+:\d+\{(?:[^{}]|\{[^{}]*\})*\}", "", text, flags=re.DOTALL)

        return text

    def _remove_all_tags(self, text: str) -> str:
        """Minimally and thoroughly remove all XML/HTML format tags and their content, preserving Markdown formatting"""
        if not text:
            return ""

        import re

        result = text

        # 0a. Filter native tool call tokens (<|...|> format)
        result = self._filter_native_tokens(result)

        # 0. Special handling: remove possibly missing-'<' tool_call markers
        result = re.sub(r'tool_call\s+name="[^"]+"\s*>', "", result, flags=re.IGNORECASE)

        # 1. Thoroughly remove these blocks and their content
        silent_blocks = [
            "thought",
            "plan",
            "think",
            "tool_call",
            "tool_result",
            "to_system",
            "state",
            "wake",
            "sleep",
            "title",
            "option",
            "arguments",
        ]
        for tag in silent_blocks:
            result = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", "", result, flags=re.DOTALL | re.IGNORECASE)
            result = re.sub(rf"<{tag}\b[^>]*/>", "", result, flags=re.IGNORECASE)

        # 2. Special handling for to_user tag: keep its content
        result = re.sub(r"<to_user\b[^>]*>(.*?)</to_user>", r"\1", result, flags=re.DOTALL | re.IGNORECASE)

        # 3. Remove remaining tag names but keep content (if any)
        result = re.sub(r"<[^>]+>", "", result)

        # 4. Thoroughly clean up remaining orphaned closing tags (e.g. </thought>) and stray brackets
        result = re.sub(r"</[a-zA-Z0-9_]+>", "", result)
        result = re.sub(r"^\s*[<>]\s*$", "", result, flags=re.MULTILINE)  # Remove lines containing only < or >

        # 5. Clean up extra blank lines while preserving necessary single and double line breaks for Markdown
        result = re.sub(r"\n{4,}", "\n\n\n", result)

        return result.strip()

    def _extract_text_before_tool(self, text: str) -> str | None:
        """Extract text content before a tool call marker"""
        if not text:
            return None

        # Find the position of the first tool_call tag
        tool_match = re.search(r"<tool_call", text, re.IGNORECASE)
        if not tool_match:
            return None

        # Extract text before tool_call
        text_before = text[: tool_match.start()]

        # Clean up text: remove other XML tags but keep plain text
        text_before = re.sub(r"<(?!tool_call)[^>]+>", "", text_before)
        text_before = text_before.strip()

        # If there is meaningful content after cleaning, return it
        if text_before and len(text_before) > 3:  # At least 3 chars to be meaningful
            return text_before

        return None

    async def _handle_turn_result(
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
                "thought", {"text": thought_text}, turn_id=self._current_turn, round_id=self._current_round
            )

        # Task plan
        plan_text = ResponseParser.extract_tag(full_response, "plan")
        if plan_text:
            plan_id = f"plan_{datetime.now().strftime('%M%S')}"
            await self._emit("plan", {"id": plan_id, "text": plan_text})
            _get_session_manager().add_event(
                "plan", {"id": plan_id, "text": plan_text}, turn_id=self._current_turn, round_id=self._current_round
            )
            # Write back to TaskManager so the next turn's {{TASK_STATE}} includes the AI's own plan
            self.task_manager.update(plan_text)

        # Option buttons
        import re

        option_matches = re.findall(r"<option>(.*?)</option>", full_response, re.DOTALL)
        for option_text in option_matches:
            await self._emit("option", option_text.strip())
            _get_session_manager().add_event(
                "option", {"text": option_text.strip()}, turn_id=self._current_turn, round_id=self._current_round
            )

        # --- 2. State and sleep tags ---
        new_state = self._extract_tag(full_response, "state")
        new_wake = self._extract_tag(full_response, "wake")
        sleep_seconds = self._extract_tag(full_response, "sleep")
        sys_cmd = self._extract_tag(full_response, "to_system")

        # --- 2.1 Task supervision tags ---
        task_start = self._extract_tag(full_response, "task_start")
        if task_start:
            self._in_task = True
            self._auto_continue_retries = 0
            task_name = task_start.strip()
            if task_name:
                _get_session_manager().set_title(task_name)
                await self._emit(
                    "current_session", {"id": _get_session_manager().get_current_session_id(), "title": task_name}
                )
                await bus.emit_async("session_list", _get_session_manager().get_session_list())
                await self._emit(
                    "session_title", {"id": _get_session_manager().get_current_session_id(), "title": task_name}
                )

        # Agent-chosen session subject via <title>...</title>
        title_tag = self._extract_tag(full_response, "title")
        if title_tag and title_tag.strip():
            title_name = title_tag.strip()
            _get_session_manager().set_title(title_name)
            sid = _get_session_manager().get_current_session_id()
            await self._emit("current_session", {"id": sid, "title": title_name})
            await bus.emit_async("session_list", _get_session_manager().get_session_list())
            await self._emit("session_title", {"id": sid, "title": title_name})

        if sys_cmd in ["task_complete", "task_failed"]:
            self._in_task = False
            self._awaiting_user_reply = False
            self._last_user_msg_from_to_user = False
            self._auto_continue_retries = 0

        # --- 3. Execute state update logic ---
        if new_state:
            logger.info(f"[Runner] Applying AI state change: {new_state}")
            await _get_state_manager().set_state(new_state)
            await self._emit("state", new_state)  # Explicitly emit state change event

            if new_state == "working" and not task_logger.has_active_task():
                task_req = self._last_user_input[:200]
                task_id = task_logger.start_task(task_req, "working")
                logger.info(f"[Runner] Task recording started: {task_id}")
                # --- Plugin Hook: on_task_start ---
                if self._plugin_manager:
                    await self._plugin_manager.run_hook(
                        "on_task_start",
                        {
                            "task_id": task_id,
                            "requirement": task_req,
                            "source": self._current_input_source,
                            "agent_id": self._agent_id,
                        },
                    )

        if new_wake:
            logger.info(f"[Runner] Applying wake mode change: {new_wake}")
            await _get_state_manager().set_wake_mode(new_wake)
            await self._emit("wake", new_wake)

        # --- 3. Handle sleep command ---
        if sleep_seconds and sleep_seconds.isdigit():
            seconds = int(sleep_seconds)
            logger.info(f"[Runner] AI entering sleep for {seconds}s")
            await self._emit("sleep", seconds)
            await _get_state_manager().set_state("sleeping")
            await self._emit("state", "sleeping")
            wake_info = await sleep_controller.sleep(seconds)
            await _get_state_manager().set_state("idle")
            await self._emit("state", "idle")
            logger.info(f"[Runner] Sleep ended: {wake_info.get('wake_type')}, reason: {wake_info.get('wake_reason')}")
            return False, "", True

        # --- 5. Text content persistence (regardless of whether tools are called) ---
        # Prefer streamed text accumulated by stream_parser during streaming.
        # stream_parser correctly identifies to_user content in real-time,
        # so its output is the authoritative source. This avoids the bug where
        # _remove_all_tags() would incorrectly strip tag names appearing as
        # explanatory text (e.g. AI explains "<to_user>" in a markdown table).
        streamed = "".join(getattr(self, "_streamed_user_text", []))
        user_msg_from_tag = None
        self._last_user_msg_from_to_user = False
        if streamed.strip():
            user_msg = streamed.strip()
            user_msg_from_tag = getattr(self, "_streamed_user_tag", None) or "to_user"
            # Prefer explicit end_task tag in the raw response over stream-tag race.
            end_only = self._extract_tag(full_response, "to_user_end_task")
            if end_only is not None:
                user_msg_from_tag = "to_user_end_task"
                cleaned_end = self._remove_all_tags(end_only).strip()
                if cleaned_end:
                    user_msg = cleaned_end
            self._last_user_msg_from_to_user = user_msg_from_tag == "to_user"
        else:
            # Fallback: extract from full_response (non-streaming API or
            # stream_parser not set up)
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
            clean_context = self._remove_tags(full_response, interfering_tags)

            # Priority: to_user_end_task > to_user_reply > to_user
            user_msg = self._extract_tag(clean_context, "to_user_end_task")
            if user_msg:
                user_msg_from_tag = "to_user_end_task"
            else:
                user_msg = self._extract_tag(clean_context, "to_user_reply")
                if user_msg:
                    user_msg_from_tag = "to_user_reply"
                else:
                    user_msg = self._extract_tag(clean_context, "to_user")
                    if user_msg:
                        user_msg_from_tag = "to_user"
                        self._last_user_msg_from_to_user = True
            if not user_msg:
                # If there is no explicit to_user*, treat the remainder as the reply
                user_msg = self._remove_all_tags(clean_context)
            else:
                user_msg = self._remove_all_tags(user_msg)

        if user_msg_from_tag == "to_user_reply":
            self._awaiting_user_reply = True

        # Guard: detect leaked tool call arguments (JSON or XML leaking as user-visible text).
        # This happens when the model outputs malformed or unclosed <tool_call> tags.
        # Report the error back to the model the same way a failed tool execution is reported,
        # so it appears in the WorkflowContainer and the model can self-correct.
        if self._is_leaked_tool_params(user_msg):
            preview = user_msg.strip()[:120]
            logger.warning(
                "[Runner] Detected leaked tool parameters in user_msg "
                "(len=%d, preview=%r) -- sending format error back to model",
                len(user_msg.strip()),
                preview,
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
            # Notify frontend (WorkflowContainer)
            await self._emit("tool_call", {"id": fe_call_id, "name": fe_name, "args": preview})
            await self._emit(
                "tool_result", {"id": fe_call_id, "name": fe_name, "args": preview, "result": f"Error: {fe_detail}"}
            )
            # Persist to session
            _get_session_manager().add_event(
                "tool_call",
                {"id": fe_call_id, "name": fe_name, "args": preview},
                turn_id=self._current_turn,
                round_id=self._current_round,
            )
            _get_session_manager().add_event(
                "tool_result",
                {"id": fe_call_id, "name": fe_name, "args": preview, "result": f"Error: {fe_detail}"},
                turn_id=self._current_turn,
                round_id=self._current_round,
            )
            # Feed back to model in same format as a tool execution result
            return False, self._summarize_result(fe_name, f"Error: {fe_detail}"), False

        # Guard: detect repetitive output (stuttering) from lower-quality models.
        # Only run if enable_repetition_check is True in model config
        is_repetitive = False
        if getattr(self.chat_api, "enable_repetition_check", False):
            # Check both user-visible message and internal thought process
            is_repetitive = self._is_repeated_content(user_msg) or self._is_repeated_content(thought_text)

        if is_repetitive:
            if self._repetition_rewind_count >= 1:
                logger.warning(
                    "[Runner] Repetition rewind already used once this turn, allowing it through to avoid infinite loop"
                )
            else:
                self._repetition_rewind_count += 1
                logger.warning(
                    "[Runner] Detected repetitive output (stuttering) -- performing context rewind and requesting re-output"
                )

                # CRITICAL: Break the loop by removing the repetitive message from model history
                # This 'Context Rewind' prevents the model from being biased by its own recent mistake.
                if hasattr(self.chat_api, "pop_last_assistant_message"):
                    self.chat_api.pop_last_assistant_message()

                re_name = "repetition_error"
                re_detail = (
                    "Detected repetitive output (stuttering / loop). "
                    "CRITICAL: System has removed your last repetitive message from history. "
                    "Do NOT repeat your previous phrases. Do NOT explain why you looped. "
                    "Immediately take a DIFFERENT approach or skip directly to the tool call."
                )
                # Notify frontend
                hint = "检测到模型输出内容重复（复读机行为），系统已自动回退上下文并要求模型修正。"
                await self._emit("info", hint)
                await self._emit("status", "Repetition loop detected, rewinding and retrying...")

                _get_session_manager().add_event(
                    "info", {"text": hint}, turn_id=self._current_turn, round_id=self._current_round
                )

                return False, self._summarize_result(re_name, f"Error: {re_detail}"), False

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
            if self._plugin_manager:
                _hook_ctx = await self._plugin_manager.run_hook(
                    "on_before_send",
                    {
                        "message": _send_msg,
                        "agent_id": self._agent_id,
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
                await self._emit(event_type, _send_msg)
                if output_media:
                    await self._emit("output_media", output_media)
                _saved_msg = _send_msg
                _saved_output_media = output_media
                if user_msg_from_tag == "to_user_end_task":
                    _get_session_manager().mark_last_assistant_end_task()
                # --- Plugin Hook: on_after_send ---
                if self._plugin_manager:
                    await self._plugin_manager.run_hook(
                        "on_after_send",
                        {
                            "message": _send_msg,
                            "agent_id": self._agent_id,
                        },
                    )

        # --- 6. Tool call logic (placed after text is saved) ---
        tc_log = get_tool_call_debug_logger()
        tc_log.debug("[runner] full_response len=%d, first 500 chars: %s", len(full_response), full_response[:500])

        # tool_data_from_api is now List[Tuple] for parallel tool call support
        if tool_data_from_api:
            tool_calls = tool_data_from_api  # List[(name, args_dict)]
            tc_log.info("[runner] [OK] Using tool_data from Native FC strategy: %d tool(s)", len(tool_calls))
        else:
            # Fallback to XML parsing — supports parallel tool calls
            tool_calls = ResponseParser.parse_tool_calls(full_response)
            if tool_calls:
                tc_log.info("[runner] [OK] Using tool_data from XML parser: %d tool(s)", len(tool_calls))
            else:
                tool_calls = []

        if tool_calls:
            tc_log.info("[runner] [tool] Executing %d parallel tool call(s)", len(tool_calls))

            # Phase 1: Execute ALL tools and collect results (no add_tool_result yet)
            _tool_results = []  # List of dicts with tool metadata for batch commit
            _control_flow_return = None  # If a control tool requests immediate return

            for call_index, (t_name, t_args_dict) in enumerate(tool_calls):
                tc_log.info("[runner] [tool] #%d: name=%r, args=%r", call_index, t_name, t_args_dict)
                call_id = f"call_{datetime.now().strftime('%M%S')}_{t_name}_{call_index}"
                _sanitized = {k: ("..." if v is ... else v) for k, v in t_args_dict.items()} if t_args_dict else {}
                t_args_json = json.dumps(_sanitized, ensure_ascii=False, indent=2) if _sanitized else "{}"

                tc_log.info("[runner] [emit] Emitting tool_call event: id=%s, name=%s", call_id, t_name)
                # Skip internal synthetic event_pipeline — hidden from frontend & session
                if t_name in ("system__event_pipeline", "system.event_pipeline"):
                    tc_log.debug("[runner] [emit] Skipping system__event_pipeline from frontend & session")
                else:
                    await self._emit("tool_call", {"id": call_id, "name": t_name, "args": t_args_json})
                    _get_session_manager().add_event(
                        "tool_call",
                        {"id": call_id, "name": t_name, "args": t_args_json},
                        turn_id=self._current_turn,
                        round_id=self._current_round,
                    )

                # --- Plugin Hook: on_before_tool ---
                _skip_tool = False
                if self._plugin_manager:
                    _hook_ctx = await self._plugin_manager.run_hook(
                        "on_before_tool",
                        {
                            "tool_name": t_name,
                            "arguments": t_args_dict,
                            "agent_id": self._agent_id,
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
                        sid=getattr(self, "_turn_sid", "") or "",
                        call_id=call_id,
                        tool_name=t_name,
                    )
                    try:
                        result = await self.tool_registry.call(t_name, t_args_dict)
                    finally:
                        reset_tool_call_context(_ctx_token)
                    task_supervisor.report_activity()

                # Collaboration board auto-sync
                try:
                    import os as _os

                    from opensquad.collab_board import update_latest_tool as _cb_update_latest_tool

                    _agent_dir = getattr(self, "_agent_dir", "") or ""
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
                if self._plugin_manager:
                    _hook_ctx = await self._plugin_manager.run_hook(
                        "on_after_tool",
                        {
                            "tool_name": t_name,
                            "arguments": t_args_dict,
                            "result": result,
                            "agent_id": self._agent_id,
                            "model": getattr(self.chat_api, "model", ""),
                        },
                    )
                    result = _hook_ctx.get("result", result)

                # TTS / media tools may return __output_media__ for chat bubble playback
                if isinstance(result, dict) and result.get("__output_media__"):
                    try:
                        await self._emit("output_media", result["__output_media__"])
                        tc_log.info(
                            "[runner] Emitted output_media from tool %r: %d item(s)",
                            t_name,
                            len(result["__output_media__"]),
                        )
                    except Exception as _om_err:
                        tc_log.warning("[runner] Failed to emit tool output_media: %s", _om_err)

                # --- Plugin Hook: on_tool_error ---
                if self._plugin_manager and isinstance(result, str) and result.startswith("Error:"):
                    _hook_ctx = await self._plugin_manager.run_hook(
                        "on_tool_error",
                        {
                            "tool_name": t_name,
                            "arguments": t_args_dict,
                            "error": result,
                            "agent_id": self._agent_id,
                        },
                    )
                    result = _hook_ctx.get("error", result)

                # Drain event pipeline (per-tool, may contain events that arrived during execution)
                from opensquad.event_pipeline import event_pipeline

                _raw_events = event_pipeline.drain_sync()

                for evt in _raw_events:
                    if evt.source in ("web", "gateway", "group", "dm") and evt.content and evt.content.strip():
                        _get_session_manager().add_message("user", evt.content)
                        await self._emit("user_msg", evt.content)
                    if evt.source == "vision_tool" and evt.metadata.get("action") == "inject_images":
                        img_paths = evt.metadata.get("image_paths", [])
                        if img_paths:
                            self._current_images.extend(img_paths)
                            already = set(self._current_images)
                            new_img_paths = [p for p in img_paths if p not in already]
                            if new_img_paths:
                                self._current_images.extend(new_img_paths)
                            try:
                                _ipf = (
                                    os.path.join(self._agent_dir, "img_path.txt") if self._agent_dir else "img_path.txt"
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
                    for _k in ("diff_old", "diff_new", "diff_start_line"):
                        if _k in result and result[_k] is not None:
                            _ui_extras[_k] = result[_k]
                    _display = result.get("message")
                    if isinstance(_display, str) and _display.strip():
                        _tool_result_text = _display
                    else:
                        _tool_result_text = str(result) if result else "(empty result)"
                else:
                    _tool_result_text = str(result) if result else "(empty result)"

                # Apply limit_token override or config-based truncation
                if _limit_token is not None:
                    _max_len = _limit_token if _limit_token > 0 else None
                else:
                    _max_len = self._get_tool_output_max_chars()
                    _max_len = _max_len if _max_len > 0 else None
                _tool_result_text = self._truncate_result_text(_tool_result_text, _max_len)

                # --- system.wait: special control flow (immediate return) ---
                if t_name in ("system.wait", "wait", "system__wait"):
                    if isinstance(result, dict) and result.get("status") == "success":
                        _ckpt_dir = getattr(self, "_agent_dir", "") or ""
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
                                self._current_images.extend(_imgs)
                            _atts = _item.get("attachments") or []
                            if _atts:
                                self._current_attachments = list(self._current_attachments or []) + list(_atts)
                        if _user_parts:
                            wake_msg = (
                                wake_msg
                                + "\n\n[Messages received while sleeping — treat these as the user's real input; "
                                "do not dismiss them as wake notifications]\n" + "\n---\n".join(_user_parts)
                            )
                            logger.info(
                                f"[Runner] system.wait merged {len(_user_parts)} pending user msg(s) "
                                f"and {len(self._current_images)} image(s) into wake context"
                            )

                        _wait_result_text = wake_msg

                        self.chat_api.add_tool_result(
                            tool_name=t_name,
                            tool_args=t_args_dict,
                            result=_wait_result_text,
                            tool_call_id=call_id,
                        )
                        if _pipeline_events and hasattr(self.chat_api, "add_pipeline_events"):
                            self.chat_api.add_pipeline_events(_pipeline_events)

                        _get_session_manager().add_event(
                            "tool_result",
                            {"id": call_id, "name": t_name, "args": t_args_json, "result": _wait_result_text},
                            turn_id=self._current_turn,
                            round_id=self._current_round,
                        )
                        await self._emit(
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
                            await self._emit("state", actual_state)
                            if actual_state == "working" and not task_logger.has_active_task():
                                task_req = self._last_user_input[:200]
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
                    }
                )

            if _control_flow_return:
                logger.info(f"[Runner] [DIAG] _control_flow_return set: {_control_flow_return}")
                return _control_flow_return

            # Phase 2: Batch-commit ALL tool results to chat_api history (one batch)
            tc_log.info("[runner] [tool] Batch-committing %d tool result(s) to chat_api history", len(_tool_results))
            for entry in _tool_results:
                self.chat_api.add_tool_result(
                    tool_name=entry["name"],
                    tool_args=entry["args"],
                    result=entry["result_text"],
                    tool_call_id=entry["call_id"],
                )
                if entry["pipeline_events"] and hasattr(self.chat_api, "add_pipeline_events"):
                    self.chat_api.add_pipeline_events(entry["pipeline_events"])

                _get_session_manager().add_event(
                    "tool_result",
                    {
                        "id": entry["call_id"],
                        "name": entry["name"],
                        "args": entry["args_json"],
                        "result": entry["result_text"],
                        **(entry.get("ui_extras") or {}),
                    },
                    turn_id=self._current_turn,
                    round_id=self._current_round,
                )
                await self._emit(
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

            tc_log.info(
                "[runner] [tool] Batch commit complete: %d result(s), returning False,'',False", len(_tool_results)
            )
            if _saved_msg:
                # Update elapsed_ms on the assistant message that ChatAPI already saved
                _elapsed_ms = int(datetime.now().timestamp() * 1000) - int(self._workflow_started_ms)
                _get_session_manager().update_last_message_elapsed_ms(_elapsed_ms)
                logger.info(
                    "[Runner] Tool turn complete: saved_msg_len=%d, elapsed_ms=%d",
                    len(_saved_msg),
                    _elapsed_ms,
                )
            return False, "", False

        # Check for auto-continue (trailing colon indicating tool intent)
        # We check the original full_response (after tag removal) to see if it ends with a colon,
        # even if user_msg was filtered out.
        clean_full = self._remove_all_tags(full_response).strip()
        needs_tool = clean_full.endswith(":") or clean_full.endswith("：")

        if needs_tool and not tool_data_from_api:
            if finish_reason == "stop" and not stream_error:
                if (
                    self._max_auto_continue_retries is None
                    or self._auto_continue_retries < self._max_auto_continue_retries
                ):
                    self._auto_continue_retries += 1
                    auto_continue_prompt = (
                        "[System Prompt] You ended with a trailing colon, which usually means you intended to call a tool next. "
                        "Continue immediately by calling the appropriate tool."
                    )
                    logger.info(
                        "[Runner] Auto-continuing due to trailing colon (limit: %s/%s)",
                        self._auto_continue_retries,
                        self._max_auto_continue_retries,
                    )
                    return False, auto_continue_prompt, False
                logger.warning("[Runner] Max auto-continue retries reached")
            elif stream_error:
                logger.warning("[Runner] Stream interrupted; skip auto-continue for tool-intent")

        # The assistant message itself is now persisted by ChatAPI.add_assistant_message()
        # (chat_api.py:1717) during streaming — always, not only for reasoning_content.
        # Here we only need to update elapsed_ms on that message.
        if _saved_msg:
            _elapsed_ms = int(datetime.now().timestamp() * 1000) - int(self._workflow_started_ms)
            _get_session_manager().update_last_message_elapsed_ms(_elapsed_ms)
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
            if self._awaiting_user_reply:
                self._awaiting_user_reply = False

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
                if completed and self._plugin_manager:
                    await self._plugin_manager.run_hook(
                        "on_task_complete",
                        {
                            "task_id": completed.get("task_id", ""),
                            "completion_status": completed.get("completion_status", ""),
                            "tools_used": completed.get("tools_used", []),
                            "turns": completed.get("turns", 0),
                            "agent_id": self._agent_id,
                        },
                    )
                await _get_state_manager().set_state("idle")
                await self._emit("state", "idle")
                return True, "", False

        return False, "Error: No output produced", False

    def _remove_tags(self, text: str, tags: list) -> str:
        """Remove specified XML tags (supports tags with attributes, e.g. <tool_call name="...">)"""
        if not text:
            return ""
        result = text
        for tag in tags:
            # Support open tags with attributes, e.g. <tool_call name="filesystem.list_directory">
            pattern = rf"<{tag}\b[^>]*>.*?</{tag}>"
            result = re.sub(pattern, "", result, flags=re.DOTALL | re.IGNORECASE)
            # Self-closing tags
            pattern = rf"<{tag}\b[^>]*/>"
            result = re.sub(pattern, "", result, flags=re.IGNORECASE)
            # Fallback: if no closing tag (incomplete model output), truncate from open tag to end
            # This prevents JSON content from leaking when <tool_call name="..."> has no </tool_call>
            pattern = rf"<{tag}\b[^>]*>.*"
            result = re.sub(pattern, "", result, flags=re.DOTALL | re.IGNORECASE)
        return result.strip()

    def _is_leaked_tool_params(self, text: str) -> bool:
        """Detect leaked tool parameters (JSON or XML parameter tags).

        Detects two leak scenarios:
        1. JSON format leak: starts with { and ends with }, first key is an ASCII identifier
        2. XML parameter tag leak: tool parameter tags appear without an outer <tool_call>
        """
        s = text.strip()
        if not s:
            return False

        # Detect JSON leak (preserve original logic)
        if s.startswith("{") and re.search(r"\}\s*$", s):
            # Empty object {} -- no-argument tool call
            if s == "{}":
                return True
            # Check whether the first key of the JSON object is an ASCII identifier.
            # Tool call parameter keys are always ASCII (e.g. content, target_id, file_paths, etc.)
            if re.match(r'^\{\s*"[a-zA-Z_][a-zA-Z0-9_]*"\s*:', s):
                logger.warning("[Runner] Detected leaked JSON parameters without <tool_call> wrapper")
                return True

        # Detect XML parameter tag leak (new)
        # Whitelist of legitimate system tags (these are not tool parameter leaks)
        system_tags = {
            "title",
            "thought",
            "think",
            "plan",
            "to_user",
            "to_user_reply",
            "to_user_end_task",
            "to_system",
            "tool_call",
            "tool_result",
            "arguments",
            "state",
            "wake",
            "sleep",
            "option",
            "forward",
            "system_reminder",
            "func",
            "task_start",
            "task_complete",
            "task_failed",
        }

        # Extract all paired XML tags
        xml_tags = re.findall(r"<([a-zA-Z_][a-zA-Z0-9_]*)>.*?</\1>", s, re.DOTALL | re.IGNORECASE)

        # Check for non-system tags not inside a tool_call
        if xml_tags and "<tool_call" not in text:
            # Filter out system tags
            leaked_tags = [tag for tag in xml_tags if tag.lower() not in system_tags]
            if leaked_tags:
                logger.warning(
                    "[Runner] Detected leaked XML parameter tags without <tool_call> wrapper: %s", leaked_tags
                )
                return True

        return False

    def _is_repeated_content(self, text: str) -> bool:
        """Detect repetitive output (stuttering) from lower-quality models."""
        if not text or len(text) < 15:
            return False

        # Pattern 1: Adjacent string repetition (fuzzy with optional whitespace)
        import re

        # Check for 2+ repeats of patterns >= 6 chars (e.g. "现在执行测试：现在执行测试：")
        # \s* allows for variations in spacing
        match2 = re.search(r"(.{6,})\s*\1+", text, re.DOTALL)
        if match2:
            pattern = match2.group(1).strip()
            # Avoid matching simple repeated punctuation or empty space patterns
            if len(pattern) >= 6 and any(c.isalnum() for c in pattern):
                logger.warning(f"[Runner] Detected repetitive output (2x): {pattern[:50]}...")
                return True

        # Check for 3+ repeats of shorter patterns >= 4 chars (e.g. "启动... 启动... 启动...")
        match3 = re.search(r"(.{4,})\s*\1{2,}", text, re.DOTALL)
        if match3:
            pattern = match3.group(1).strip()
            if len(pattern) >= 4 and any(c.isalnum() for c in pattern):
                logger.warning(f"[Runner] Detected repetitive output (3x short): {pattern[:50]}...")
                return True

        # Pattern 2: High density of identical lines
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if len(lines) > 2:
            from collections import Counter

            counts = Counter(lines)
            most_common, count = counts.most_common(1)[0]
            # If a line repeats 2+ times AND makes up > 50% of the output (for very short responses)
            # or 3+ times AND makes up > 40%
            if (count >= 2 and count > len(lines) * 0.6) or (count >= 3 and count > len(lines) * 0.4):
                if len(most_common) > 4:
                    logger.warning(f"[Runner] Detected repetitive lines: {most_common[:50]}")
                    return True

        # Pattern 3: Cross-turn repetition (detecting if model repeats exactly what it said last turn)
        current_clean = text.strip()
        # Find the last assistant message in session history
        history = _get_session_manager().get_messages()
        last_asst = None
        for msg in reversed(history):
            if msg.get("role") == "assistant":
                last_asst = msg.get("content", "").strip()
                break

        if last_asst and current_clean == last_asst:
            logger.warning(f"[Runner] Detected exact cross-turn repetition: {current_clean[:50]}...")
            return True

        # Pattern 4: Meta-repetition (repeating "I am stuck in a loop" or similar apologies)
        loop_phrases = [
            "stuck in a repetition loop",
            "stuck in a loop",
            "apologize for the repetition",
            "breaking out of the loop",
        ]
        for phrase in loop_phrases:
            if phrase in current_clean.lower() and last_asst and phrase in last_asst.lower():
                logger.warning(f"[Runner] Detected meta-repetition (looping apologies): {phrase}")
                return True

        return False

    def _extract_tag(self, response: str, tag: str) -> str | None:
        """Robustly extract XML tag content, supporting tag attributes and extra whitespace, with debug logging."""
        search = response or ""
        # Avoid matching tag names mentioned inside reasoning (e.g. `<plan>` in <think>).
        if tag.lower() not in ("think", "thought"):
            search = ResponseParser.strip_reasoning_blocks(search)

        # Match <tag ...>content</tag>
        pattern = rf"<{re.escape(tag)}\b[^>]*>(.*?)</{re.escape(tag)}>"
        match = re.search(pattern, search, re.DOTALL | re.IGNORECASE)
        if match:
            val = match.group(1).strip()
            logger.info(f"[Extractor] Found tag <{tag}>: {val}")
            return val

        # Fallback 1: if no closing tag, try extracting up to the next < symbol
        pattern_fallback = rf"<{re.escape(tag)}\b[^>]*>(.*)"
        match_fb = re.search(pattern_fallback, search, re.IGNORECASE | re.DOTALL)
        if match_fb:
            val = match_fb.group(1).split("<")[0].strip()  # Extract up to next tag start
            logger.info(f"[Extractor] Found unclosed tag <{tag}>: {val}")
            return val

        # Fallback 2: support possibly missing < (for lazy AI output patterns)
        if tag in ["state", "wake", "sleep"]:
            pattern_lazy = rf"{re.escape(tag)}\s*>\s*(.*?)\s*</{re.escape(tag)}>"
            match_lazy = re.search(pattern_lazy, search, re.IGNORECASE)
            if match_lazy:
                val = match_lazy.group(1).strip()
                logger.info(f"[Extractor] Found lazy tag {tag}: {val}")
                return val

        return None

    @staticmethod
    def _truncate_result_text(text: str, max_len: int | None) -> str:
        """Truncate text to max_len chars, preserving head and tail portions.
        If max_len is None or <= 0, no truncation is applied."""
        if max_len is None or max_len <= 0 or len(text) <= max_len:
            return text
        if max_len >= 50000:
            return text[:25000] + "...[truncated]..." + text[-10000:]
        else:
            return text[:1000] + "...[truncated]..." + text[-500:]

    def _summarize_result(self, name: str, result: Any) -> str:
        now = datetime.now().strftime("%H:%M:%S")
        # MCP multimodal result (contains screenshots): keep only the text portion
        if isinstance(result, dict) and result.get("__mcp_multimodal__"):
            text = result.get("text", "")
            img_count = len(result.get("images", []))
            res_str = f"{text} [+{img_count} screenshot(s) attached]"
        else:
            res_str = str(result)

        # Skill-related reads should preserve content to avoid cutting SKILL.md
        tool_name = (name or "").lower()
        is_skill_read = "read_skill" in tool_name or "activate_skill" in tool_name
        is_skill_related = is_skill_read or "skill" in tool_name

        # read_skill/activate_skill: no truncation
        # other skill-related tools: allow much larger payload before truncation
        # normal tools: use tool_output_max_chars from agent config (default 50000)
        if is_skill_read:
            max_len = None
        elif is_skill_related:
            max_len = 50000
        else:
            max_len = self._get_tool_output_max_chars()
            max_len = max_len if max_len > 0 else None

        res_str = AgentRunner._truncate_result_text(res_str, max_len)
        return f"[{now}] Tool '{name}' executed. Result: {res_str}"

    def _get_tool_output_max_chars(self) -> int:
        """
        Read tool_output_max_chars from agent config.json.
        Returns 0 for no limit; defaults to 50000 chars if not configured.
        """
        try:
            if self._config_path and _os.path.isfile(self._config_path):
                with open(self._config_path, encoding="utf-8") as _f:
                    cfg = json.load(_f)
                val = cfg.get("model", {}).get("tool_output_max_chars")
                if val is not None:
                    v = int(val)
                    if v < 0:
                        return 0  # negative also means no limit
                    return v
        except Exception:
            pass
        return 50000

    def _prepare_task(self, query: str) -> tuple[str, str]:
        task_id, cleaned = extract_and_remove_first_tag(query[:30])
        if task_id:
            query = query.replace(f"<{task_id}>", "")
            self.chat_api.load_his = task_id

        # Long-term memory: advance turn count + evict expired memories
        if self._memory_manager:
            self._memory_manager.advance_turn()

        return query, self.chat_api.load_his or "continuous"

    async def _setup_prompt(self):
        """P1-1: Delegate prompt building to ContextBuilder.

        ContextBuilder handles all layers: tool call strategy, skills injection,
        MCP two-stage injection, standard context injection, custom hooks, and
        anti-repetition reminder. This method only wires the result back into
        the runner's state (tools, dynamic prefix, prompt snapshot).
        """
        final, dynamic_prefix, llm_params, is_changed = await self._context_builder.build(
            last_user_input=self._last_user_input,
            current_input_source=self._current_input_source,
            current_turn=self._current_turn,
            current_round=self._current_round,
        )

        # Store tools parameter for later use in chat() call
        self._current_tools = llm_params.get("tools")
        self._current_tool_choice = llm_params.get("tool_choice", "auto")
        self._dynamic_context_prefix = dynamic_prefix

        # --- Compute system_prompt diff (only when changed) ---
        diff_lines: list = []
        if is_changed:
            try:
                import difflib

                prev_prompt = self.chat_api.get_system_prompt()
                if prev_prompt:
                    old_lines = prev_prompt.splitlines(keepends=True)
                    new_lines = final.splitlines(keepends=True)
                    diff_lines = list(
                        difflib.unified_diff(
                            old_lines, new_lines, fromfile="old prompt", tofile="new prompt", lineterm=""
                        )
                    )
            except Exception:
                pass

        # --- Send/persist prompt snapshot only for first load or actual changes ---
        should_emit_prompt = (not self._context_builder.has_prompt_snapshot) or is_changed
        if should_emit_prompt:
            prompt_payload = {
                "system_prompt": final,
                "dynamic_prefix": self._dynamic_context_prefix or "",
                "changed": is_changed,
                "diff": diff_lines,
            }
            await self._emit("prompt_update", prompt_payload)
            _get_session_manager().add_event(
                "prompt_update",
                prompt_payload,
                turn_id=self._current_turn,
                round_id=self._current_round,
            )
            self._context_builder.mark_snapshot_emitted()

    def _setup_event_dispatch(self):
        if not self.chat_api.stream_parser:
            return

        sid = self._turn_sid
        # Reset streamed text accumulator for this turn.
        # stream_parser correctly identifies to_user content during streaming,
        # so we accumulate it here as the authoritative save source.
        # This avoids the bug where _remove_all_tags() would incorrectly strip
        # tag names that appear as explanatory text in AI responses (e.g. when
        # the AI explains runner.py architecture and mentions <to_user> as text).
        self._streamed_user_text = []
        self._streamed_user_tag = None

        def emit_with_sid(etype, data):
            # Inject session_id into event data
            bus.emit(etype, {"sid": sid, "data": data})

        def emit_user_stream(text):
            """Emit to_user_stream and accumulate for later persistence."""
            # Filter native tool call tokens to prevent <|...|> format tokens from leaking into user messages
            text = self._filter_native_tokens(text)
            if not text:
                return
            self._streamed_user_text.append(text)
            emit_with_sid("to_user_stream", text)

        def emit_to_user(text):
            self._streamed_user_tag = "to_user"
            emit_user_stream(text)

        def emit_to_user_reply(text):
            self._streamed_user_tag = "to_user_reply"
            emit_user_stream(text)

        def emit_to_user_end_task(text):
            self._streamed_user_tag = "to_user_end_task"
            emit_user_stream(text)

        self.chat_api.stream_parser._default_handler = emit_user_stream

        # Strict separation of streaming vs non-streaming tags.
        # Streaming tags: emitted as they are parsed.
        # Non-streaming tags: empty handler intercepts them to prevent them from flowing to to_user_stream as plain text.
        self.chat_api.stream_parser._handlers.update(
            {
                "thought": lambda x: emit_with_sid("thought", x),
                "think": lambda x: emit_with_sid("thought", x),
                "to_user": emit_to_user,
                "to_user_reply": emit_to_user_reply,
                "to_user_end_task": emit_to_user_end_task,
                # Intercept the following tags to prevent them from appearing in the content stream
                "title": lambda x: None,  # Intercept title tag (subject handled elsewhere)
                "plan": lambda x: None,
                "tool_call": lambda x: None,
                "arguments": lambda x: None,
                "func": lambda x: None,  # Intercept func tag (new tool call format)
                "state": lambda x: None,
                "wake": lambda x: None,
                "sleep": lambda x: None,
                "to_system": lambda x: None,
                "option": lambda x: None,
            }
        )

    def _restore_cumulative_stats(self):
        """Restore historical cumulative stats from token_stats.json into _hist_* fields at startup.

        Does not write to chat_api (chat_api.total_* only records the current session, starting from 0).
        Historical data is stored in self._hist_*; _broadcast_token_stats adds them when computing cumulative totals.
        """
        try:
            import json
            import os

            history_dir = getattr(self.chat_api, "history_dir", None)
            if not history_dir:
                return
            stats_file = os.path.join(history_dir, "token_stats.json")
            if not os.path.isfile(stats_file):
                return
            with open(stats_file, encoding="utf-8") as f:
                old = json.load(f)
            cumul = old.get("cumulative") or {}
            if cumul.get("total_tokens", 0) == 0:
                return
            # Restore to history fields (do not write to chat_api; chat_api starts from 0 for this session)
            self._hist_input_tokens = cumul.get("total_input_tokens", 0)
            self._hist_output_tokens = cumul.get("total_output_tokens", 0)
            self._hist_requests = cumul.get("total_requests", 0)
            self._hist_cache_read_tokens = cumul.get("cache_read_tokens", 0)
            self._hist_cache_creation_tokens = cumul.get("cache_creation_tokens", 0)
        except Exception:
            pass

    def _replay_pending(self):
        """Replay buffered pre-ready messages into input_hub so they get processed."""
        if not self._pending_buffer:
            return
        logger.info(f"[Runner] Replaying {len(self._pending_buffer)} pending pre-ready message(s)")
        from opensquad.input_hub import input_hub

        for item in self._pending_buffer:
            input_hub.push(
                content=item["content"],
                source=item.get("source", "web"),
                images=item.get("images"),
                attachments=item.get("attachments"),
                channel=item.get("channel", ""),
            )
        self._pending_buffer.clear()

    def _reset_session_stats(self):
        """When a new session starts, roll the current session token stats into history, then reset chat_api counters."""
        # First roll current session totals into history
        self._hist_input_tokens += getattr(self.chat_api, "total_input_tokens", 0)
        self._hist_output_tokens += getattr(self.chat_api, "total_output_tokens", 0)
        self._hist_requests += getattr(self.chat_api, "total_requests", 0)
        self._hist_cache_read_tokens += getattr(self.chat_api, "total_cache_read_tokens", 0)
        # cache_creation is only tracked by ClaudeAPI; other backends report 0.
        self._hist_cache_creation_tokens += getattr(self.chat_api, "total_cache_creation_tokens", 0)
        # Reset chat_api session counters
        if hasattr(self.chat_api, "total_input_tokens"):
            self.chat_api.total_input_tokens = 0
        if hasattr(self.chat_api, "total_output_tokens"):
            self.chat_api.total_output_tokens = 0
        if hasattr(self.chat_api, "total_requests"):
            self.chat_api.total_requests = 0
        if hasattr(self.chat_api, "total_cache_read_tokens"):
            self.chat_api.total_cache_read_tokens = 0
        if hasattr(self.chat_api, "total_cache_creation_tokens"):
            self.chat_api.total_cache_creation_tokens = 0
        # GoogleAPI-specific: reset prompt_token_count baseline to avoid incorrect delta calculation on first turn of new session
        if hasattr(self.chat_api, "_last_prompt_token_count"):
            self.chat_api._last_prompt_token_count = 0

    def _tools_for_token_stats(self):
        """Resolve tools schema for context token counting (incl. after restart).

        Before the first LLM turn, ``_last_tools`` is unset, so resumed sessions
        would under-count (missing Tool definitions). Fall back to registry.
        """
        tools = getattr(self.chat_api, "_last_tools", None) or self._current_tools
        if tools:
            return tools
        try:
            from opensquad.agent_mode import filter_tools_for_mode, get_current_mode

            raw = self.tool_registry.generate_openai_tools("all") if self.tool_registry else None
            if not raw:
                return None
            return filter_tools_for_mode(raw, get_current_mode())
        except Exception:
            logger.debug("[Runner] _tools_for_token_stats fallback failed", exc_info=True)
            return None

    def _broadcast_token_stats_sync(self):
        """Sync version for __init__ — only writes token_stats.json (no WS event yet)."""
        try:
            from opensquad.token_breakdown import compute_token_breakdown

            tools = self._tools_for_token_stats()
            total = self.chat_api._count_tokens(self.chat_api.req, tools)
            encoding = getattr(self.chat_api, "encoding", None)
            stats = compute_token_breakdown(
                self.chat_api.req,
                tools,
                encoding=encoding,
                total=total,
            )
            token_data = {
                "used": total,
                "max": self.chat_api.token_max,
                "breakdown": stats,
                "model": getattr(self.chat_api, "model", ""),
            }
            import json
            import os

            data_dir = getattr(self.chat_api, "history_dir", None)
            if data_dir:
                stats_file = os.path.join(data_dir, "token_stats.json")
                with open(stats_file, "w", encoding="utf-8") as f:
                    json.dump(token_data, f, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"[Runner] _broadcast_token_stats_sync: {e}")

    async def _broadcast_token_stats(self):
        try:
            import json

            from opensquad.token_breakdown import compute_token_breakdown

            tools = self._tools_for_token_stats()
            total = self.chat_api._count_tokens(self.chat_api.req, tools)
            # `tool` = real tool IO (tool_call args, tool_result / functionResponse).
            # `tool_defs` = OpenAI tools JSON schema sent via the API `tools` param.
            encoding = getattr(self.chat_api, "encoding", None)
            stats = compute_token_breakdown(
                self.chat_api.req,
                tools,
                encoding=encoding,
                total=total,
            )

            # Cumulative totals (history + current session).
            # chat_api.total_* only records the current session; runner._hist_*
            # carries across sessions/restarts (see _restore_cumulative_stats).
            # The token_analytics plugin reads data.cumulative.* from the
            # 'token_stats' EventBus event; without these fields its DB rows
            # store zeros and the dashboard renders empty token numbers.
            cumul_input = self._hist_input_tokens + getattr(self.chat_api, "total_input_tokens", 0)
            cumul_output = self._hist_output_tokens + getattr(self.chat_api, "total_output_tokens", 0)
            cumul_total = cumul_input + cumul_output
            cumul_requests = self._hist_requests + getattr(self.chat_api, "total_requests", 0)
            cumul_cache_read = self._hist_cache_read_tokens + getattr(self.chat_api, "total_cache_read_tokens", 0)
            # cache_creation is only tracked by ClaudeAPI; other backends report 0.
            cumul_cache_creation = self._hist_cache_creation_tokens + getattr(
                self.chat_api, "total_cache_creation_tokens", 0
            )

            token_data = {
                "used": total,
                "max": self.chat_api.token_max,
                "breakdown": stats,
                "model": getattr(self.chat_api, "model", ""),
                "cumulative": {
                    "total_input_tokens": cumul_input,
                    "total_output_tokens": cumul_output,
                    "total_tokens": cumul_total,
                    "total_requests": cumul_requests,
                    "cache_read_tokens": cumul_cache_read,
                    "cache_creation_tokens": cumul_cache_creation,
                },
                "session": {
                    "input_tokens": getattr(self.chat_api, "total_input_tokens", 0),
                    "output_tokens": getattr(self.chat_api, "total_output_tokens", 0),
                    "total_input_tokens": getattr(self.chat_api, "total_input_tokens", 0),
                    "total_output_tokens": getattr(self.chat_api, "total_output_tokens", 0),
                    "total_tokens": getattr(self.chat_api, "total_input_tokens", 0)
                    + getattr(self.chat_api, "total_output_tokens", 0),
                    "requests": getattr(self.chat_api, "total_requests", 0),
                    "total_requests": getattr(self.chat_api, "total_requests", 0),
                    "cache_read_tokens": getattr(self.chat_api, "total_cache_read_tokens", 0),
                },
            }

            logger.warning(
                "[Runner] _broadcast_token_stats: used=%d max=%d pct=%.1f%% msgs=%d sys=%d tool=%d tool_defs=%d thought=%d overhead=%d",
                total,
                self.chat_api.token_max,
                (total / max(self.chat_api.token_max, 1)) * 100,
                len(self.chat_api.req),
                stats.get("system", 0),
                stats.get("tool", 0),
                stats.get("tool_defs", 0),
                stats.get("thought", 0),
                stats.get("overhead", 0),
            )
            await bus.emit_async("token_stats", {"sid": self._turn_sid, "agent_id": self._agent_id, "data": token_data})

            # Write stats file to the agent data directory (for Launcher to read)
            try:
                import os

                data_dir = getattr(self.chat_api, "history_dir", None)
                if data_dir:
                    stats_file = os.path.join(data_dir, "token_stats.json")
                    with open(stats_file, "w", encoding="utf-8") as f:
                        json.dump(token_data, f, ensure_ascii=False)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[Runner] _broadcast_token_stats failed: {e}")


if __name__ == "__main__":
    import argparse
    import asyncio
    import json
    import logging
    import os
    import sys
    import warnings

    # Suppress Windows Proactor pipe closing errors (harmless noise)
    warnings.filterwarnings("ignore", category=ResourceWarning, message="unclosed transport")
    if sys.platform == "win32":
        # Suppress "I/O operation on closed pipe" which is raised as ValueError in asyncio logs
        logging.getLogger("asyncio").setLevel(logging.CRITICAL)

    from opensquad.chat_api import ChatAPI
    from opensquad.claude_api import ClaudeAPI
    from opensquad.gateway_adapter import GatewayAdapter
    from opensquad.registry import ToolRegistry
    from opensquad.sdk import AgentConfig
    from opensquad.tool import logger
    from opensquad.xml_parser import StreamingTagParser

    # Ensure we are in the project root if possible, or handle paths correctly
    # start_team.py runs with python -m opensquad.runner, so cwd is project root.

    parser = argparse.ArgumentParser(description="OpenSquad Agent Runner")
    parser.add_argument("--config", required=True, help="Path to agent config.json")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"Config file not found: {args.config}")
        sys.exit(1)

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)

    # 1. Initialize ToolRegistry
    registry = ToolRegistry()
    # Load default tools (with try-except to prevent one broken module from blocking all)
    try:
        from opensquad.tools import system

        registry.register(system, "system", level="core")
    except ImportError as e:
        logger.warning(f"Failed to import system tool: {e}")
        system = None

    try:
        from opensquad.tools import filesystem

        registry.register(filesystem, "filesystem", level="core")
    except ImportError as e:
        logger.warning(f"Failed to import filesystem tool: {e}")
        filesystem = None

    try:
        from opensquad.tools import memory
    except ImportError as e:
        logger.warning(f"Failed to import memory tool: {e}")
        memory = None

    # Check config for enabled tools
    tools_list = config.get("tools", [])
    if "websearch" in tools_list:
        # websearch is now a plugin -- loaded via plugin system, not direct import
        pass
    if "long_memory" in tools_list:
        registry.register(memory, "memory")

    # Choice tools (propose_options) — always available for plan decision UI
    try:
        from opensquad.tools import choice_tools

        registry.register(choice_tools, "choice_tools", level="core")
    except ImportError as e:
        logger.warning(f"Failed to import choice_tools: {e}")

    # agent_setup tool for project management skill
    if "agent_setup" in tools_list:
        try:
            from opensquad.tools import agent_setup

            registry.register(agent_setup, "agent_setup")
        except ImportError:
            logger.warning("Failed to import agent_setup tool")

    # mcp_query tool for MCP service management
    if "mcp_query" in tools_list:
        try:
            from opensquad.tools import mcp_query

            registry.register(mcp_query, "mcp_query")
        except ImportError:
            logger.warning("Failed to import mcp_query tool")

    # media tool for audio/video processing
    if "media" in tools_list:
        try:
            from opensquad.tools import media

            registry.register(media, "media")
        except ImportError:
            logger.warning("Failed to import media tool")

    # IM tool (Group chat)
    if "im" in tools_list:
        try:
            from opensquad.tools import im

            registry.register(im, "im")
        except ImportError:
            logger.warning("Failed to import im tool")

    # 2. Initialize ChatAPI
    model_conf = config.get("model", {})
    api_key = model_conf.get("api_key")
    base_url = model_conf.get("base_url")
    model_name = model_conf.get("model_name", "gpt-3.5-turbo")

    # Validate api_key early — provide a clear error message instead of silent failure
    if not api_key or api_key in (None, ""):
        agent_name = config.get("agent_name", "unknown")
        logger.error(
            "[Boot] Agent '%s' has NO api_key configured in model card. "
            "The agent will start but cannot call any LLM. "
            "Set api_key in model_cards/*.json or via environment variable.",
            agent_name,
        )
        # Push an immediate error event so frontend/websocket users see it
        from opensquad.bus import bus as _boot_bus

        _boot_bus.emit(
            "agent_error",
            {
                "agent_id": config.get("agent_id") or agent_name,
                "error": (
                    "LLM API key not configured. "
                    "Please set 'api_key' in the model card (model_cards/*.json) "
                    "or via the OPENAI_API_KEY environment variable, then restart the agent."
                ),
                "fatal": True,
            },
        )
    token_max = model_conf.get("token_max", 128000)

    # Prompt
    prompt_conf = config.get("prompt", {})
    base_prompt_path = prompt_conf.get("base", "prompts/base.md")
    role_prompt_path = prompt_conf.get("role", "role.md")

    # Resolve prompt paths relative to config file location
    agent_dir = os.path.dirname(os.path.abspath(args.config))

    def read_prompt(path):
        if not path:
            return ""
        # If absolute, use it. If relative, try relative to agent_dir, then relative to cwd.
        if os.path.isabs(path):
            p = path
        else:
            p = os.path.join(agent_dir, path)
            if not os.path.exists(p):
                p = os.path.abspath(path)  # try cwd relative

        if os.path.exists(p):
            from opensquad.prompt_includes import read_prompt_with_includes

            return read_prompt_with_includes(p, os.path.dirname(os.path.abspath(p)))
        logger.warning(f"Prompt file not found: {path} (looked in {agent_dir} and cwd)")
        return ""

    base_prompt = read_prompt(base_prompt_path)
    role_prompt = read_prompt(role_prompt_path)
    full_prompt = base_prompt + "\n\n" + role_prompt
    if not full_prompt.strip():
        full_prompt = "You are a helpful assistant."

    stream_parser = StreamingTagParser(handlers={})

    if model_name.startswith("claude"):
        chat_api = ClaudeAPI(
            api_key, model_name, base_url, full_prompt, stream_parser=stream_parser, token_max=token_max
        )
    else:
        chat_api = ChatAPI(api_key, model_name, base_url, full_prompt, stream_parser=stream_parser, token_max=token_max)

    # Set history dir to agent dir for persistence
    chat_api.history_dir = os.path.join(agent_dir, "history")
    os.makedirs(chat_api.history_dir, exist_ok=True)

    # 3. Vision Config
    vision_config = config.get("vision", {})

    # 4. Memory Manager (Optional)
    # Note: MemoryManager requires 'agent_memory_tool' which is an external dependency.
    # If not available, we gracefully skip it.
    memory_manager = None
    if "long_memory" in tools_list:
        try:
            from opensquad.memory_manager import MemoryManager

            # We need agent_memory_tool, which is external. Try to import.
            from opensquad.tools.agent_memory_tool.memory import AgentMemory

            agent_name = config.get("agent_name", "unknown_agent")
            memory_dir = os.path.join(agent_dir, "memory")

            # Initialize AgentMemory
            am = AgentMemory(data_dir=memory_dir)

            # Instantiate MemoryManager
            memory_manager = MemoryManager(am, agent_name)
            logger.info("Memory Manager initialized successfully")
        except ImportError:
            logger.warning("long_memory tool requires 'agent_memory_tool' package (external). Skipping memory manager.")
            memory_manager = None

    # 5. Initialize Runner
    runner = AgentRunner(
        chat_api,
        registry,
        vision_config=vision_config,
        memory_manager=memory_manager,
        agent_id=config.get("agent_id", "unknown"),
    )

    # 6. Gateway Adapter Configuration
    gateway_conf = config.get("gateway", {})
    adapter = None
    if gateway_conf.get("enabled"):
        agent_config = AgentConfig(
            gateway_url=gateway_conf.get("url", syscfg.gateway_register_url()),
            agent_id=config.get("agent_id", "unknown"),
            agent_name=config.get("agent_name", "Unknown Agent"),
            agent_type=config.get("agent_type", "general"),
            capabilities=config.get("capabilities", []),
            description=config.get("description", ""),
            node_id=syscfg.node_id(),
            node_label=syscfg.node_label(),
        )
        adapter = GatewayAdapter(agent_config)

    async def main():
        if adapter:
            asyncio.create_task(adapter.start())

        logger.info(f"Agent '{config.get('agent_name')}' started successfully.")
        logger.info(f"ID: {config.get('agent_id')}")
        logger.info(f"Model: {model_name}")

        await runner.run()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAgent stopped by user.")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback

        traceback.print_exc()
        input("Press Enter to exit...")  # Keep window open on crash
