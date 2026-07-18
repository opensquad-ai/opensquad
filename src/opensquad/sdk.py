"""
Agent SDK - allows agents to self-register with the AI Web Gateway
(Re-implemented for opensquad v3)
"""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import websockets

logger = logging.getLogger(__name__)


def _drain_task_cancellation() -> int:
    """Clear leaked cancel counters (anyio/MCP on Python 3.12+) for the current task."""
    task = asyncio.current_task()
    if task is None or not hasattr(task, "uncancel"):
        return 0
    drained = 0
    while task.uncancel() > 0:
        drained += 1
    return drained


@dataclass
class AgentConfig:
    """Agent configuration."""

    gateway_url: str  # Gateway WebSocket address
    agent_id: str  # unique agent ID
    agent_name: str  # display name
    agent_type: str  # type: coder/writer/analyst
    capabilities: list[str]  # capability list
    description: str = ""  # description
    node_id: str = ""  # node ID (passed in via AgentConfig for multi-node deployments)
    node_label: str = ""  # human-readable node label
    node_secret: str = ""  # node auth secret (must match Gateway's auth.node_secret)


class BaseAgent:
    """
    Base agent class.
    Handles self-registration and message processing.
    """

    # WebSocket message deduplication config
    DEDUP_WINDOW_SIZE = 100  # keep last N seq numbers
    DEDUP_LOG_INTERVAL = 60  # seconds between "duplicate dropped" log summaries

    def __init__(self, config: AgentConfig):
        self.config = config
        self.ws = None
        self.connected = False
        self.message_handler: Callable | None = None
        self.command_handler: Callable | None = None
        self._load_percent: int = 0  # updated by subclass/adapter to reflect current load

        # P2-2: Message sequencing and deduplication
        self._send_seq: int = 0  # monotonically increasing outbound sequence number
        self._recv_seq_history: set = set()  # recently seen inbound seq numbers
        self._recv_seq_ordered: list = []  # ordered list for eviction (FIFO)
        self._last_recv_seq: int | None = None  # highest seq seen so far
        self._dup_drop_count: int = 0  # duplicates dropped since last log
        self._dup_log_time: float = 0.0  # last time we logged duplicate summary
        self._reconnect_attempts: int = 0  # consecutive reconnect attempts for backoff

    async def _disconnect(self) -> None:
        """Close the current Gateway WS connection (best-effort)."""
        self.connected = False
        ws = self.ws
        self.ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def start(self):
        """Start the agent."""
        logger.info(f"Starting agent {self.config.agent_id}...")
        reconnect_delay = 1.0
        while True:
            attempt_t0 = asyncio.get_event_loop().time()
            try:
                await self._connect()
                await self._register()
                ready_ms = int((asyncio.get_event_loop().time() - attempt_t0) * 1000)
                logger.info(f"[BootPerf] agent_ws_registered_ready={ready_ms}ms agent_id={self.config.agent_id}")
                reconnect_delay = 1.0
                self._reconnect_attempts = 0
                await self._message_loop()
            except asyncio.CancelledError:
                # CancelledError is BaseException — not caught by `except Exception`.
                # anyio/MCP can leak cancels into this task and kill the WS reader
                # while Gateway still shows the agent as online (send_text succeeds,
                # but no chat reaches GatewayAdapter). Drain and reconnect.
                drained = _drain_task_cancellation()
                self._reconnect_attempts += 1
                logger.warning(
                    "[SDK] CancelledError in agent WS loop (drained=%d), reconnecting in %ds (attempt %d)...",
                    drained,
                    reconnect_delay,
                    self._reconnect_attempts,
                )
                await self._disconnect()
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60.0)
            except websockets.exceptions.ConnectionClosed:
                self._reconnect_attempts += 1
                logger.warning(
                    "Connection closed, reconnecting in %ds (attempt %d)...", reconnect_delay, self._reconnect_attempts
                )
                await self._disconnect()
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60.0)
            except Exception as e:
                self._reconnect_attempts += 1
                logger.error(
                    f"Error: {e}, reconnecting in %ds (attempt %d)...", reconnect_delay, self._reconnect_attempts
                )
                await self._disconnect()
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60.0)

    async def _connect(self):
        """Connect to the Gateway."""
        logger.info(f"Connecting to {self.config.gateway_url}...")
        self.ws = await websockets.connect(
            self.config.gateway_url,
            proxy=None,
            open_timeout=15,
            close_timeout=5,
        )
        logger.info("Connected!")

    async def _register(self):
        """Send registration information."""
        register_msg = {
            "action": "register",
            "agent_id": self.config.agent_id,
            "agent_name": self.config.agent_name,
            "agent_type": self.config.agent_type,
            "capabilities": self.config.capabilities,
            "description": self.config.description,
            "node_id": self.config.node_id,
            "node_label": self.config.node_label,
            "node_secret": self.config.node_secret,
        }

        await self.ws.send(json.dumps(register_msg))

        # Wait for confirmation
        response = await self.ws.recv()
        data = json.loads(response)

        if data.get("status") == "registered":
            self.connected = True
            logger.info(f"Registered successfully! Route: {data.get('assigned_route')}")

            # Start heartbeat
            asyncio.create_task(self._heartbeat_loop())
        else:
            raise Exception(f"Registration failed: {data.get('message')}")

    async def _heartbeat_loop(self):
        """Heartbeat loop."""
        while self.connected:
            try:
                await asyncio.sleep(30)  # 30-second heartbeat
                if self.ws and self.connected:
                    await self.ws.send(
                        json.dumps({"action": "heartbeat", "stats": {"load_percent": self._load_percent}})
                    )
            except asyncio.CancelledError:
                drained = _drain_task_cancellation()
                logger.warning("[SDK] CancelledError in heartbeat loop (drained=%d), stopping heartbeat", drained)
                self.connected = False
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                self.connected = False
                break

    async def _process_inbound_message(self, message: str) -> None:
        """Parse and dispatch one inbound Gateway WS frame."""
        data = json.loads(message)
        msg_type = data.get("type") or data.get("action")

        # P2-2: Deduplication — skip duplicate messages
        seq = data.get("seq")
        if seq is not None:
            if self._is_duplicate(seq):
                self._dup_drop_count += 1
                now = asyncio.get_event_loop().time()
                if now - self._dup_log_time >= self.DEDUP_LOG_INTERVAL:
                    logger.warning(
                        f"[SDK] Dropped {self._dup_drop_count} duplicate message(s) in last {self.DEDUP_LOG_INTERVAL}s"
                    )
                    self._dup_drop_count = 0
                    self._dup_log_time = now
                return
            self._record_seq(seq)

            # P2-2: Out-of-order detection
            if self._last_recv_seq is not None and seq < self._last_recv_seq:
                logger.warning(
                    f"[SDK] Out-of-order message detected: seq={seq} < last={self._last_recv_seq}, type={msg_type}"
                )
            self._last_recv_seq = max(self._last_recv_seq or 0, seq)

        logger.debug(f"[SDK] Received WS message: type={msg_type}, keys={list(data.keys())}")

        if msg_type == "chat":
            logger.info(f"Processing chat message from user {data.get('user_id')}")
            await self._handle_chat(data)
        elif msg_type == "command":
            logger.info(f"Processing command: {data}")
            await self._handle_command(data)
        elif msg_type == "voice_audio_in":
            # Low-latency realtime PCM16 chunk from browser (base64)
            try:
                from opensquad.audio import realtime_manager as rtm

                audio = data.get("audio") or data.get("data") or ""
                if isinstance(audio, dict):
                    audio = audio.get("audio") or ""
                if audio:
                    await rtm.append_audio(audio)
            except Exception as e:
                logger.error("[SDK] voice_audio_in failed: %s", e)
        elif msg_type == "voice_mouthpiece_utterance":
            try:
                from opensquad.audio import realtime_manager as rtm

                audio = data.get("audio") or ""
                if isinstance(audio, dict):
                    sample_rate = int(audio.get("sample_rate") or 24000)
                    audio = audio.get("audio") or ""
                else:
                    sample_rate = int(data.get("sample_rate") or 24000)
                if audio:
                    await rtm.handle_mouthpiece_utterance(audio, sample_rate=sample_rate)
            except Exception as e:
                logger.error("[SDK] voice_mouthpiece_utterance failed: %s", e)
        elif msg_type == "pong":
            logger.debug("Received pong")
        else:
            logger.warning(f"Unknown message type: {msg_type}, data: {data}")

    async def _message_loop(self):
        """Message processing loop (with deduplication and out-of-order detection)."""
        logger.info(f"[SDK] _message_loop STARTED for agent {self.config.agent_id}")
        try:
            async for message in self.ws:
                try:
                    await self._process_inbound_message(message)
                except asyncio.CancelledError:
                    drained = _drain_task_cancellation()
                    logger.warning(
                        "[SDK] CancelledError while handling inbound message (drained=%d), continuing...",
                        drained,
                    )
                    continue
                except Exception as e:
                    logger.error(f"Message handling error: {e}", exc_info=True)
        except asyncio.CancelledError:
            drained = _drain_task_cancellation()
            logger.warning("[SDK] CancelledError in message recv loop (drained=%d), will reconnect...", drained)
            self.connected = False
            raise

    def _is_duplicate(self, seq: int) -> bool:
        """Check if a message sequence number has been seen before."""
        return seq in self._recv_seq_history

    def _record_seq(self, seq: int):
        """Record a sequence number in the deduplication window."""
        self._recv_seq_history.add(seq)
        self._recv_seq_ordered.append(seq)
        # Evict oldest entries when window is full
        while len(self._recv_seq_ordered) > self.DEDUP_WINDOW_SIZE:
            old_seq = self._recv_seq_ordered.pop(0)
            self._recv_seq_history.discard(old_seq)

    async def _handle_chat(self, data: dict):
        """Handle chat message (subclasses should override this method)."""
        user_id = data.get("user_id")
        content = data.get("content")
        data.get("history", [])

        logger.info(f"Received chat from {user_id}: {content}")

        # Default reply
        response = (
            f"[{self.config.agent_name}] Received your message: {content}\n\nI am an AI assistant here to help you."
        )

        # Send response including user_id so the Gateway can route it
        await self.send_response_to_user(user_id, response)

    async def _handle_command(self, data: dict):
        """Handle a command."""
        command = data.get("command")

        if command == "update_config":
            config = data.get("config", {})
            await self.on_config_update(config)
        elif command == "reload":
            await self.on_reload()
        elif command == "shutdown":
            await self.on_shutdown()

    async def send_response(self, content: str, msg_type: str = "message", sid: str = ""):
        """Send a reply to the user (with sequence number for deduplication)."""
        if self.ws and self.connected:
            self._send_seq += 1
            payload = {
                "type": msg_type,
                "role": "assistant",
                "content": content,
                "timestamp": asyncio.get_event_loop().time(),
                "seq": self._send_seq,  # P2-2: outbound sequence number
            }
            if sid:
                payload["sid"] = sid
            await self.ws.send(json.dumps(payload))

    async def send_response_to_user(self, user_id: str, content: str, msg_type: str = "message", sid: str = ""):
        """Send a reply to a specific user (with sequence number for deduplication)."""
        if self.ws and self.connected:
            self._send_seq += 1
            payload = {
                "type": msg_type,
                "user_id": user_id,
                "role": "assistant",
                "content": content,
                "timestamp": asyncio.get_event_loop().time(),
                "seq": self._send_seq,  # P2-2: outbound sequence number
            }
            if sid:
                payload["sid"] = sid
            await self.ws.send(json.dumps(payload))

    async def send_thought(self, content: str):
        """Send the thought process."""
        await self.send_response(content, "thought")

    # Methods that subclasses may override
    async def on_config_update(self, config: dict):
        """Called when configuration is updated."""
        logger.info(f"Config updated: {config}")

    async def on_reload(self):
        """Called when the agent is reloaded."""
        logger.info("Reloading...")

    async def on_shutdown(self):
        """Called when the agent is shutting down."""
        logger.info("Shutting down...")
        self.connected = False
