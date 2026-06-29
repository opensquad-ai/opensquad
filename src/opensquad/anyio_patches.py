# -*- coding: utf-8 -*-
"""
anyio_patches.py — Global monkey-patch for anyio CancelScope on Python 3.12+

ROOT CAUSE (Python 3.12+ + anyio 4.x):
  anyio's CancelScope._deliver_cancellation() schedules itself via
  call_soon() and repeatedly calls task.cancel() on every task inside
  the scope. The internal `should_retry` flag is always True as long as
  _tasks is non-empty, causing infinite cancellation delivery cycles.

  When a CancelledError propagates OUTSIDE the anyio with-block (e.g.,
  the runner's asyncio.wait() call), anyio's __exit__ cannot properly
  call task.uncancel(), so Python 3.12+'s _num_cancels_requested counter
  stays > 0 → every subsequent await re-raises CancelledError → infinite
  loop → 100% CPU → agent restart loop.

FIX:
  Monkey-patch CancelScope._deliver_cancellation to add a per-scope
  _cancelled_task_ids set. Each task is cancelled at most ONCE per
  scope lifecycle. `should_retry` is only True when a NEW cancellation
  was actually delivered, breaking the infinite re-scheduling loop.

  Also patch CancelScope.__exit__ to aggressively drain uncancel
  on the host task after scope exit, preventing counter leaks.

Usage:
  Import and call apply() at the earliest point in the agent boot process,
  BEFORE any MCP adapter or other anyio-using code is imported.

  from opensquad.anyio_patches import apply
  apply()
"""

import asyncio
import sys


_patch_applied = False


def apply() -> None:
    """Apply all anyio patches once. Idempotent."""
    global _patch_applied
    if _patch_applied:
        return

    # Only needed on Python 3.12+ (3.12 introduced task.uncancel / _num_cancels_requested)
    if sys.version_info < (3, 12):
        _patch_applied = True
        return

    try:
        import anyio._backends._asyncio as _ba
    except ImportError:
        # anyio not installed — nothing to patch
        _patch_applied = True
        return

    # The infinite-cancellation-delivery bug and the (self, origin) calling
    # convention exist only in anyio 4.x. anyio 3.x uses a different
    # _deliver_cancellation(self) implementation with a _cancel_calls counter
    # and does NOT suffer from that bug. Applying the 4.x patch (which expects
    # an `origin` argument and 4.x-only attributes like _pending_uncancellations)
    # onto 3.x makes every call raise
    #   TypeError: _patched_deliver() missing 1 required positional argument: 'origin'
    # inside anyio's cancellation path. httpx runs there during request
    # teardown/timeout, so the TypeError surfaces as APIConnectionError and
    # breaks every LLM call. Skip the patch entirely on 3.x.
    try:
        import importlib.metadata as _md
        _anyio_version = tuple(int(p) for p in _md.version("anyio").split(".")[:2])
    except Exception:
        _anyio_version = (0, 0)
    if _anyio_version < (4, 0):
        _patch_applied = True
        return

    _patch_deliver_cancellation(_ba)
    _patch_exit(_ba)

    _patch_applied = True


def _patch_deliver_cancellation(_ba) -> None:
    """Patch CancelScope._deliver_cancellation to add per-task-once dedup."""

    _orig_deliver = _ba.CancelScope._deliver_cancellation

    def _patched_deliver(self, origin):
        # Initialize the dedup set lazily (once per scope lifecycle)
        if not hasattr(self, '_cancelled_task_ids'):
            self._cancelled_task_ids = set()

        current = _ba.current_task()
        did_new_cancel = False

        for task in self._tasks:
            if task._must_cancel:  # type: ignore[attr-defined]
                continue

            if task is not current and (task is self._host_task or _ba._task_started(task)):
                waiter = task._fut_waiter  # type: ignore[attr-defined]
                if not isinstance(waiter, asyncio.Future) or not waiter.done():
                    # Only cancel each task ONCE per scope lifecycle
                    tid = id(task)
                    if tid not in self._cancelled_task_ids:
                        self._cancelled_task_ids.add(tid)
                        task.cancel(f"Cancelled by cancel scope {id(origin):x}")
                        did_new_cancel = True
                        if (
                            task is origin._host_task
                            and origin._pending_uncancellations is not None
                        ):
                            origin._pending_uncancellations += 1

        for scope in self._child_scopes:
            if not scope._shield and not scope.cancel_called:
                if scope._deliver_cancellation(origin):
                    did_new_cancel = True

        # Only re-schedule if we actually delivered a NEW cancellation
        if origin is self:
            if did_new_cancel and self._active:
                self._cancel_handle = _ba.get_running_loop().call_soon(
                    self._deliver_cancellation, origin
                )
            else:
                self._cancel_handle = None

        return did_new_cancel

    _ba.CancelScope._deliver_cancellation = _patched_deliver


def _patch_exit(_ba) -> None:
    """Patch CancelScope.__exit__ to aggressively drain uncancel on host task."""

    _orig_exit = _ba.CancelScope.__exit__

    def _patched_exit(self, exc_type, exc_val, exc_tb):
        host_task = self._host_task  # save before __exit__ clears it
        result = _orig_exit(self, exc_type, exc_val, exc_tb)

        # After scope exit, drain any remaining uncancel on the host task
        # (Python 3.12+). This catches cases where cancellations were
        # delivered but the matching __exit__ cleanup didn't fully drain.
        try:
            if host_task is not None and hasattr(host_task, 'uncancel'):
                while host_task.uncancel() > 0:
                    pass
        except Exception:
            pass

        return result

    _ba.CancelScope.__exit__ = _patched_exit
