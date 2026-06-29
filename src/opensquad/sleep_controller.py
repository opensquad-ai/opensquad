# -*- coding: utf-8 -*-
"""
Interruptible sleep controller
- Supports async waiting
- Supports external wakeup
- Supports natural wakeup and interrupted wakeup modes
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, Any
import logging

logger = logging.getLogger(__name__)

class SleepController:
    """
    Manages the AI's sleep state.
    - Non-blocking async sleep
    - Can be woken by any external event
    - Records the wakeup reason
    """
    
    def __init__(self):
        self._sleep_event: Optional[asyncio.Event] = None  # created lazily
        self._wake_reason: Optional[str] = None   # wakeup reason
        self._start_time: Optional[datetime] = None # sleep start time
        self._planned_duration: int = 0           # planned sleep duration
        self._is_sleeping = False
        self._lock = asyncio.Lock()
        
    def _get_event(self) -> asyncio.Event:
        """Get or create Event, ensuring it is in the current event loop."""
        if self._sleep_event is None:
            self._sleep_event = asyncio.Event()
        return self._sleep_event
        
    async def sleep(self, seconds: int, on_wake: Optional[Callable] = None, agent_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Enter sleep state.
        - seconds: planned sleep duration in seconds
        - on_wake: optional wakeup callback

        Returns wakeup info dict:
        {
            "wake_type": "natural" | "interrupted",
            "planned_seconds": int,
            "actual_seconds": float,
            "wake_reason": str,
            "wake_time": str
        }
        """
        async with self._lock:
            self._is_sleeping = True
            self._start_time = datetime.now()
            self._planned_duration = seconds
            self._wake_reason = None
            # Re-create Event to ensure it is in the current loop
            self._sleep_event = asyncio.Event()

        logger.info(f"[Sleep] AI entering sleep for {seconds}s...")

        # Initialize variables
        wake_type = "natural"  # default to natural wakeup
        actual_duration = float(seconds)

        try:
            event_task = asyncio.create_task(self._get_event().wait())

            done, pending = await asyncio.wait(
                [event_task],
                timeout=seconds,
                return_when=asyncio.FIRST_COMPLETED
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if event_task in done:
                wake_type = "interrupted"
                actual_duration = (datetime.now() - self._start_time).total_seconds()
            else:
                wake_type = "natural"
                actual_duration = seconds
                if not self._wake_reason:
                    self._wake_reason = "Sleep duration elapsed"
            
        except Exception as e:
            # Other exceptions
            logger.error(f"[Sleep] Error during sleep: {e}")
            wake_type = "error"
            actual_duration = (datetime.now() - self._start_time).total_seconds() if self._start_time else 0
            if not self._wake_reason:
                self._wake_reason = f"Exception wakeup: {str(e)}"
            
        finally:
            async with self._lock:
                self._is_sleeping = False
            
            # Ensure wake_type has a value
            if wake_type is None:
                wake_type = "unknown"
                actual_duration = seconds
            
            wake_info = {
                "wake_type": wake_type,
                "planned_seconds": seconds,
                "actual_seconds": round(actual_duration, 1),
                "wake_reason": self._wake_reason or "Unknown reason",
                "wake_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # Invoke callback
            if on_wake:
                try:
                    if asyncio.iscoroutinefunction(on_wake):
                        await on_wake(wake_info)
                    else:
                        on_wake(wake_info)
                except Exception as e:
                    logger.error(f"[Sleep] Wake callback error: {e}")
            
            logger.info(f"[Sleep] Wake up! Type: {wake_type}, Reason: {wake_info['wake_reason']}, Actual: {actual_duration:.1f}s")
            return wake_info
    
    def wake_up(self, reason: str) -> bool:
        """Called externally to wake up the controller."""
        if self._is_sleeping and self._sleep_event:
            self._wake_reason = reason
            self._sleep_event.set()
            logger.info(f"[Sleep] Wake signal received: {reason}")
            return True
        logger.debug(f"[Sleep] Wake signal ignored (not sleeping)")
        return False
    
    def is_sleeping(self) -> bool:
        """Check whether currently sleeping."""
        return self._is_sleeping
    
    def get_remaining_seconds(self) -> float:
        """Get the remaining sleep duration in seconds."""
        if not self._is_sleeping or not self._start_time:
            return 0
        elapsed = (datetime.now() - self._start_time).total_seconds()
        remaining = max(0, self._planned_duration - elapsed)
        return round(remaining, 1)
    
    def get_sleep_info(self) -> Dict[str, Any]:
        """Get current sleep state information."""
        return {
            "is_sleeping": self._is_sleeping,
            "planned_duration": self._planned_duration,
            "remaining_seconds": self.get_remaining_seconds(),
            "start_time": self._start_time.strftime("%Y-%m-%d %H:%M:%S") if self._start_time else None
        }

# Global singleton
sleep_controller = SleepController()


# ── AgentContext-aware getter (Phase 1a) ──
def get_sleep_controller(ctx=None):
    """Return sleep_controller from AgentContext if available, else global singleton."""
    if ctx is not None:
        return ctx.sleep_controller
    from opensquad._context import get_current_context
    ctx = get_current_context()
    return ctx.sleep_controller if ctx is not None else sleep_controller
