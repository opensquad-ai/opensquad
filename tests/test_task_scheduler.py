"""Tests for opensquad.tasks.task_scheduler (M2)."""

from __future__ import annotations

import asyncio

import pytest

from opensquad.tasks import task_scheduler as ts
from opensquad.tasks.task_scheduler import (
    STATUS_ABORTED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    Task,
    TaskScheduler,
)


@pytest.fixture()
def scheduler(tmp_path, monkeypatch):
    sched = TaskScheduler()
    monkeypatch.setattr(sched, "_persist_path", str(tmp_path / "tasks.json"))
    sched.configure(agent_id="test-agent", max_concurrent=2, max_tasks=5)
    return sched


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestLifecycle:
    def test_submit_queues_task(self, scheduler):
        async def fake_executor(task: Task) -> str:
            return "ok"

        ts.register_executor(fake_executor)
        scheduler.attach_loop(asyncio.new_event_loop())
        # avoid actually scheduling: no running loop -> submit keeps it queued
        task = scheduler.submit(title="demo", prompt="do it", agent_id="test-agent")
        assert task["status"] == STATUS_QUEUED
        assert task["agent_id"] == "test-agent"
        assert task["origin"] == "manual"
        assert scheduler.get_task(task["task_id"]) is not None

    def test_task_ceiling_enforced(self, scheduler):
        # Fill the registry with active tasks
        for i in range(5):
            scheduler._tasks[f"t{i}"] = Task(
                task_id=f"t{i}", agent_id="test-agent", title=f"x{i}", status=STATUS_RUNNING
            )
        with pytest.raises(RuntimeError, match="limit reached"):
            scheduler.submit(title="overflow", prompt="x", agent_id="test-agent")

    def test_abort_transitions(self, scheduler):
        t = scheduler._tasks["t0"] = Task(task_id="t0", agent_id="a", title="x", status=STATUS_RUNNING)
        result = scheduler.abort("t0")
        assert result["status"] == "ok"
        assert t.status == STATUS_ABORTED

    def test_abort_terminal_rejected(self, scheduler):
        scheduler._tasks["t0"] = Task(task_id="t0", agent_id="a", title="x", status=STATUS_DONE)
        result = scheduler.abort("t0")
        assert result["status"] == "error"

    def test_remove_requires_terminal(self, scheduler):
        scheduler._tasks["t0"] = Task(task_id="t0", agent_id="a", title="x", status=STATUS_RUNNING)
        assert scheduler.remove("t0")["status"] == "error"
        scheduler._tasks["t0"].status = STATUS_DONE
        assert scheduler.remove("t0")["status"] == "ok"
        assert scheduler.get_task("t0") is None

    def test_set_approval(self, scheduler):
        t = scheduler._tasks["t0"] = Task(task_id="t0", agent_id="a", title="x", status=ts.STATUS_WAITING_APPROVAL)
        result = scheduler.set_approval("t0", approved=True)
        assert result["status"] == "ok"
        assert t.status == STATUS_RUNNING

        t.status = ts.STATUS_WAITING_APPROVAL
        result = scheduler.set_approval("t0", approved=False)
        assert t.status == STATUS_ABORTED

    def test_update_progress(self, scheduler):
        t = scheduler._tasks["t0"] = Task(task_id="t0", agent_id="a", title="x", status=STATUS_RUNNING)
        scheduler.update_progress("t0", plan_done=3, plan_total=10)
        assert t.plan_done == 3 and t.plan_total == 10


class TestPersistence:
    def test_recover_interrupts_non_terminal(self, scheduler, tmp_path):
        path = tmp_path / "tasks.json"
        import json as _json

        path.write_text(
            _json.dumps(
                [
                    {"task_id": "t1", "agent_id": "a", "title": "x", "status": STATUS_RUNNING},
                    {"task_id": "t2", "agent_id": "a", "title": "y", "status": STATUS_DONE},
                ]
            ),
            encoding="utf-8",
        )
        scheduler._persist_path = str(path)
        scheduler.recover()
        assert scheduler._tasks["t1"].status == STATUS_INTERRUPTED
        assert scheduler._tasks["t2"].status == STATUS_DONE

    def test_save_roundtrip(self, scheduler):
        scheduler._tasks["t0"] = Task(task_id="t0", agent_id="a", title="hello", status=STATUS_RUNNING)
        scheduler._save()
        sched2 = TaskScheduler()
        sched2._persist_path = scheduler._persist_path
        sched2.recover()
        assert sched2._tasks["t0"].title == "hello"


class TestConcurrency:
    def test_semaphore_caps_parallel_execution(self, scheduler):
        running = 0
        peak = 0

        async def slow_executor(task: Task) -> str:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1
            return "done"

        ts.register_executor(slow_executor)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        scheduler.attach_loop(loop)

        async def runner():
            for i in range(5):
                await scheduler._run(scheduler._tasks[f"t{i}"])

        for i in range(5):
            scheduler._tasks[f"t{i}"] = Task(task_id=f"t{i}", agent_id="a", title=f"t{i}", status=STATUS_QUEUED)
        loop.run_until_complete(runner())
        assert peak <= 2  # max_concurrent=2
        assert all(scheduler._tasks[f"t{i}"].status == STATUS_DONE for i in range(5))


class TestEvents:
    def test_subscriber_receives_updates(self, scheduler):
        events: list[dict] = []
        ts.subscribe(events.append)
        try:
            scheduler._tasks["t0"] = Task(task_id="t0", agent_id="a", title="x", status=STATUS_RUNNING)
            scheduler.update_progress("t0", plan_done=1)
            assert len(events) >= 1
            assert events[-1]["task"]["task_id"] == "t0"
        finally:
            ts.unsubscribe(events.append)


class TestExecutorHook:
    def test_missing_executor_fails_task(self, scheduler):
        ts.register_executor(None)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        scheduler.attach_loop(loop)
        t = Task(task_id="tX", agent_id="a", title="x", status=STATUS_QUEUED)
        scheduler._tasks["tX"] = t
        loop.run_until_complete(scheduler._run(t))
        assert t.status == STATUS_FAILED
        assert "no task executor" in t.error

    def test_cancel_marks_aborted(self, scheduler):
        async def hanging(task: Task) -> str:
            await asyncio.sleep(60)
            return "never"

        ts.register_executor(hanging)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        scheduler.attach_loop(loop)
        t = Task(task_id="tC", agent_id="a", title="x", status=STATUS_QUEUED)
        scheduler._tasks["tC"] = t
        task_fut = loop.create_task(scheduler._run(t))
        loop.run_until_complete(asyncio.sleep(0.05))
        task_fut.cancel()
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(task_fut)
        assert t.status == STATUS_ABORTED
