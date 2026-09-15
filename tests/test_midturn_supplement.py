"""Regression locks: force-sent messages must reach a running parallel turn.

Bug (2026-09-13 user report): a message sent (or force-sent via 立即发送) while
the agent's workflow was mid-turn never reached the model — not on the next
tool round, and not after the flow ended. Root causes:

1. `_parallel_session_turn` had NO mid-turn supplement checkpoint (serial mode
   had one; parallel did not) — the message sat in the dispatcher's
   pop→busy→re-push cycle until the whole turn ended. On an autonomous loop
   (news2theme_agent turn=189+) that is effectively forever, and an agent
   restart silently drops the in-memory queue (observed in agent.log:
   the same message re-popped 5+ times then the process rebooted).
2. The dispatcher's busy path busy-polled at 50ms, emitting 2 WS frames per
   cycle (busy_sessions + status) — a frame storm for the whole turn.

Fixes locked here:
- `ParallelTurnScheduler.wait_session_free` (event-based handoff, no polling).
- `AgentRunner._drain_parallel_session_supplements` → event_pipeline bucket.
- dispatcher busy path waits on the scheduler event instead of sleep(0.05).
- `_turn_loop` per-tool pipeline drain prefers the per-coroutine TurnLocal sid
  (shared `runner._turn_sid` races across concurrent parallel turns).
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from opensquad.event_pipeline import event_pipeline
from opensquad.input_hub import InputHub
from opensquad.session_parallel import ParallelTurnScheduler

APP_ROOT = Path(__file__).resolve().parents[1] / "src" / "opensquad"
DISPATCHER_SRC = (APP_ROOT / "session_dispatcher.py").read_text(encoding="utf-8")
RUNNER_SRC = (APP_ROOT / "runner.py").read_text(encoding="utf-8")
TURN_LOOP_SRC = (APP_ROOT / "_runner" / "_turn_loop.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Behavioural: ParallelTurnScheduler.wait_session_free
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_session_free_returns_immediately_when_free():
    sched = ParallelTurnScheduler(max_parallel=2)
    assert await sched.wait_session_free("s1", timeout=0.1) is True


@pytest.mark.asyncio
async def test_wait_session_free_blocks_until_finish_signals():
    sched = ParallelTurnScheduler(max_parallel=2)
    hold = asyncio.Event()

    async def linger():
        await hold.wait()

    assert await sched.acquire_slot("s1")
    sched.start("s1", linger())
    assert sched.is_session_busy("s1")

    async def waiter():
        return await sched.wait_session_free("s1", timeout=2.0)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    assert not task.done(), "waiter must still be waiting while the turn runs"
    sched.finish("s1")
    assert await asyncio.wait_for(task, timeout=1.0) is True
    hold.set()


@pytest.mark.asyncio
async def test_wait_session_free_wakes_on_reap_of_finished_task():
    sched = ParallelTurnScheduler(max_parallel=2)

    async def quick():
        await asyncio.sleep(0.02)

    assert await sched.acquire_slot("s1")
    sched.start("s1", quick())
    task = asyncio.create_task(sched.wait_session_free("s1", timeout=2.0))
    # The turn ends by itself; reap() (called inside wait_session_free or the
    # dispatcher loop) must release the waiter even without finish().
    assert await asyncio.wait_for(task, timeout=1.0) is True
    assert not sched.is_session_busy("s1")


@pytest.mark.asyncio
async def test_wait_session_free_times_out_while_turn_lingers():
    sched = ParallelTurnScheduler(max_parallel=2)
    hold = asyncio.Event()

    async def linger():
        await hold.wait()

    assert await sched.acquire_slot("s1")
    sched.start("s1", linger())
    assert await sched.wait_session_free("s1", timeout=0.05) is False
    hold.set()
    await asyncio.sleep(0.01)
    sched.reap()


# --------------------------------------------------------------------------
# Behavioural: the supplement drain itself (runner method, no full Runner boot)
# --------------------------------------------------------------------------


def _bare_runner():
    """An AgentRunner with __init__ skipped — the drain method only touches
    TurnLocal-backed media lists, the hub and the pipeline."""
    from opensquad.runner import AgentRunner
    from opensquad.session_parallel import TurnLocal

    r = object.__new__(AgentRunner)
    r._root_tl = TurnLocal()  # _current_images/_current_attachments are @property over this
    r._current_images = []
    r._current_attachments = []
    return r


@pytest.mark.asyncio
async def test_supplement_drain_pushes_session_text_into_pipeline(monkeypatch):
    import opensquad.runner as runner_mod

    hub = InputHub()
    monkeypatch.setattr(runner_mod, "input_hub", hub)
    event_pipeline.drain_sync(session_id="sid-x")  # start from a clean bucket

    hub.push("帮我看下这个报错", source="gateway", session_id="sid-x", channel="web")
    hub.push("__SYSTEM_SENTINEL__", source="system", session_id="sid-x")
    hub.push("[wakeup-urgent-command]", source="web", session_id="sid-x")

    runner = _bare_runner()
    runner._current_images = []
    runner._current_attachments = []

    pushed = runner._drain_parallel_session_supplements("sid-x")

    assert pushed == 1, "only the real user message may be injected"
    events = event_pipeline.drain_sync(session_id="sid-x")
    assert len(events) == 1
    assert "帮我看下这个报错" in events[0].content
    # The hub must be drained — the dispatcher must not re-deliver the item.
    assert hub.get_session_pending("sid-x") == []


@pytest.mark.asyncio
async def test_supplement_drain_does_not_touch_other_session_buckets(monkeypatch):
    import opensquad.runner as runner_mod

    hub = InputHub()
    monkeypatch.setattr(runner_mod, "input_hub", hub)
    event_pipeline.drain_sync(session_id="sid-a")
    event_pipeline.drain_sync(session_id="sid-b")

    hub.push("message for A", source="gateway", session_id="sid-a", channel="web")

    runner = _bare_runner()
    runner._current_images = []
    runner._current_attachments = []
    assert runner._drain_parallel_session_supplements("sid-b") == 0
    assert event_pipeline.size_for(session_id="sid-b") == 0
    # A's item stays queued for A's own turn.
    assert hub.peek_session_pending("sid-a") is True


@pytest.mark.asyncio
async def test_supplement_drain_carries_images(monkeypatch):
    import opensquad.runner as runner_mod

    hub = InputHub()
    monkeypatch.setattr(runner_mod, "input_hub", hub)
    event_pipeline.drain_sync(session_id="sid-img")

    hub.push("看这张图", source="gateway", session_id="sid-img", images=["/tmp/pic.png"])

    runner = _bare_runner()
    runner._current_images = []
    runner._current_attachments = []
    assert runner._drain_parallel_session_supplements("sid-img") == 1
    assert runner._current_images == ["/tmp/pic.png"]


# --------------------------------------------------------------------------
# Source-level locks (the wiring itself)
# --------------------------------------------------------------------------


def test_parallel_turn_loop_calls_the_supplement_drain():
    # Inside the turn loop, after the stop-check and before turn bookkeeping —
    # i.e. between tool rounds, not only at turn start.
    assert "_drain_parallel_session_supplements(sid)" in RUNNER_SRC
    assert "if turn > 0:" in RUNNER_SRC


def test_supplement_drain_targets_the_event_pipeline_with_session_id():
    needle = "event_pipeline.push_nowait("
    assert needle in RUNNER_SRC
    # The push must be session-scoped, or parallel panes cross-drain.
    block = RUNNER_SRC[RUNNER_SRC.index("def _drain_parallel_session_supplements") :]
    block = block[: block.index("async def _parallel_session_turn")]
    assert "session_id=sid" in block
    # Serial-parity guards: sentinels and system markers are not user input.
    assert "[wakeup-urgent-command]" in block
    assert 'content.startswith("__")' in block


def test_dispatcher_busy_path_waits_on_scheduler_event_not_sleep_polling():
    busy_block = DISPATCHER_SRC[DISPATCHER_SRC.index("Same session already running") :]
    busy_block = busy_block[: busy_block.index("Acquire parallel slot")]
    assert "wait_session_free" in busy_block, "busy path must wait on the scheduler event"
    assert "asyncio.sleep(0.05)" not in busy_block, "50ms busy-polling must be gone from the busy path"
    # The re-push must preserve the pane's model card (it used to be dropped).
    assert "model_card" in busy_block


def test_turn_loop_drain_prefers_turnlocal_sid_over_shared_attr():
    assert "get_turn_local()" in TURN_LOOP_SRC
    assert "_tool_sid" in TURN_LOOP_SRC
    # Scope to the drain block: the shared attr is used elsewhere in the file
    # (titles, event persistence) — this lock is about the pipeline drain.
    drain_block = TURN_LOOP_SRC[TURN_LOOP_SRC.index("_tl_here = get_turn_local()") :]
    drain_block = drain_block[: drain_block.index("for evt in _raw_events")]
    assert "_tool_sid = (_tl_here.sid" in drain_block
    assert 'getattr(self.runner, "_turn_sid"' in drain_block  # fallback allowed
    assert drain_block.index("_tl_here.sid") < drain_block.index('getattr(self.runner, "_turn_sid"')


def test_scheduler_wakes_waiters_on_finish_and_reap():
    sched_src = (APP_ROOT / "session_parallel.py").read_text(encoding="utf-8")
    assert "_signal_idle(sid)" in sched_src
    # reap() (finished-task reaping) must signal, ...
    reap_block = sched_src[sched_src.index("def reap(") : sched_src.index("async def wait_session_free")]
    assert "_signal_idle" in reap_block
    # ... finish() must signal, ...
    finish_block = sched_src[sched_src.index("def finish(") :]  # finish is the last method
    assert "_signal_idle" in finish_block
    # ... and the natural-completion wrapper in start() must signal too (a turn
    # that just returns runs neither finish() nor reap() before the waiter
    # would otherwise linger until its timeout).
    start_block = sched_src[sched_src.index("def start(") : sched_src.index("def request_stop_session")]
    assert "_signal_idle" in start_block
    # And wait_session_free must exist with a bounded timeout.
    assert "async def wait_session_free" in sched_src
    sig = inspect.signature(ParallelTurnScheduler.wait_session_free)
    assert sig.parameters["timeout"].default > 0
