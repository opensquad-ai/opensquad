# -*- coding: utf-8 -*-
"""
Generic asynchronous batch writer.

Replaces the duplicated ``_delayed_save`` / ``_dirty`` / ``_flush_count``
pattern in ``SessionManager`` and ``AIStateManager`` with a single reusable
component.
"""

from __future__ import annotations

import asyncio
import queue
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class AsyncBatchWriter:
    """Queue-based asynchronous batch writer.

    Usage::

        writer = AsyncBatchWriter(
            flush_fn=my_flush,
            flush_interval=2.0,
            batch_size=20,
            name="session_writer",
        )
        writer.start(asyncio.get_running_loop())
        writer.enqueue(lambda: save(data))
        ...
        await writer.stop()
    """

    def __init__(
        self,
        flush_fn: Callable[[], None],
        *,
        flush_interval: float = 2.0,
        batch_size: int = 20,
        name: str = "writer",
    ):
        self._flush_fn = flush_fn
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._name = name

        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the background flush loop."""
        if self._running:
            return
        self._queue = asyncio.Queue()
        self._running = True
        self._task = loop.create_task(self._loop())
        logger.debug("[BatchWriter:%s] Started", self._name)

    def enqueue(self, mutation: Callable[[], None]) -> None:
        """Submit a mutation to the write queue.

        If the queue is full, the mutation is executed synchronously as a
        fallback to avoid blocking the caller.
        """
        if not self._running or self._queue is None:
            mutation()
            self._flush_fn()
            return

        try:
            self._queue.put_nowait(mutation)
            self._count += 1
        except queue.Full:
            mutation()
            self._flush_fn()

    def mark_dirty(self) -> None:
        """Convenience: enqueue a no-op to trigger the next flush interval."""
        if self._queue is not None:
            try:
                self._queue.put_nowait(lambda: None)
            except queue.Full:
                pass

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the writer and flush remaining items."""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        # Flush any final pending mutations
        if self._queue is not None:
            while not self._queue.empty():
                try:
                    mutation = self._queue.get_nowait()
                    mutation()
                except (queue.Empty, queue.Full):
                    break
            self._flush_fn()
        logger.debug("[BatchWriter:%s] Stopped", self._name)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Background flush loop — flushes on interval and on batch size."""
        import time

        while self._running:
            start = time.monotonic()
            try:
                async with asyncio.timeout(self._flush_interval):
                    await self._queue.get()
                    self._count += 1
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

            # Drain queue
            if self._queue is not None:
                while not self._queue.empty() and self._count < self._batch_size:
                    try:
                        mutation = self._queue.get_nowait()
                        mutation()
                        self._count += 1
                    except queue.Empty:
                        break

            if self._count > 0:
                try:
                    self._flush_fn()
                except Exception as exc:
                    logger.error(
                        "[BatchWriter:%s] Flush error: %s", self._name, exc
                    )
                self._count = 0

            # Sleep for remaining interval
            elapsed = time.monotonic() - start
            remaining = self._flush_interval - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
