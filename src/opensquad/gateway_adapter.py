import asyncio
import contextlib
import logging

from opensquad import bus
from opensquad.input_hub import input_hub
from opensquad.sdk import AgentConfig, BaseAgent
from opensquad.system_config import syscfg

logger = logging.getLogger(__name__)

_TOP_LEVEL_CMD_SKIP = frozenset({"type", "command", "user_id", "data", "seq", "timestamp"})
_TOP_LEVEL_CMD_FALLBACK_KEYS = (
    "id",
    "request_id",
    "chosen_option_id",
    "chosen_option_ids",
    "option_id",
    "option_ids",
    "custom_answer",
    "custom",
    "ignored",
    "mode",
    "agent_mode",
    "approved_request_id",
    "reason",
    "card",
    "card_name",
    "effort",
    "reasoning_effort",
    "session_id",
    "content",
)


def coerce_command_data(message: dict) -> dict:
    """Return the command payload dict for gateway_adapter handlers.

    Prefer ``message["data"]``. If callers put fields at the top level (a bug that
    used to break group ``resolve_proposed_options``), merge those in so the
    agent still wakes.
    """
    if not isinstance(message, dict):
        return {}
    raw = message.get("data", {})
    cmd_data = dict(raw) if isinstance(raw, dict) else {}
    if not cmd_data:
        return {k: v for k, v in message.items() if k not in _TOP_LEVEL_CMD_SKIP}
    if cmd_data.get("id") or cmd_data.get("request_id"):
        return cmd_data
    if not (message.get("id") or message.get("request_id")):
        return cmd_data
    merged = dict(cmd_data)
    for key in _TOP_LEVEL_CMD_FALLBACK_KEYS:
        if key in message and key not in merged:
            merged[key] = message[key]
    return merged


