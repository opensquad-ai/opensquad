import asyncio
import logging

from opensquad import bus
from opensquad.input_hub import input_hub
from opensquad.sdk import AgentConfig, BaseAgent
from opensquad.system_config import syscfg

logger = logging.getLogger(__name__)


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
        _sub("thought", self.on_runner_thought)
        _sub("to_user_stream", self.on_runner_stream)
        _sub("tool_call", self.on_tool_call)
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
        # Subscribe to context compression summary stream events
        _sub("summary_stream", self.on_generic_event("summary_stream"))
        _sub("group_member_update", self.on_generic_event("group_member_update"))
        _sub("user_status_update", self.on_generic_event("user_status_update"))

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
        cmd_data = data.get("data", {})
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

        logger.warning(f"[Adapter] Unknown command: {command}, falling back to base handler")
        await super()._handle_command(data)

    async def _try_wake_agent(self, reason: str = "urgent-command"):
        try:
            from opensquad.sleep_controller import sleep_controller
            from opensquad.state_manager import state_manager

            ai_state = await state_manager.get_state()
            if ai_state == "sleeping":
                sleep_controller.wake_up(reason)
                input_hub.push("[wakeup-urgent-command]", source="wake")
                logger.info(f"[Adapter] Woke agent from sleep for {reason}")
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

        # Push message to InputHub; Runner in main.py handles it uniformly.
        await self._try_wake_agent("web-message")

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
        await asyncio.sleep(0.03)
        if not self._stream_buffer:
            return
        # Take and clear the buffer
        chunks = self._stream_buffer[:]
        self._stream_buffer.clear()
        sid = self._stream_sid
        # Merge string chunks (non-string content is not merged, sent individually)
        combined = "".join(c for c in chunks if isinstance(c, str))
        if combined:
            await self._send_event(combined, "stream", sid=sid)
        # Non-string chunks (theoretically non-existent; handled as safety net)
        for c in chunks:
            if not isinstance(c, str):
                await self._send_event(c, "stream", sid=sid)

    async def on_tool_call(self, data):
        """When a tool is called (content is a {"name":...,"args":...,"id":...} object)."""
        if self.connected:
            sid = self._extract_sid(data)
            content = self._unwrap(data)
            await self._send_event(content, "tool_call", sid=sid)

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
