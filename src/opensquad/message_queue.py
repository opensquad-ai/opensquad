"""
Message queue pipeline - for real-time receipt and accumulation of group/DM messages.
The agent checks this pipeline each conversation turn.
"""

import asyncio
import contextlib
import logging
import queue
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class QueueMessage:
    """Pipeline message structure."""

    id: str
    type: str  # 'group' | 'dm'
    source_id: str  # group_id or sender_id
    source_name: str  # group name or sender name
    sender_id: str
    sender_name: str
    content: str
    timestamp: float
    mentions: list[str]
    raw_data: dict[Any, Any]  # raw data, used for context
    images: list[str] = field(default_factory=list)  # image path list


class MessageQueue:
    """
    Asynchronous message pipeline.
    - WebSocket receiver places messages into the queue.
    - AI periodically consumes from the queue.
    - Supports message accumulation and priority.
    """

    def __init__(self, max_size: int = 1000):
        self._queue = asyncio.Queue(maxsize=max_size)
        # Deduplication: deque maintains insertion order (for maxlen eviction); set provides O(1) lookup.
        self._processed_ids_deque: deque = deque(maxlen=2000)
        self._processed_ids_set: set = set()
        self._lock = asyncio.Lock()
        self._stats = {"received": 0, "consumed": 0, "dropped": 0}
        # ── Event-driven notification (P0 perf) ──
        self._new_message_event: asyncio.Event | None = None

    async def put(self, msg: QueueMessage) -> bool:
        """Put a message into the queue (async, non-blocking)."""
        try:
            # Deduplication check (O(1) set lookup)
            if msg.id in self._processed_ids_set:
                logger.debug(f"[Queue] Duplicate message dropped: {msg.id}")
                return False

            # Try to enqueue (non-blocking)
            self._queue.put_nowait(msg)

            # Maintain deque+set dual structure.
            # deque(maxlen=2000) automatically evicts the oldest ID; remove the evicted ID from the set.
            if len(self._processed_ids_deque) == self._processed_ids_deque.maxlen:
                evicted = self._processed_ids_deque[0]  # oldest ID about to be evicted
                self._processed_ids_set.discard(evicted)
            self._processed_ids_deque.append(msg.id)
            self._processed_ids_set.add(msg.id)

            self._stats["received"] += 1
            logger.info(f"[Queue] Message queued: {msg.type} from {msg.sender_name} in {msg.source_name}")

            # Signal event-driven waiters
            if self._new_message_event is not None:
                self._new_message_event.set()

            # Push to event_pipeline for "never stop" inner-loop architecture
            try:
                from opensquad.event_pipeline import event_pipeline

                event_pipeline.push_nowait(
                    source=msg.type,  # "group" or "dm"
                    content=msg.content,
                    metadata={
                        "sender_name": msg.sender_name,
                        "source_name": msg.source_name,
                        "source_id": msg.source_id,
                        "sender_id": msg.sender_id,
                    },
                )
            except Exception:
                pass  # event_pipeline not available, no-op

            return True

        except queue.Full:
            self._stats["dropped"] += 1
            logger.warning(f"[Queue] Queue full! Message dropped from {msg.sender_name}")
            return False
        except Exception as e:
            logger.error(f"[Queue] Failed to put message: {e}")
            return False

    async def get(self, timeout: float = 0.1) -> QueueMessage | None:
        """Get a message with timeout (non-blocking)."""
        try:
            msg = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            self._stats["consumed"] += 1
            self._queue.task_done()
            return msg
        except asyncio.TimeoutError:
            return None

    def get_all(self) -> list[QueueMessage]:
        """Get all accumulated messages synchronously (for AI polling).

        Drains the queue atomically: messages are only committed to the
        returned list after a successful ``get_nowait`` + ``task_done`` pair,
        so an exception in the middle cannot leave the queue in an
        inconsistent task-count state or silently drop a dequeued message.
        """
        messages = []
        while True:
            try:
                msg = self._queue.get_nowait()
            except (queue.Empty, asyncio.QueueEmpty):
                break
            # task_done must be paired with get; do it immediately so the
            # unfinished-task counter stays consistent even if append raises.
            with contextlib.suppress(ValueError):
                self._queue.task_done()
            messages.append(msg)
            self._stats["consumed"] += 1
        return messages

    # ── Event-driven notification API (P0 perf) ──

    def get_message_event(self) -> asyncio.Event:
        """Get (or lazily create) the asyncio.Event that is set when new messages arrive."""
        if self._new_message_event is None:
            self._new_message_event = asyncio.Event()
        return self._new_message_event

    async def wait_for_message(self, timeout: float = 5.0) -> bool:
        """Wait up to *timeout* seconds for a new message.

        Returns True if a message is available, False on timeout.
        """
        event = self.get_message_event()
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            event.clear()
            return True
        except asyncio.TimeoutError:
            return False

    def peek(self) -> QueueMessage | None:
        """Peek at the next message without removing it from the queue."""
        try:
            msg = self._queue.get_nowait()
            self._queue.put_nowait(msg)  # put it back
            return msg
        except (queue.Empty, asyncio.QueueEmpty):
            return None

    @property
    def size(self) -> int:
        return self._queue.qsize()

    @property
    def stats(self) -> dict:
        return self._stats.copy()

    def clear(self):
        """Clear the queue."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        self._stats["consumed"] = 0
        self._stats["received"] = 0
        logger.info("[Queue] Cleared")


# Global message queue instance
message_queue = MessageQueue()


# ── AgentContext-aware getter (Phase 1a) ──
def get_message_queue(ctx=None):
    """Return message_queue from AgentContext if available, else global singleton."""
    if ctx is not None:
        return ctx.message_queue
    from opensquad._context import get_current_context

    ctx = get_current_context()
    return ctx.message_queue if ctx is not None else message_queue


# Helper function: convert a raw message to a queue message
def parse_websocket_message(data: dict) -> QueueMessage | None:
    """Parse a WebSocket message into a QueueMessage."""
    try:
        msg_type = data.get("type")
        msg_data = data.get("data", {})

        # Support both "new_message" and "message"
        if msg_type in ["new_message", "message"]:
            # Group chat message
            group_id = msg_data.get("group_id", "unknown_group")
            sender_id = msg_data.get("sender_id", "unknown_user")

            return QueueMessage(
                id=msg_data.get("id", f"msg_{time.time()}"),
                type="group",
                source_id=group_id,
                source_name=msg_data.get("group_name") or f"Group({group_id})",
                sender_id=sender_id,
                sender_name=msg_data.get("sender_name") or f"User({sender_id})",
                content=msg_data.get("content", ""),
                timestamp=msg_data.get("timestamp", time.time()),
                mentions=msg_data.get("mentions", []),
                raw_data=msg_data,
            )
        elif msg_type == "new_direct_message":
            # Direct message
            sender_id = msg_data.get("sender_id", "unknown_user")
            return QueueMessage(
                id=msg_data.get("id", f"dm_{time.time()}"),
                type="dm",
                source_id=sender_id,
                source_name="DM",
                sender_id=sender_id,
                sender_name=msg_data.get("sender_name") or f"User({sender_id})",
                content=msg_data.get("content", ""),
                timestamp=msg_data.get("timestamp", time.time()),
                mentions=[],
                raw_data=msg_data,
            )
        return None
    except Exception as e:
        logger.error(f"[Parse] Failed to parse message: {e}, data={data}")
        return None
