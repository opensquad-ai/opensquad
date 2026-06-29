# -*- coding: utf-8 -*-
"""
AI State Manager - Persistence and Sharing
"""
import json
import os
import shutil
import logging
import time as _time
from datetime import datetime
from typing import Optional, Dict, Any, Callable, List
import asyncio

logger = logging.getLogger(__name__)

class AIStateManager:
    """
    Manages the AI's autonomous state.
    - Persisted to file (root: ai_state.json)
    - Retains historical backup (ai_state.json.bak)
    - Thread-safe
    - Supports listener pattern
    - Batched writes: changes accumulate and flush to disk on interval or count threshold
    """

    _DEFAULT_FLUSH_INTERVAL = 2.0    # seconds between disk flushes
    _DEFAULT_FLUSH_COUNT = 3         # flush after this many dirty state changes

    def __init__(self, state_file: str = "ai_state.json", flush_interval: float = _DEFAULT_FLUSH_INTERVAL, flush_count: int = _DEFAULT_FLUSH_COUNT):
        self.state_file = state_file
        self.backup_file = state_file + ".bak"
        self._state = {
            "ai_state": "idle",           # idle | working | sleeping
            "wake_mode": "strict",        # strict | normal
            "sleep_end_time": None,       # ISO format time or None
            "last_updated": None,         # last updated time
            "version": 1
        }
        self._lock: Optional[asyncio.Lock] = None  # lazily created
        self._listeners: List[Callable] = []  # state change listeners

        # Batched write state
        self._flush_interval = flush_interval
        self._flush_count = flush_count
        self._dirty = False
        self._dirty_count = 0
        self._last_save_time = 0.0
        self._pending_save_task: Optional[asyncio.Task] = None
        self._pending_save_lock = asyncio.Lock()

        # Load on startup
        self._load()

    def _get_lock(self) -> asyncio.Lock:
        """Get or create Lock (lazily, ensuring it is in the current event loop)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _backup(self):
        """Back up the current state file."""
        if os.path.exists(self.state_file):
            try:
                shutil.copy2(self.state_file, self.backup_file)
                logger.info(f"[StateManager] Backup created: {self.backup_file}")
            except Exception as e:
                logger.error(f"[StateManager] Backup failed: {e}")

    def _load(self):
        """Load state from file."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    self._state.update(loaded)
                    logger.info(f"[StateManager] Loaded state: ai_state={self._state['ai_state']}, wake_mode={self._state['wake_mode']}")
            except Exception as e:
                logger.error(f"[StateManager] Failed to load: {e}, using defaults")
                if os.path.exists(self.backup_file):
                    try:
                        with open(self.backup_file, 'r', encoding='utf-8') as f:
                            loaded = json.load(f)
                            self._state.update(loaded)
                            logger.info(f"[StateManager] Restored from backup")
                    except Exception as e2:
                        logger.error(f"[StateManager] Backup also failed: {e2}")

    def _sync_flush(self):
        """Synchronous flush — performs actual I/O. Called via run_in_executor."""
        from opensquad.structured_log import perf_event
        t0 = _time.perf_counter()
        self._backup()
        self._state["last_updated"] = datetime.now().isoformat()
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
            flush_ms = int((_time.perf_counter() - t0) * 1000)
            if flush_ms > 50:
                logger.info(f"[StateManager] State saved: {self._state['ai_state']} (flush_ms={flush_ms})")
            perf_event("state_manager", "flush", elapsed_ms=flush_ms, dirty_count=self._dirty_count)
        except Exception as e:
            logger.error(f"[StateManager] Failed to save: {e}")

    async def _flush(self):
        """Write state to disk via run_in_executor (non-blocking).
        The caller is responsible for lock management:
        - Called WITH lock held from synchronous save paths (e.g. _immediate_save)
        - Called WITHOUT lock by _delayed_save_task (which acquires lock internally)
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_flush)

    async def _delayed_save(self):
        """Background task: waits for interval/count threshold then flushes."""
        async with self._pending_save_lock:
            if self._pending_save_task is not None:
                return  # already scheduled

        elapsed = _time.perf_counter() - self._last_save_time
        wait_time = max(0.0, self._flush_interval - elapsed)
        await asyncio.sleep(wait_time)

        async with self._pending_save_lock:
            self._pending_save_task = None

        async with self._get_lock():
            if self._dirty:
                self._dirty = False
                self._dirty_count = 0
                self._last_save_time = _time.perf_counter()
                await self._flush()

    def _schedule_save(self):
        """Schedule a delayed disk flush if not already pending."""
        if self._pending_save_task is not None and not self._pending_save_task.done():
            return
        self._pending_save_task = asyncio.create_task(self._delayed_save())

    async def _save(self):
        """Internal save: marks dirty and schedules async flush. Caller holds lock."""
        self._dirty = True
        self._dirty_count += 1
        self._schedule_save()

    async def get_state(self) -> str:
        """Get the current state."""
        async with self._get_lock():
            return self._state["ai_state"]

    async def set_state(self, new_state: str):
        """Set a new state."""
        if new_state not in ["idle", "working", "sleeping"]:
            logger.warning(f"[StateManager] Invalid state: {new_state}")
            return

        async with self._get_lock():
            old_state = self._state["ai_state"]
            if old_state != new_state:
                self._state["ai_state"] = new_state
                await self._save()

                # Notify listeners
                for listener in self._listeners:
                    try:
                        if asyncio.iscoroutinefunction(listener):
                            await listener(old_state, new_state)
                        else:
                            listener(old_state, new_state)
                    except Exception as e:
                        logger.error(f"[StateManager] Listener error: {e}")

                logger.info(f"[StateManager] State changed: {old_state} -> {new_state}")

    async def get_wake_mode(self) -> str:
        """Get the wakeup mode."""
        async with self._get_lock():
            return self._state["wake_mode"]

    async def set_wake_mode(self, mode: str):
        """Set a new wake mode."""
        if mode not in ["strict", "normal"]:
            logger.warning(f"[StateManager] Invalid wake_mode: {mode}")
            return

        async with self._get_lock():
            old_mode = self._state["wake_mode"]
            if old_mode != mode:
                self._state["wake_mode"] = mode
                await self._save()
                logger.info(f"[StateManager] Wake mode changed: {old_mode} -> {mode}")

    async def set_sleep_end(self, end_time: Optional[datetime]):
        """Set the sleep end time."""
        async with self._get_lock():
            if end_time:
                self._state["sleep_end_time"] = end_time.isoformat()
            else:
                self._state["sleep_end_time"] = None
            await self._save()

    async def get_sleep_end(self) -> Optional[datetime]:
        """Get the sleep end time."""
        async with self._get_lock():
            end_str = self._state.get("sleep_end_time")
            if end_str:
                try:
                    return datetime.fromisoformat(end_str)
                except (ValueError, TypeError):
                    return None
            return None

    def add_listener(self, callback: Callable):
        """Add a state change listener."""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable):
        """Remove a listener."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    async def is_sleeping(self) -> bool:
        """Check whether currently sleeping."""
        return await self.get_state() == "sleeping"

    def get_full_state(self) -> Dict[str, Any]:
        """Get the full state dict (for API responses)."""
        return self._state.copy()

    async def flush_sync(self):
        """Force an immediate synchronous flush (call on shutdown)."""
        if self._pending_save_task and not self._pending_save_task.done():
            self._pending_save_task.cancel()
            try:
                await self._pending_save_task
            except asyncio.CancelledError:
                pass
        async with self._get_lock():
            if self._dirty:
                self._dirty = False
                self._dirty_count = 0
                self._last_save_time = _time.perf_counter()
                await self._flush()

# Global singleton
import logging
state_manager = AIStateManager()


# ── AgentContext-aware getter (Phase 1a) ──
def get_state_manager(ctx=None):
    """Return state_manager from AgentContext if available, else global singleton."""
    if ctx is not None:
        return ctx.state_manager
    from opensquad._context import get_current_context
    ctx = get_current_context()
    return ctx.state_manager if ctx is not None else state_manager


def reinit_state_manager(state_file: str):
    """Re-initialize the global singleton pointing to a new state file (for multi-agent isolation)."""
    global state_manager
    state_manager = AIStateManager(state_file=state_file)
    logger.info(f"[StateManager] Reinitialized with state_file: {state_file}")
    return state_manager
