# -*- coding: utf-8 -*-
"""
Enhanced EventBus with subscriber tracking (P1-1).

Changes from V3.2:
  - ``subscribe(event_type, callback, owner=None)`` now returns a ``subscriber_id``.
  - ``subscribe_once(event_type, callback, owner=None)`` fires the callback once then auto-removes it.
  - ``unsubscribe_by_id(subscriber_id)`` removes a subscription by its ID.
  - ``unsubscribe_owner(owner)`` removes all subscriptions for an owner tag.
  - ``get_subscribers(event_type)`` returns the list of (id, callback) pairs for inspection.
  - Original ``unsubscribe(event_type, callback)`` retained for backward compatibility.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
import weakref
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Callable, Dict, List, Optional

from opensquad._events.payloads import EVENT_PAYLOADS

logger = logging.getLogger(__name__)


@dataclass
class SubscriberInfo:
    """Metadata for a single subscription entry."""
    id: str
    callback: Any = None  # Direct callback (only for non-weakref lambda fallback)
    owner: Optional[str] = None  # Arbitrary tag for bulk removal
    callback_ref: Any = None  # weakref.ref or None for strong reference (lambda fallback)


class EventBus:
    """
    V3.3 Async event bus (thread-safe) with subscriber tracking.

    Supports:
      - Publishing events from any thread with execution in the designated event loop
      - Subscriber IDs returned on subscribe for clean removal
      - One-time subscriptions (subscribe_once)
      - Owner-based bulk removal
    """

    def __init__(self):
        # Original: event_type -> list of SubscriberInfo
        self._subscribers: Dict[str, List[SubscriberInfo]] = {}
        # New: subscriber_id -> SubscriberInfo (for O(1) removal)
        self._by_id: Dict[str, SubscriberInfo] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        # Track background tasks to avoid fire-and-forget losing exceptions
        self._background_tasks: set[asyncio.Task] = set()
        # Guards _subscribers / _by_id against concurrent subscribe/unsubscribe/emit
        # from multiple threads (e.g. WebSocket receive thread + main loop).
        self._lock = threading.RLock()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the main event loop."""
        self._loop = loop

    # --------------------------------------------------------------------------
    # Subscribe (enhanced)
    # --------------------------------------------------------------------------

    def subscribe(
        self,
        event_type: str,
        callback: Callable,
        owner: str | None = None,
    ) -> str:
        """
        Subscribe to an event type.

        Uses weakref to manage subscriber lifecycle so that subscribers that
        go out of scope are automatically cleaned up on next emit():
        - For bound methods: weakref to the owning object (``__self__``).
        - For regular functions/inner functions: weakref to the function.
        - For lambdas and C callables (no weakref support): strong reference
          with a debug log warning.

        Args:
            event_type: The event name to listen for.
            callback:   Callable that will receive ``(data)``. Can be sync or async.
            owner:     Optional owner tag for bulk removal via ``unsubscribe_owner``.

        Returns:
            A ``subscriber_id`` string. Pass this to ``unsubscribe_by_id`` to remove.
        """
        sub_id = uuid.uuid4().hex[:16]

        # Determine the best weakref target:
        # 1. Bound methods: weakref self (the owning object)
        # 2. Regular functions: weakref the function itself
        # 3. Lambdas/unbound callables: fallback to strong ref
        callback_ref = None
        bound_self = None
        bound_func = None
        if hasattr(callback, '__self__'):
            # Bound method — weakref the owning object
            try:
                bound_self = weakref.ref(callback.__self__)
                bound_func = callback.__func__
            except TypeError:
                pass
        if bound_self is None:
            try:
                callback_ref = weakref.ref(callback)
            except TypeError:
                logger.debug(
                    "[EventBus] Callback type %s does not support weakref, "
                    "using strong reference (sub_id=%s)",
                    type(callback).__name__, sub_id,
                )

        # When weakref is used, do NOT store a strong ref to callback.
        # The strong ref would prevent GC of bound methods/objects.
        if bound_self is not None:
            info = SubscriberInfo(
                id=sub_id, callback=None, owner=owner,
            )
            # Store bound-method specific data in callback_ref slot
            # Tuple: (self_ref, func) — NO strong ref to the bound method
            info.callback_ref = (bound_self, bound_func)
        elif callback_ref is not None:
            info = SubscriberInfo(
                id=sub_id, callback=None, owner=owner,
                callback_ref=callback_ref,
            )
        else:
            info = SubscriberInfo(
                id=sub_id, callback=callback, owner=owner,
                callback_ref=None,
            )

        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(info)
            self._by_id[sub_id] = info

        logger.debug("[EventBus] Subscribed %s to [%s] (id=%s)", owner or "?", event_type, sub_id)
        return sub_id

    def subscribe_once(
        self,
        event_type: str,
        callback: Callable,
        owner: str | None = None,
    ) -> str:
        """
        Subscribe to an event type for exactly one emission, then auto-remove.

        Args:
            event_type: The event name to listen for.
            callback:   Callable that will receive ``(data)``. Can be sync or async.
            owner:      Optional owner tag.

        Returns:
            A ``subscriber_id`` string.
        """
        sub_id = uuid.uuid4().hex[:16]

        async def _wrapper(data: Any) -> None:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            finally:
                self.unsubscribe_by_id(sub_id)

        # IMPORTANT: _wrapper is a local closure and would be garbage-collected
        # as soon as subscribe_once returns if we only held a weakref to it.
        # That would make the one-shot callback never fire. Keep a STRONG
        # reference via info.callback instead.
        info = SubscriberInfo(
            id=sub_id, callback=_wrapper, owner=owner,
            callback_ref=None,
        )
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(info)
            self._by_id[sub_id] = info

        return sub_id

    # --------------------------------------------------------------------------
    # Unsubscribe variants
    # --------------------------------------------------------------------------

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """
        Remove a specific callback from an event type (original API, backward compat).

        Args:
            event_type: Event name.
            callback:   The exact callback reference to remove.
        """
        if event_type not in self._subscribers:
            return
        removed = False
        for info in self._subscribers[event_type]:
            # Direct comparison (lambda fallback)
            cb = info.callback
            if cb is not None and cb == callback:
                self._by_id.pop(info.id, None)
                self._subscribers[event_type].remove(info)
                removed = True
                break
            # Bound method tuple: compare the original bound method
            if isinstance(info.callback_ref, tuple):
                self_ref, func = info.callback_ref
                obj = self_ref()
                if obj is not None:
                    bound = func.__get__(obj, type(obj))
                    if bound == callback:
                        self._by_id.pop(info.id, None)
                        self._subscribers[event_type].remove(info)
                        removed = True
                        break
            # Weakref to callable
            if info.callback_ref is not None and not isinstance(info.callback_ref, tuple):
                resolved = info.callback_ref()
                if resolved is not None and resolved == callback:
                    self._by_id.pop(info.id, None)
                    self._subscribers[event_type].remove(info)
                    removed = True
                    break
        if removed:
            logger.debug("[EventBus] Unsubscribed from [%s]", event_type)

    def unsubscribe_by_id(self, subscriber_id: str) -> bool:
        """
        Remove a subscription by its ID.

        Args:
            subscriber_id: The ID returned by ``subscribe``.

        Returns:
            True if the subscription was found and removed, False otherwise.
        """
        with self._lock:
            info = self._by_id.pop(subscriber_id, None)
            if info is None:
                return False

            event_type = None
            for etype, lst in self._subscribers.items():
                if info in lst:
                    lst.remove(info)
                    event_type = etype
                    break

        logger.debug(
            "[EventBus] Unsubscribed id=%s from [%s]",
            subscriber_id,
            event_type or "?",
        )
        return True

    def unsubscribe_owner(self, owner: str) -> int:
        """
        Remove ALL subscriptions belonging to a given owner tag.

        Args:
            owner: The owner tag passed to ``subscribe`` / ``subscribe_once``.

        Returns:
            The number of subscriptions removed.
        """
        with self._lock:
            to_remove: List[str] = [
                sid for sid, info in self._by_id.items() if info.owner == owner
            ]
        for sid in to_remove:
            self.unsubscribe_by_id(sid)
        if to_remove:
            logger.debug("[EventBus] Unsubscribed %d items for owner=%s", len(to_remove), owner)
        return len(to_remove)

    # --------------------------------------------------------------------------
    # Inspection
    # --------------------------------------------------------------------------

    def get_subscribers(self, event_type: str) -> List[tuple[str, Callable]]:
        """
        Return list of (subscriber_id, callback) pairs for an event type.

        Useful for debugging and introspection. GC'd subscribers are excluded.
        """
        lst = self._subscribers.get(event_type, [])
        result: List[tuple[str, Callable]] = []
        for info in lst:
            resolved = self._resolve_callback(info)
            if resolved is not None:
                result.append((info.id, resolved))
        return result

    # --------------------------------------------------------------------------
    # Background task tracking
    # --------------------------------------------------------------------------

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Done callback for tracked background tasks — log exceptions and discard."""
        self._background_tasks.discard(task)
        try:
            exc = task.exception()
            if exc:
                logger.error("[EventBus] Background task failed: %s", exc)
        except asyncio.CancelledError:
            pass  # Cancellation is expected during shutdown

    # --------------------------------------------------------------------------
    # Weakref helpers
    # --------------------------------------------------------------------------

    def _resolve_callback(self, info: SubscriberInfo) -> Callable | None:
        """Resolve a subscriber's callback through weakref, auto-cleaning if GC'd.

        Returns the callable, or None if the subscriber was garbage collected.
        """
        # Bound method: callback_ref is (self_ref, func)
        if isinstance(info.callback_ref, tuple):
            self_ref, func = info.callback_ref
            obj = self_ref()
            if obj is None:
                self._auto_cleanup(info)
                return None
            return func.__get__(obj, type(obj))
        # Weakref to function/callable
        if info.callback_ref is not None:
            resolved = info.callback_ref()
            if resolved is None:
                self._auto_cleanup(info)
                return None
            return resolved
        # Strong reference fallback
        return info.callback

    def _auto_cleanup(self, info: SubscriberInfo) -> None:
        """Remove a subscriber whose callback has been garbage collected."""
        self._by_id.pop(info.id, None)
        for etype, lst in self._subscribers.items():
            if info in lst:
                lst.remove(info)
                logger.debug(
                    "[EventBus] Auto-cleaned GC'd subscriber id=%s from [%s]",
                    info.id, etype,
                )
                break

    # --------------------------------------------------------------------------
    # Emit
    # --------------------------------------------------------------------------

    def emit(self, event_type: str, data: Any) -> None:
        """Thread-safe synchronous publish interface.

        If *data* is a dataclass instance registered in ``EVENT_PAYLOADS``,
        it is automatically converted to a dict and validated against the
        expected type::

            bus.emit("agent_ready", AgentReadyEvent(agent_id="x"))
        """
        # Auto-convert dataclass payloads to dict
        if is_dataclass(data) and not isinstance(data, type):
            payload_cls = EVENT_PAYLOADS.get(event_type)
            if payload_cls is not None and not isinstance(data, payload_cls):
                raise TypeError(
                    f"Event '{event_type}' expects {payload_cls.__name__}, "
                    f"got {type(data).__name__}"
                )
            data = asdict(data)

        # Snapshot the subscriber list under the lock so concurrent
        # subscribe/unsubscribe from another thread cannot mutate it while we
        # iterate. The actual callback dispatch happens outside the lock.
        with self._lock:
            snapshot = list(self._subscribers.get(event_type, []))
        if not snapshot:
            return

        def _run_handlers() -> None:
            for info in snapshot:
                try:
                    # Resolve weakref: if the subscriber was GC'd, auto-clean
                    resolved = self._resolve_callback(info)
                    if resolved is None:
                        continue

                    if asyncio.iscoroutinefunction(resolved):
                        task = asyncio.create_task(resolved(data))
                        # Track task: discard on completion, log exceptions
                        self._background_tasks.add(task)
                        task.add_done_callback(self._on_task_done)
                    else:
                        resolved(data)
                except Exception as e:
                    logger.error("[EventBus] Event error [%s]: %s", event_type, e)

        if self._loop and self._loop.is_running():
            try:
                current_loop = asyncio.get_running_loop()
                if current_loop == self._loop:
                    _run_handlers()
                else:
                    self._loop.call_soon_threadsafe(_run_handlers)
            except RuntimeError:
                self._loop.call_soon_threadsafe(_run_handlers)
        else:
            _run_handlers()

    async def emit_async(self, event_type: str, data: Any) -> None:
        """Async publish interface."""
        self.emit(event_type, data)

    # ------------------------------------------------------------------
    # Typed emit (Fix 3: EventBus event type safety)
    # ------------------------------------------------------------------

    def emit_typed(self, event_type: str, payload: Any) -> None:
        """Emit an event with a typed payload.

        If *event_type* is registered in ``EVENT_PAYLOADS``, the payload
        is validated against the expected dataclass type and automatically
        converted to a dict before dispatch.

        Args:
            event_type: The event name.
            payload: A dataclass instance matching the registered type,
                     or a raw dict (passed through unchanged for unregistered types).

        Raises:
            TypeError: If the payload type does not match the registered schema.
        """
        payload_cls = EVENT_PAYLOADS.get(event_type)
        if payload_cls is not None:
            if not isinstance(payload, payload_cls):
                raise TypeError(
                    f"Event '{event_type}' expects {payload_cls.__name__}, "
                    f"got {type(payload).__name__}"
                )
            if hasattr(payload, "__dataclass_fields__"):
                payload = payload.__dict__
        self.emit(event_type, payload)

    async def emit_typed_async(self, event_type: str, payload: Any) -> None:
        """Async variant of ``emit_typed``."""
        self.emit_typed(event_type, payload)

    def on(self, payload_cls: type, callback: Callable) -> str:
        """Type-safe subscription: register a callback for an event type derived from *payload_cls*.

        The event type is looked up from ``EVENT_PAYLOADS`` by payload class,
        so there is no risk of string-typo mismatches::

            bus.on(ToolCallEvent, my_handler)

        Is equivalent to::

            bus.subscribe("tool_call", my_handler)

        Returns the same subscriber ID as ``subscribe``.
        """
        for event_type, cls in EVENT_PAYLOADS.items():
            if cls is payload_cls:
                return self.subscribe(event_type, callback)
        raise KeyError(
            f"Payload class {payload_cls.__name__} is not registered in EVENT_PAYLOADS. "
            f"Available: {[c.__name__ for c in EVENT_PAYLOADS.values()]}"
        )


# Global singleton (unchanged API)
bus = EventBus()


# ── AgentContext-aware getter (Phase 1a) ──
def get_event_bus(ctx=None):
    """Return EventBus from AgentContext if available, else global singleton."""
    if ctx is not None:
        return ctx.event_bus
    from opensquad._context import get_current_context
    ctx = get_current_context()
    return ctx.event_bus if ctx is not None else bus