class GatewayAdapter(BaseAgent):
    """
    Adapter that connects the Main Runner to the Gateway.
    1. Receives Gateway messages -> pushes to InputHub
    2. Listens to Runner events -> forwards back to Gateway

    Event forwarding strategy:
    - Has current_user_id (work triggered by a Web user) -> directed push to that user
    - No current_user_id (triggered by group chat/wakeup) -> broadcast to all Web users connected to this agent
    """

    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self.current_user_id = None
        # stream batch-processing buffer
        self._stream_buffer: list = []
        self._max_chunks = 1000  # P2: hard cap to prevent unbounded growth
        self._stream_flush_task: asyncio.Task | None = None
        self._stream_sid: str = ""
        # Bus subscription tracking — enables clean dispose()
        self._subscriptions: list[tuple[str, callable]] = []

        def _sub(event_type: str, callback: callable):
            """Subscribe and track for later cleanup."""
            bus.subscribe(event_type, callback)
            self._subscriptions.append((event_type, callback))

        # Listen to Runner output events
        _sub("to_user_final", self.on_runner_output)
        _sub("to_user_reply", self.on_runner_output)
        _sub("to_user_end_task", self.on_runner_end_task)
        _sub("thought", self.on_runner_thought)
        _sub("to_user_stream", self.on_runner_stream)
        _sub("tool_call", self.on_tool_call)
        _sub("tool_call_delta", self.on_tool_call_delta)
        _sub("tool_result", self.on_tool_result)
        # Subscribe to state and system notification events
        _sub("state", self.on_runner_state)
        _sub("wake", self.on_generic_event("wake"))
        _sub("sleep", self.on_generic_event("sleep"))
        _sub("info", self.on_generic_event("info"))
        _sub("status", self.on_generic_event("status"))
        _sub("turn_start", self.on_generic_event("turn_start"))
        # Subscribe to Token stats events
        _sub("token_stats", self.on_generic_event("token_stats"))
        # Subscribe to session management events
        _sub("current_session", self.on_generic_event("current_session"))
        _sub("history_sync", self.on_generic_event("history_sync"))
        _sub("session_list", self.on_generic_event("session_list"))
        # Subscribe to plan events
        _sub("plan", self.on_generic_event("plan"))
        # Subscribe to workflow elapsed-time events
        _sub("turn_elapsed", self.on_generic_event("turn_elapsed"))
        # Subscribe to prompt_update events
        _sub("prompt_update", self.on_generic_event("prompt_update"))
        # Subscribe to model output media events (audio/image)
        _sub("output_media", self.on_generic_event("output_media"))
        # Realtime voice events
        _sub("voice_audio_out", self.on_generic_event("voice_audio_out"))
        _sub("voice_transcript", self.on_generic_event("voice_transcript"))
        _sub("voice_realtime_status", self.on_generic_event("voice_realtime_status"))
        # Subscribe to context compression summary stream events
        _sub("summary_stream", self.on_generic_event("summary_stream"))
        _sub("group_member_update", self.on_generic_event("group_member_update"))
        _sub("user_status_update", self.on_generic_event("user_status_update"))
        # Shell / background job live output for CMD-style web panel
        _sub("job_stdout", self.on_generic_event("job_stdout"))
        _sub("job_status", self.on_generic_event("job_status"))
        _sub("compression_progress", self.on_generic_event("compression_progress"))

    def dispose(self):
        """Unsubscribe all event bus handlers.
        Call this when the adapter is being replaced (e.g. during agent hot-restart)
        to prevent stale handlers from piling up and causing duplicate event forwarding.
        """
        count = 0
        for event_type, callback in self._subscriptions:
            try:
                bus.unsubscribe(event_type, callback)
                count += 1
            except Exception as e:
                logger.warning(f"[GatewayAdapter] Failed to unsubscribe {event_type}: {e}")
        self._subscriptions.clear()
        cancel_task = self._stream_flush_task
        if cancel_task and not cancel_task.done():
            cancel_task.cancel()
        logger.info(f"[GatewayAdapter] Disposed: unsubscribed {count} handlers")

    @staticmethod
    def _unwrap(data):
        """
        Unwrap the {"sid": ..., "data": ...} wrapper produced by Runner._emit.
        sid is the internal event bus session routing tag; Gateway/React does not need it.
        Returns the actual content (the value of the "data" field), or returns data unchanged
        if it is not a wrapped structure.

        Note: len(data) == 2 is not enforced because some events (e.g. token_stats)
        carry extra fields like agent_id, but the actual content is still in the "data" key.
        """
        if isinstance(data, dict) and "sid" in data and "data" in data:
            return data["data"]
        return data

    @staticmethod
    def _extract_sid(data) -> str:
        """Extract session ID from EventBus wrapper, if present."""
        if isinstance(data, dict) and "sid" in data and "data" in data:
            return data["sid"]
        return ""

    async def _send_event(self, content, msg_type: str = "message", sid: str = ""):
        """
        Unified event dispatch: directed push when user_id is set, broadcast otherwise.
        Includes session_id so the frontend can filter cross-session messages.
        """
        if self.current_user_id:
            await self.send_response_to_user(self.current_user_id, content, msg_type, sid=sid)
        else:
            await self.send_response(content, msg_type, sid=sid)

    async def on_runner_state(self, data):
        """When Runner state changes: forward the event to the frontend and update local load_percent."""
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            # content may be a string (e.g. "working") or a dict (e.g. {"state": "working", ...})
            state_value = (
                content if isinstance(content, str) else (content.get("state") if isinstance(content, dict) else "")
            )
            if state_value == "working":
                self._load_percent = 100
            else:
                self._load_percent = 0
            logger.debug(f"[GatewayAdapter] State changed to '{state_value}', load_percent={self._load_percent}")
            await self._send_event(content, "state", sid=sid)

    def on_generic_event(self, event_type):
        """Generic event forwarder."""

        async def handler(data):
            if self.connected:
                sid = self._extract_sid(data)
                content = self._unwrap(data)
                logger.debug(f"[GatewayAdapter] Event {event_type}: {str(content)[:50]}...")
                await self._send_event(content, event_type, sid=sid)

        return handler

    async def send_files_to_chat(self, files: list, message: str = "", user_id: str | None = None):
        """
        Push local files to the AI Chat panel (via Gateway /agent-push/chat).
        files: [{path, original_name, size, content_type, is_image, is_audio, is_video}, ...]
        """
        import httpx

        try:
            payload = {
                "agent_id": self.config.agent_id,
                "message": message or "",
                "files": files or [],
            }
            if user_id:
                payload["user_id"] = user_id
            url = f"{syscfg.gateway_http()}/api/ai-web/agent-push/chat"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[GatewayAdapter] send_files_to_chat failed: {e}")
            return False

    async def _handle_command(self, data: dict):
        """Handle command messages from the Gateway (separate channel from chat)."""
        command = data.get("command", "")
        cmd_data = coerce_command_data(data)
        user_id = data.get("user_id", "")

        # Route session/control events back to the requesting web user (not broadcast).
        if user_id:
            self.current_user_id = user_id

        logger.info(f"[Adapter] Command received from Gateway ({user_id}): {command}")

        if command == "stop_task":
            input_hub.request_stop()
            logger.info(f"[Adapter] Stop task requested by user {user_id}")
            return

        if command == "request_token_stats":
            logger.info(f"[Adapter] request_token_stats from user {user_id}")
            input_hub.push_urgent("__REQUEST_TOKEN_STATS__", source="gateway")
            return

        if command == "new_session":
            # Abort sub-agents immediately (don't wait for Runner to drain urgent queue).
            try:
                from opensquad.sub_agent_runner import job_manager

                n = job_manager.cancel_all("new_session")
                if n:
                    logger.info(f"[Adapter] cancelled {n} sub-agent job(s)/runner(s) on new_session")
            except Exception:
                logger.debug("[Adapter] sub-agent cancel_all on new_session skipped", exc_info=True)
            input_hub.push_urgent("__NEW_SESSION__", source="gateway")
            logger.info("[Adapter] New session command sent via urgent queue")
            await self._try_wake_agent("urgent-command")
            return

        if command == "compress_context":
            input_hub.push_urgent("__COMPRESS_CONTEXT__", source="gateway")
            logger.info("[Adapter] Compress context command sent via urgent queue")
            await self._try_wake_agent("urgent-command")
            return

        if command == "switch_and_reply":
            sid = cmd_data.get("session_id", "")
            reply = cmd_data.get("content", "")
            cmd = f"__SWITCH_AND_REPLY__:{sid}:{reply}"
            input_hub.push_urgent(cmd, source="gateway")
            logger.info(f"[Adapter] Switch and reply command sent via urgent queue: {cmd[:80]}")
            await self._try_wake_agent("urgent-command")
            return

        if command == "switch_model":
            # Event-driven runtime model switch.  Only the card name is carried
            # over the WebSocket; the agent process resolves the full cfg (with
            # api_key) from its local model_cards directory.  Emitting on the
            # bus schedules the async coordinator immediately -- no need to wait
            # for the next turn, and no urgent-queue sentinel is pushed so an
            # in-flight LLM stream is not interrupted.
            card_name = cmd_data.get("card", "") or cmd_data.get("card_name", "")
            if card_name:
                bus.emit("model.switch.requested", {"card": card_name})
                logger.info(f"[Adapter] switch_model requested: card={card_name}")
            else:
                logger.warning("[Adapter] switch_model command missing 'card' field")
            return

        if command == "set_reasoning_effort":
            effort = cmd_data.get("effort", "") or cmd_data.get("reasoning_effort", "")
            if effort:
                bus.emit("model.reasoning_effort.requested", {"effort": effort})
                logger.info(f"[Adapter] set_reasoning_effort requested: effort={effort}")
            else:
                logger.warning("[Adapter] set_reasoning_effort command missing 'effort' field")
            return

        if command == "set_agent_mode":
            mode = cmd_data.get("mode", "") or cmd_data.get("agent_mode", "")
            req_id = cmd_data.get("id") or cmd_data.get("approved_request_id")
            if mode:
                bus.emit(
                    "agent.mode.requested",
                    {"mode": mode, "id": req_id, "approved_request_id": req_id},
                )
                logger.info(f"[Adapter] set_agent_mode requested: mode={mode}")
            else:
                logger.warning("[Adapter] set_agent_mode command missing 'mode' field")
            return

        if command == "deny_mode_switch":
            req_id = cmd_data.get("id", "")
            bus.emit("agent.mode.requested", {"action": "deny", "id": req_id, "reason": cmd_data.get("reason", "")})
            logger.info(f"[Adapter] deny_mode_switch: id={req_id}")
            return

        if command == "resolve_proposed_options":
            req_id = cmd_data.get("id", "") or cmd_data.get("request_id", "")
            chosen = cmd_data.get("chosen_option_id", "") or cmd_data.get("option_id", "")
            chosen_ids_raw = cmd_data.get("chosen_option_ids") or cmd_data.get("option_ids") or []
            if isinstance(chosen_ids_raw, str):
                chosen_ids = [p.strip() for p in chosen_ids_raw.split(",") if p.strip()]
            elif isinstance(chosen_ids_raw, list):
                chosen_ids = [str(x).strip() for x in chosen_ids_raw if str(x).strip()]
            else:
                chosen_ids = []
            custom = cmd_data.get("custom_answer", "") or cmd_data.get("custom", "")
            ignored = bool(cmd_data.get("ignored", False))
            if req_id:
                try:
                    from opensquad.model_switch import resolve_proposed_options

                    await resolve_proposed_options(
                        str(req_id),
                        chosen_option_id=str(chosen),
                        chosen_option_ids=chosen_ids,
                        custom_answer=str(custom),
                        ignored=ignored,
                    )
                    logger.info(
                        f"[Adapter] resolve_proposed_options: id={req_id} "
                        f"chosen={chosen_ids or chosen or custom or ('ignored' if ignored else '?')}"
                    )
                except Exception as e:
                    logger.warning(f"[Adapter] resolve_proposed_options failed: {e}")
            else:
                logger.warning("[Adapter] resolve_proposed_options command missing 'id' field")
            return

        if command == "voice_realtime_start":
            from opensquad.audio import realtime_manager as rtm

            try:
                result = await rtm.start_session(
                    voice=cmd_data.get("voice", ""),
                    instructions=cmd_data.get("instructions", ""),
                )
                await self._send_event(result, "voice_realtime_status")
            except Exception as e:
                logger.error("[Adapter] voice_realtime_start failed: %s", e)
                await self._send_event({"status": "error", "error": str(e)}, "voice_realtime_status")
            return

        if command == "voice_realtime_stop":
            from opensquad.audio import realtime_manager as rtm

            try:
                result = await rtm.stop_session()
                await self._send_event(result, "voice_realtime_status")
            except Exception as e:
                logger.error("[Adapter] voice_realtime_stop failed: %s", e)
            return

        if command == "voice_audio_commit":
            from opensquad.audio import realtime_manager as rtm

            await rtm.commit_audio()
            return

        logger.warning(f"[Adapter] Unknown command: {command}, falling back to base handler")
        await super()._handle_command(data)

    async def _try_wake_agent(self, reason: str = "urgent-command", *, inject_sentinel: bool = False):
        """Wake the agent if it is sleeping.

        Args:
            reason: Passed to sleep_controller.wake_up (appears in wake tool result).
            inject_sentinel: When True, also push a synthetic ``[wakeup-urgent-command]``
                into input_hub so the idle loop notices the wake even without a real
                user message.  MUST be False for normal chat — the real user payload
                is pushed right after wake, and the sentinel would otherwise be
                treated as the user's message (or drown it out).
        """
        try:
            from opensquad.sleep_controller import sleep_controller
            from opensquad.state_manager import state_manager

            ai_state = await state_manager.get_state()
            if ai_state == "sleeping":
                sleep_controller.wake_up(reason)
                if inject_sentinel:
                    input_hub.push("[wakeup-urgent-command]", source="wake")
                logger.info(f"[Adapter] Woke agent from sleep for {reason} (sentinel={inject_sentinel})")
        except Exception as e:
            logger.warning(f"[Adapter] Wake-up for {reason} failed: {e}")

    async def _handle_chat(self, data: dict):
        """Handle user messages from the Gateway."""
        user_id = data.get("user_id")
        content = data.get("content")
        images = data.get("images", [])
        attachments = data.get("attachments", [])
        channel = data.get("channel", "web")
        sender_name = data.get("sender_name", "")
        chat_name = data.get("chat_name", "")
        source_chat_id = data.get("source_chat_id", "")
        self.current_user_id = user_id

        logger.info(
            f"[Adapter] Received from Gateway ({user_id}, channel={channel}): {content}"
            + (f" images={len(images)}" if images else "")
            + (f" attachments={len(attachments)}" if attachments else "")
        )

        # Wake only — do NOT inject a fake wakeup message; the real payload follows.
        await self._try_wake_agent("web-message", inject_sentinel=False)

        logger.debug(f"[Adapter] About to push to input_hub: content_len={len(content)}, channel={channel}")
        input_hub.push(
            content,
            source="gateway",
            images=images if images else None,
            attachments=attachments if attachments else None,
            channel=channel,
            sender_name=sender_name,
            chat_name=chat_name,
            source_chat_id=source_chat_id,
            user_id=user_id or "",
        )
        logger.debug("[Adapter] Push to input_hub DONE")

    async def on_runner_output(self, data):
        """When Runner finishes a reply (final text response; content should be a string)."""
        logger.info(f"[GatewayAdapter] on_runner_output called, connected={self.connected}, data={str(data)[:200]}")
        # Flush any pending stream debounce so clients don't keep a truncated preview.
        await self._flush_stream_buffer_now()
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            if content:
                logger.info(
                    f"[GatewayAdapter] Sending final response (user={self.current_user_id or 'broadcast'}), content_len={len(str(content))}, content_preview={str(content)[:100]}"
                )
                await self._send_event(content, "message", sid=sid)
            else:
                logger.warning("[GatewayAdapter] on_runner_output called but content is empty")
        else:
            logger.warning("[GatewayAdapter] on_runner_output called but not connected, discarding response")

    async def on_runner_end_task(self, data):
        """Complex-task final report — distinct WS type so the UI can fold the process."""
        logger.info(f"[GatewayAdapter] on_runner_end_task called, connected={self.connected}, data={str(data)[:200]}")
        await self._flush_stream_buffer_now()
        if not self.connected:
            logger.warning("[GatewayAdapter] on_runner_end_task called but not connected, discarding response")
            return
        sid = self._extract_sid(data)
        content = self._unwrap(data)
        if not content:
            logger.warning("[GatewayAdapter] on_runner_end_task called but content is empty")
            return
        await self._send_event(content, "to_user_end_task", sid=sid)

    async def on_runner_thought(self, data):
        """When Runner is thinking (content may be a {"text":...,"final":...} object)."""
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            await self._send_event(content, "thought", sid=sid)

    async def on_runner_stream(self, data):
        """When Runner streams output -- uses 30ms debounce batching to reduce WS frame count."""
        if not self.connected:
            return
        sid = self._extract_sid(data)
        content = self._unwrap(data)
        if not content:
            return

        # Store latest sid for flush
        self._stream_sid = sid

        # Add chunk to buffer
        self._stream_buffer.append(content)
        # P2: enforce max_chunks limit — flush immediately if exceeded
        if len(self._stream_buffer) >= self._max_chunks:
            if self._stream_flush_task is None or self._stream_flush_task.done():
                self._stream_flush_task = asyncio.create_task(self._flush_stream_buffer())

        # Don't create a duplicate flush task if one is already pending
        if self._stream_flush_task is not None and not self._stream_flush_task.done():
            return

        # Schedule a flush task after 30ms
        self._stream_flush_task = asyncio.create_task(self._flush_stream_buffer())

    async def _flush_stream_buffer(self):
        """Wait 30ms then send all buffered chunks merged into one frame."""
        try:
            await asyncio.sleep(0.03)
        except asyncio.CancelledError:
            return
        await self._emit_stream_buffer()

    async def _flush_stream_buffer_now(self) -> None:
        """Immediately flush pending stream chunks (cancel debounce timer if any)."""
        task = getattr(self, "_stream_flush_task", None)
        current = asyncio.current_task()
        if task is not None and not task.done() and task is not current:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._stream_flush_task = None
        await self._emit_stream_buffer()

    async def _emit_stream_buffer(self) -> None:
        """Send and clear whatever is currently in the stream debounce buffer."""
        if not self._stream_buffer:
            return
        chunks = self._stream_buffer[:]
        self._stream_buffer.clear()
        sid = self._stream_sid
        combined = "".join(c for c in chunks if isinstance(c, str))
        if combined and self.connected:
            await self._send_event(combined, "stream", sid=sid)
        for c in chunks:
            if not isinstance(c, str) and self.connected:
                await self._send_event(c, "stream", sid=sid)

    async def on_tool_call(self, data):
        """When a tool is called (content is a {"name":...,"args":...,"id":...} object)."""
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            await self._send_event(content, "tool_call", sid=sid)

    async def on_tool_call_delta(self, data):
        """Incremental native-FC tool arguments (file write/edit streaming preview)."""
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            await self._send_event(content, "tool_call_delta", sid=sid)

    async def on_tool_result(self, data):
        """When a tool result is returned (content is a {"id":...,"name":...,"result":...} object)."""
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            await self._send_event(content, "tool_result", sid=sid)


async def start_gateway_adapter():
    config = AgentConfig(
        gateway_url=syscfg.gateway_register_url(),
        agent_id="legacy-main",  # This ID must match what the frontend accesses
        agent_name="Primary Controller Agent",
        agent_type="general",
        capabilities=["core", "tools", "legacy"],
        description="Main.py core engine",
        node_id=syscfg.node_id(),
        node_label=syscfg.node_label(),
    )

    adapter = GatewayAdapter(config)
    # Run in the background
    asyncio.create_task(adapter.start())
