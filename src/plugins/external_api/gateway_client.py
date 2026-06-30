"""
Gateway WebSocket Client

Maintains a WebSocket connection to the ChatPro Gateway,
responsible for sending messages and receiving Agent response events.

Protocol: reuses the /ai-web/ws/{agent_id}?token=xxx endpoint,
sharing the same message format as the Web UI.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import websockets

logger = logging.getLogger("external.gateway_client")


def _ws_is_closed(ws) -> bool:
    """Check if WebSocket is closed (compatible with websockets v12~v15+)"""
    # v12 and earlier: has .closed bool
    if hasattr(ws, "closed"):
        return ws.closed
    # v13+: .close_code is None while open, set to int after close
    if hasattr(ws, "close_code"):
        return ws.close_code is not None
    # fallback: assume closed
    return True


@dataclass
class AgentEvent:
    """A single event returned by the Agent"""

    event_type: str  # message / stream / thought / tool_call / tool_result / error / connected
    content: object = None  # content (string or dict)
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "type": self.event_type,
            "content": self.content,
            "timestamp": self.timestamp or time.time(),
        }


@dataclass
class PendingRequest:
    """A pending request awaiting a reply"""

    request_id: str
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    done: bool = False
    final_text: str = ""
    all_events: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


class GatewayWSClient:
    """
    WebSocket client to the Gateway.

    Each (agent_id, user_id) pair has its own independent WS connection.
    Supports multiple concurrent requests sharing a single connection
    (dispatched via PendingRequest queues).
    """

    def __init__(self, gateway_ws_url: str, gateway_token: str):
        self.gateway_ws_url = gateway_ws_url
        self.gateway_token = gateway_token
        # agent_id -> WebSocket connection
        self._connections: dict[str, websockets.WebSocketClientProtocol] = {}
        # agent_id -> background reader task
        self._reader_tasks: dict[str, asyncio.Task] = {}
        # agent_id -> current active PendingRequest (single conversation round per Agent)
        self._pending: dict[str, PendingRequest] = {}
        # connection lock to prevent concurrent connections to the same Agent
        self._connect_locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, agent_id: str) -> asyncio.Lock:
        if agent_id not in self._connect_locks:
            self._connect_locks[agent_id] = asyncio.Lock()
        return self._connect_locks[agent_id]

    async def ensure_connected(self, agent_id: str) -> bool:
        """Ensure the WS connection to the specified Agent is established"""
        lock = self._get_lock(agent_id)
        async with lock:
            ws = self._connections.get(agent_id)
            if ws and not _ws_is_closed(ws):
                return True

            url = f"{self.gateway_ws_url}/{agent_id}?token={self.gateway_token}"
            try:
                ws = await websockets.connect(url, max_size=2**24, ping_interval=30)
                self._connections[agent_id] = ws
                logger.info(f"[GW] Connected to Gateway for agent '{agent_id}'")

                # Read the connection confirmation message
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                data = json.loads(raw)
                if data.get("type") == "connected":
                    logger.info(f"[GW] Agent '{agent_id}' online: {data.get('agent_name', '?')}")
                elif data.get("type") == "error":
                    logger.error(f"[GW] Agent '{agent_id}' error: {data.get('message')}")
                    await ws.close()
                    del self._connections[agent_id]
                    return False

                # Read and discard history messages (Gateway sends type=history messages)
                try:
                    history_count = data.get("history_count", 0)
                    for _ in range(history_count):
                        await asyncio.wait_for(ws.recv(), timeout=2)
                except asyncio.TimeoutError:
                    pass

                # Start background reader task
                task = asyncio.create_task(self._reader_loop(agent_id))
                self._reader_tasks[agent_id] = task

                return True
            except Exception as e:
                logger.error(f"[GW] Failed to connect to agent '{agent_id}': {e}")
                return False

    async def _reader_loop(self, agent_id: str):
        """Background task: continuously reads events pushed by Gateway and dispatches to PendingRequest"""
        ws = self._connections.get(agent_id)
        if not ws:
            return

        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "")
                content = data.get("content")

                # Build event
                event = AgentEvent(
                    event_type=msg_type,
                    content=content,
                    timestamp=time.time(),
                )

                # Dispatch to current pending request
                pending = self._pending.get(agent_id)
                if pending and not pending.done:
                    pending.all_events.append(event)
                    await pending.event_queue.put(event)

                    # message type indicates the final reply
                    if msg_type in ("message", "response"):
                        if isinstance(content, str):
                            pending.final_text = content
                        pending.done = True

                    # error also ends the request
                    if msg_type == "error":
                        pending.done = True

                # Skip pong / history etc. that don't need forwarding
                if msg_type in ("pong", "history", "connected"):
                    continue

        except websockets.ConnectionClosed:
            logger.warning(f"[GW] Connection to agent '{agent_id}' closed")
        except asyncio.CancelledError:
            pass
        finally:
            self._connections.pop(agent_id, None)
            self._reader_tasks.pop(agent_id, None)
            # Notify current pending request that the connection was closed
            pending = self._pending.get(agent_id)
            if pending and not pending.done:
                await pending.event_queue.put(AgentEvent(event_type="error", content="Gateway connection closed"))
                pending.done = True

    async def send_and_wait(
        self,
        agent_id: str,
        message: str,
        user_id: str = "external-user",
        images: list | None = None,
        timeout: float = 120.0,
        channel: str = "external",
        sender_name: str = "",
        chat_name: str = "",
        source_chat_id: str = "",
    ) -> PendingRequest:
        """
        Send a message and wait for the complete reply.
        Returns a PendingRequest where final_text is the final text and all_events contains all events.
        """
        if not await self.ensure_connected(agent_id):
            req = PendingRequest(request_id=str(uuid.uuid4()))
            req.done = True
            req.final_text = ""
            req.all_events.append(AgentEvent(event_type="error", content=f"Agent '{agent_id}' is not available"))
            return req

        ws = self._connections.get(agent_id)
        if not ws or _ws_is_closed(ws):
            req = PendingRequest(request_id=str(uuid.uuid4()))
            req.done = True
            req.final_text = ""
            req.all_events.append(AgentEvent(event_type="error", content="WebSocket connection lost"))
            return req

        # Create PendingRequest
        req = PendingRequest(request_id=str(uuid.uuid4()))
        self._pending[agent_id] = req

        # Send message
        msg = {
            "type": "chat",
            "content": message,
            "channel": channel,
        }
        if images:
            msg["images"] = images
        if sender_name:
            msg["sender_name"] = sender_name
        if chat_name:
            msg["chat_name"] = chat_name
        if source_chat_id:
            msg["source_chat_id"] = source_chat_id

        try:
            await ws.send(json.dumps(msg))
            logger.info(f"[GW] Sent message to '{agent_id}': {message[:80]}...")
        except Exception as e:
            req.done = True
            req.all_events.append(AgentEvent(event_type="error", content=f"Failed to send: {e}"))
            return req

        # Wait for completion
        try:
            deadline = time.time() + timeout
            while not req.done:
                remaining = deadline - time.time()
                if remaining <= 0:
                    req.all_events.append(AgentEvent(event_type="error", content="Request timed out"))
                    req.done = True
                    break
                try:
                    await asyncio.wait_for(req.event_queue.get(), timeout=min(remaining, 5.0))
                except asyncio.TimeoutError:
                    # Keep waiting, total timeout not yet reached
                    continue
        finally:
            # Clean up
            if self._pending.get(agent_id) is req:
                del self._pending[agent_id]

        return req

    async def send_and_stream(
        self,
        agent_id: str,
        message: str,
        user_id: str = "external-user",
        images: list | None = None,
        timeout: float = 120.0,
        channel: str = "external",
        sender_name: str = "",
        chat_name: str = "",
        source_chat_id: str = "",
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Send a message and return events one-by-one as an AsyncGenerator.
        Used for SSE and WebSocket streaming mode.
        """
        if not await self.ensure_connected(agent_id):
            yield AgentEvent(event_type="error", content=f"Agent '{agent_id}' is not available")
            return

        ws = self._connections.get(agent_id)
        if not ws or _ws_is_closed(ws):
            yield AgentEvent(event_type="error", content="WebSocket connection lost")
            return

        # Create PendingRequest
        req = PendingRequest(request_id=str(uuid.uuid4()))
        self._pending[agent_id] = req

        # Send message
        msg = {
            "type": "chat",
            "content": message,
            "channel": channel,
        }
        if images:
            msg["images"] = images
        if sender_name:
            msg["sender_name"] = sender_name
        if chat_name:
            msg["chat_name"] = chat_name
        if source_chat_id:
            msg["source_chat_id"] = source_chat_id

        try:
            await ws.send(json.dumps(msg))
            logger.info(f"[GW] Sent (stream) to '{agent_id}': {message[:80]}...")
        except Exception as e:
            yield AgentEvent(event_type="error", content=f"Failed to send: {e}")
            return

        # Yield events one by one
        try:
            deadline = time.time() + timeout
            while not req.done:
                remaining = deadline - time.time()
                if remaining <= 0:
                    yield AgentEvent(event_type="error", content="Request timed out")
                    break
                try:
                    event = await asyncio.wait_for(req.event_queue.get(), timeout=min(remaining, 5.0))
                    yield event
                except asyncio.TimeoutError:
                    continue
        finally:
            if self._pending.get(agent_id) is req:
                del self._pending[agent_id]

    async def close_all(self):
        """Close all connections"""
        for _agent_id, task in list(self._reader_tasks.items()):
            task.cancel()
        for _agent_id, ws in list(self._connections.items()):
            with contextlib.suppress(Exception):
                await ws.close()
        self._connections.clear()
        self._reader_tasks.clear()
        self._pending.clear()
        logger.info("[GW] All connections closed")
