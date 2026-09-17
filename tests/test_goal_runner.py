"""M3 — GoalRunner: budget, milestone, resume semantics (pure + wiring).

The three invariants that make "resume continues rather than restarts" true are
each locked individually, because they are the ones a refactor quietly breaks:

1. a ``done`` milestone is never executed again;
2. ``attempts`` is cumulative across resumes;
3. tripping a budget parks the plan as ``blocked`` (resumable) — never ``failed``.
"""

from __future__ import annotations

import asyncio

import pytest

from opensquad.tasks import hooks as h
from opensquad.tasks import task_scheduler as ts
from opensquad.tasks.goal_runner import (
    KIND_GOAL,
    KIND_TASK,
    MS_BLOCKED,
    MS_DONE,
    MS_PENDING,
    PLAN_BLOCKED,
    PLAN_DONE,
    STOP_SECONDS,
    STOP_TOKENS,
    Budget,
    GoalPlan,
    TurnOutcome,
    build_plan,
    milestones_from_payload,
    resume_plan,
    run_goal,
)
from opensquad.tasks.rpc import TASK_RPC_OPS, dispatch_task_op
from opensquad.tasks.task_scheduler import Task, TaskBlockedError, TaskScheduler


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# payload → plan
# ---------------------------------------------------------------------------


class TestMilestonesFromPayload:
    def test_strings_become_title_and_prompt(self):
        ms = milestones_from_payload(["design", "  build  "])
        assert [m.id for m in ms] == ["m1", "m2"]
        assert ms[0].title == "design" and ms[0].prompt == "design"
        assert ms[1].title == "build"

    def test_dicts_carry_verify(self):
        ms = milestones_from_payload([{"title": "T", "prompt": "P", "verify": "tests pass", "id": "x"}])
        assert len(ms) == 1
        assert (ms[0].id, ms[0].title, ms[0].prompt, ms[0].verify) == ("x", "T", "P", "tests pass")

    def test_title_only_dict_uses_title_as_prompt(self):
        ms = milestones_from_payload([{"title": "only"}])
        assert ms[0].prompt == "only"

    def test_blanks_and_junk_are_dropped(self):
        assert milestones_from_payload(["", "   ", None, 7, {}, {"title": "  "}]) == []

    def test_non_list_is_empty(self):
        assert milestones_from_payload("nope") == []


class TestBuildPlan:
    def test_plan_starts_queued_with_counts(self):
        plan = build_plan("ship it", ["a", "b", "c"], {"max_tokens": 500, "max_attempts": 3})
        assert plan.status == "queued"
        assert plan.progress() == (0, 3)
        assert plan.budget.max_tokens == 500
        assert plan.budget.attempts_allowed() == 3


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------


class TestRunGoal:
    def test_happy_path_runs_every_milestone_in_order(self):
        seen: list[str] = []

        async def turn(prompt, milestone):
            seen.append(prompt)
            return TurnOutcome(ok=True, text=f"did {prompt}", tokens=7)

        plan = build_plan("goal", ["a", "b", "c"])
        out = _run(run_goal(plan, turn))

        assert seen == ["a", "b", "c"]
        assert out.status == PLAN_DONE
        assert out.progress() == (3, 3)
        assert out.spent_tokens == 21
        assert [m.result for m in out.milestones] == ["did a", "did b", "did c"]

    def test_empty_goal_is_vacuously_done(self):
        async def turn(prompt, milestone):  # pragma: no cover - must not run
            raise AssertionError("no milestone should run")

        out = _run(run_goal(build_plan("", []), turn))
        assert out.status == PLAN_DONE

    def test_failure_parks_plan_as_blocked_not_failed(self):
        async def turn(prompt, milestone):
            return TurnOutcome(ok=False, error="boom")

        out = _run(run_goal(build_plan("g", ["a"], {"max_attempts": 1}), turn))
        assert out.status == PLAN_BLOCKED
        assert out.status != "failed"
        assert "boom" in out.blocked_reason
        assert out.milestones[0].status == MS_BLOCKED

    def test_raising_turn_is_recorded_not_propagated(self):
        async def turn(prompt, milestone):
            raise ValueError("kaboom")

        out = _run(run_goal(build_plan("g", ["a"], {"max_attempts": 1}), turn))
        assert out.status == PLAN_BLOCKED
        assert "kaboom" in out.milestones[0].error

    def test_none_outcome_counts_as_failure(self):
        async def turn(prompt, milestone):
            return None

        out = _run(run_goal(build_plan("g", ["a"], {"max_attempts": 1}), turn))
        assert out.status == PLAN_BLOCKED
        assert out.milestones[0].status == MS_BLOCKED

    def test_retries_until_allowed(self):
        calls = {"n": 0}

        async def turn(prompt, milestone):
            calls["n"] += 1
            return TurnOutcome(ok=calls["n"] >= 3, text="ok")

        out = _run(run_goal(build_plan("g", ["a"], {"max_attempts": 3}), turn))
        assert calls["n"] == 3
        assert out.status == PLAN_DONE
        assert out.milestones[0].attempts == 3

    def test_token_budget_stops_before_next_milestone(self):
        seen: list[str] = []

        async def turn(prompt, milestone):
            seen.append(prompt)
            return TurnOutcome(ok=True, tokens=60)

        plan = build_plan("g", ["a", "b", "c"], {"max_tokens": 100, "max_attempts": 1})
        out = _run(run_goal(plan, turn))

        assert seen == ["a", "b"]  # 60 then 120 >= 100 -> c never starts
        assert out.status == PLAN_BLOCKED
        assert out.blocked_reason == STOP_TOKENS

    def test_budget_is_rechecked_before_every_attempt(self):
        """A budget already blown must not start the *first* milestone either."""
        seen: list[str] = []

        async def turn(prompt, milestone):
            seen.append(prompt)
            return TurnOutcome(ok=True)

        plan = build_plan("g", ["a"], {"max_tokens": 10, "max_attempts": 1})
        plan.spent_tokens = 10
        plan.started_at = 0.0
        out = _run(run_goal(plan, turn))

        assert seen == []
        assert out.status == PLAN_BLOCKED
        assert out.blocked_reason == STOP_TOKENS

    def test_time_budget_blocks_with_its_own_reason(self):
        async def turn(prompt, milestone):  # pragma: no cover - budget trips first
            raise AssertionError("should not run")

        plan = build_plan("g", ["a"], {"max_seconds": 60, "max_attempts": 1})
        plan.started_at = 0.0
        out = _run(run_goal(plan, turn, now=lambda: 61.0))

        assert out.status == PLAN_BLOCKED
        assert out.blocked_reason == STOP_SECONDS

    def test_no_retry_after_budget_trips_mid_milestone(self):
        calls = {"n": 0}

        async def turn(prompt, milestone):
            calls["n"] += 1
            return TurnOutcome(ok=False, error="nope", tokens=100)

        plan = build_plan("g", ["a"], {"max_tokens": 50, "max_attempts": 5})
        out = _run(run_goal(plan, turn))

        assert calls["n"] == 1  # budget gone -> remaining attempts are wasted work
        assert out.status == PLAN_BLOCKED

    def test_on_update_fires_per_milestone_and_survives_a_broken_callback(self):
        snaps: list[tuple[int, int]] = []

        def on_update(plan):
            snaps.append(plan.progress())
            raise RuntimeError("callback is broken on purpose")

        async def turn(prompt, milestone):
            return TurnOutcome(ok=True)

        out = _run(run_goal(build_plan("g", ["a", "b"]), turn, on_update=on_update))

        assert out.status == PLAN_DONE
        # start + after-a + after-b  → the run is unaffected by the raise.
        assert snaps[-1] == (2, 2)

    def test_async_on_update_is_awaited(self):
        seen: list[tuple[int, int]] = []

        async def on_update(plan):
            seen.append(plan.progress())

        async def turn(prompt, milestone):
            return TurnOutcome(ok=True)

        _run(run_goal(build_plan("g", ["a"]), turn, on_update=on_update))
        assert seen[-1] == (1, 1)


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


class TestResume:
    def test_done_milestones_are_never_rerun(self):
        """The whole point of the checkpoint.

        Uses a *raised* retry allowance for the second run, because that is the
        only way a blocked milestone may legally run again — and it is also what
        proves the attempt counter is cumulative rather than reset.
        """
        first_run: list[str] = []

        async def turn_1(prompt, milestone):
            first_run.append(prompt)
            if prompt == "b":
                return TurnOutcome(ok=False, error="b failed")
            return TurnOutcome(ok=True, text=f"ok {prompt}")

        plan = build_plan("g", ["a", "b", "c"], {"max_attempts": 1})
        blocked = _run(run_goal(plan, turn_1))
        assert blocked.status == PLAN_BLOCKED
        assert first_run == ["a", "b"]

        # survive a serialisation round-trip, exactly like the on-disk checkpoint
        checkpoint = GoalPlan.from_dict(blocked.to_dict())
        assert checkpoint.milestones[1].attempts == 1
        resume_plan(checkpoint)
        checkpoint.budget.max_attempts = 3  # operator raises the allowance

        second_run: list[str] = []

        async def turn_2(prompt, milestone):
            second_run.append(prompt)
            return TurnOutcome(ok=True, text=f"ok {prompt}")

        done = _run(run_goal(checkpoint, turn_2))
        assert second_run == ["b", "c"]  # "a" stays done
        assert done.status == PLAN_DONE
        assert done.progress() == (3, 3)
        # cumulative: the resumed attempt is #2, not a fresh #1
        assert done.milestones[1].attempts == 2

    def test_attempts_are_cumulative_across_resumes(self):
        calls: list[int] = []

        async def always_fail(prompt, milestone):
            calls.append(milestone.attempts)
            return TurnOutcome(ok=False, error="nope")

        plan = build_plan("g", ["a"], {"max_attempts": 2})
        first = _run(run_goal(plan, always_fail))
        assert calls == [1, 2]
        assert first.status == PLAN_BLOCKED

        resumed = GoalPlan.from_dict(first.to_dict())
        resume_plan(resumed)
        calls.clear()
        second = _run(run_goal(resumed, always_fail))

        # A fresh allowance would have produced [1, 2] again — that is the bug.
        assert calls == []
        assert second.status == PLAN_BLOCKED
        assert second.milestones[0].attempts == 2

    def test_time_budget_is_re_armed_on_resume(self):
        """A goal parked on its time budget must be able to move again.

        ``max_seconds`` bounds one run's wall clock, not the goal's whole life.
        If the baseline survived the resume, the idle hours between parking and
        resuming would already exceed the cap and the run would re-park without
        doing a single thing — a resume button that provably cannot resume.
        """
        clock = {"t": 0.0}
        first_run: list[str] = []

        async def slow(prompt, milestone):
            first_run.append(prompt)
            clock["t"] += 100.0  # this one milestone blows the 10s cap
            return TurnOutcome(ok=True, text="ok")

        plan = build_plan("g", ["a", "b"], {"max_seconds": 10, "max_attempts": 1})
        blocked = _run(run_goal(plan, slow, now=lambda: clock["t"]))

        assert first_run == ["a"]
        assert blocked.status == PLAN_BLOCKED
        assert blocked.blocked_reason == STOP_SECONDS

        resumed = GoalPlan.from_dict(blocked.to_dict())
        resume_plan(resumed)
        assert resumed.started_at is None  # the clock was re-armed

        clock["t"] = 9999.0  # hours of sitting parked
        second_run: list[str] = []

        async def quick(prompt, milestone):
            second_run.append(prompt)
            return TurnOutcome(ok=True, text="ok")

        done = _run(run_goal(resumed, quick, now=lambda: clock["t"]))

        assert second_run == ["b"]  # "a" stays done, "b" finally runs
        assert done.status == PLAN_DONE
        assert done.progress() == (2, 2)

    def test_resume_keeps_spent_tokens_and_attempts(self):
        """Time is re-armed; cost and retry counts are not.

        ``spent_tokens`` records work actually paid for and ``attempts`` is a
        per-milestone allowance — resetting either would misreport cost or hand
        out a fresh retry allowance on every resume.
        """
        plan = build_plan("g", ["a"], {"max_tokens": 50, "max_attempts": 3})
        plan.milestones[0].attempts = 2
        plan.spent_tokens = 50
        plan.status = PLAN_BLOCKED

        resume_plan(plan)

        assert plan.spent_tokens == 50
        assert plan.milestones[0].attempts == 2

    def test_token_blocked_goal_needs_a_raised_cap(self):
        """Resume alone cannot rescue a token-exhausted goal.

        That is deliberate, not a gap: the tokens are gone. The operator (or
        the panel, which shows spent/max) has to raise ``max_tokens`` before
        the next run gets anywhere.
        """
        plan = build_plan("g", ["a"], {"max_tokens": 50, "max_attempts": 1})
        plan.spent_tokens = 50
        plan.status = PLAN_BLOCKED
        resume_plan(plan)

        async def unreachable(prompt, milestone):  # pragma: no cover - must not run
            raise AssertionError("budget should still be exhausted")

        still_blocked = _run(run_goal(plan, unreachable))
        assert still_blocked.status == PLAN_BLOCKED
        assert still_blocked.blocked_reason == STOP_TOKENS

        plan.budget.max_tokens = 500
        ran: list[str] = []

        async def turn(prompt, milestone):
            ran.append(prompt)
            return TurnOutcome(ok=True, text="ok")

        done = _run(run_goal(plan, turn))

        assert ran == ["a"]
        assert done.status == PLAN_DONE

    def test_resume_plan_resets_blocked_milestone_only(self):
        plan = build_plan("g", ["a", "b"])
        plan.milestones[0].status = MS_DONE
        plan.milestones[0].result = "kept"
        plan.milestones[1].status = MS_BLOCKED
        plan.milestones[1].error = "old error"
        plan.status = PLAN_BLOCKED
        plan.blocked_reason = "token budget exhausted"

        out = resume_plan(plan)

        assert out.milestones[0].status == MS_DONE
        assert out.milestones[0].result == "kept"
        assert out.milestones[1].status == MS_PENDING
        assert out.milestones[1].error == ""
        assert out.status == "queued"
        assert out.blocked_reason == ""
        assert out.finished_at is None

    def test_resume_plan_is_a_noop_on_a_finished_plan(self):
        plan = build_plan("g", ["a"])
        plan.status = PLAN_DONE
        plan.milestones[0].status = MS_DONE
        assert resume_plan(plan).status == PLAN_DONE

    def test_checkpoint_round_trip_preserves_everything_resume_needs(self):
        plan = build_plan("goal text", ["a", {"title": "B", "verify": "green"}], {"max_tokens": 9})
        plan.milestones[0].status = MS_DONE
        plan.milestones[0].attempts = 2
        plan.milestones[0].result = "shipped"
        plan.spent_tokens = 123
        plan.started_at = 111.0
        plan.status = PLAN_BLOCKED
        plan.blocked_reason = STOP_TOKENS

        back = GoalPlan.from_dict(plan.to_dict())

        assert back.goal == "goal text"
        assert back.spent_tokens == 123
        assert back.started_at == 111.0
        assert back.status == PLAN_BLOCKED
        assert back.blocked_reason == STOP_TOKENS
        assert back.budget.max_tokens == 9
        assert back.milestones[0].status == MS_DONE
        assert back.milestones[0].attempts == 2
        assert back.milestones[0].result == "shipped"
        assert back.milestones[1].verify == "green"


class TestBudget:
    def test_zero_means_no_limit(self):
        b = Budget()
        assert b.check(spent_tokens=10**9, elapsed_s=10**9) == ""

    def test_attempts_allowed_never_below_one(self):
        assert Budget(max_attempts=0).attempts_allowed() == 1
        assert Budget(max_attempts=-5).attempts_allowed() == 1


# ---------------------------------------------------------------------------
# scheduler: blocked status, goal submit, resume
# ---------------------------------------------------------------------------


@pytest.fixture()
def scheduler(tmp_path, monkeypatch):
    sched = TaskScheduler()
    monkeypatch.setattr(sched, "_persist_path", str(tmp_path / "tasks.json"))
    sched.configure(agent_id="test-agent", max_concurrent=2, max_tasks=5)
    return sched


class TestSchedulerGoal:
    def test_goal_submit_seeds_progress_counters(self, scheduler):
        task = scheduler.submit(
            title="g",
            prompt="g",
            agent_id="test-agent",
            kind=KIND_GOAL,
            plan=build_plan("g", ["a", "b", "c"]).to_dict(),
        )
        assert task["kind"] == KIND_GOAL
        assert (task["plan_done"], task["plan_total"]) == (0, 3)

    def test_plain_task_keeps_kind_and_has_no_plan(self, scheduler):
        task = scheduler.submit(title="t", prompt="p", agent_id="test-agent")
        assert task["kind"] == KIND_TASK
        # ``None``, not ``{}``: an empty dict is truthy, so a consumer guarding
        # with `if task.plan` would treat a plain task as a goal and read
        # checkpoint fields that are not there. The web panel did exactly that
        # and white-screened on `plan.budget.max_tokens`.
        assert task["plan"] is None

    def test_goal_wire_carries_a_complete_budget(self, scheduler):
        """The keys the panel dereferences must exist on every goal payload.

        `TaskDetail` reads `plan.budget.max_tokens` / `max_seconds` /
        `max_attempts` and `plan.milestones.length` unconditionally once a
        checkpoint is present. A goal that serialises without one of them is
        the same blank screen from the other direction.
        """
        task = scheduler.submit(
            title="g",
            prompt="g",
            agent_id="test-agent",
            kind=KIND_GOAL,
            plan=build_plan("g", ["a", "b"]).to_dict(),
        )
        plan = task["plan"]
        assert isinstance(plan, dict)
        assert set(plan["budget"]) == {"max_tokens", "max_seconds", "max_attempts"}
        assert isinstance(plan["milestones"], list)
        # The discriminator the frontend presence check relies on.
        assert plan["status"]

    def test_a_plain_task_reloads_without_a_checkpoint(self, scheduler):
        """``None`` must survive the persistence round trip.

        ``to_dict`` writes ``plan: None``; ``__init__`` maps anything non-dict
        back to ``{}``. If that guard went away, reloading a plain task would
        leave ``plan = None`` and every ``task.plan[...]`` write would explode.
        """
        submitted = scheduler.submit(title="t", prompt="p", agent_id="test-agent")
        assert submitted["plan"] is None
        reloaded = ts.Task(**submitted)
        assert reloaded.plan == {}
        assert reloaded.to_dict()["plan"] is None

    def test_task_blocked_error_parks_instead_of_failing(self, scheduler):
        async def blocker(task):
            raise TaskBlockedError("token budget exhausted")

        async def _go():
            ts.register_executor(blocker)
            task = scheduler.submit(title="t", prompt="p", agent_id="test-agent")
            await scheduler._run(scheduler._tasks[task["task_id"]])
            return scheduler._tasks[task["task_id"]]

        parked = _run(_go())
        assert parked.status == ts.STATUS_BLOCKED
        assert parked.error == "token budget exhausted"
        # parked tasks release their slot and are terminal for scheduling
        assert parked.status in ts.TERMINAL_STATUSES

    def test_a_broken_executor_still_fails_the_task(self, scheduler):
        """TaskBlockedError is the *only* exception that parks instead of failing."""

        async def blocker(task):
            raise RuntimeError("something broke")

        async def _go():
            ts.register_executor(blocker)
            task = scheduler.submit(title="t", prompt="p", agent_id="test-agent")
            await scheduler._run(scheduler._tasks[task["task_id"]])
            return scheduler._tasks[task["task_id"]]

        failed = _run(_go())
        assert failed.status == ts.STATUS_FAILED
        assert failed.error == "something broke"

    def test_resume_rejects_a_plain_task(self, scheduler):
        task = scheduler.submit(title="t", prompt="p", agent_id="test-agent")
        out = scheduler.resume(task["task_id"])
        assert out["status"] == "error"
        assert "only goal tasks" in out["message"]

    def test_resume_rejects_a_goal_that_is_not_parked(self, scheduler):
        task = scheduler.submit(
            title="g",
            prompt="g",
            agent_id="test-agent",
            kind=KIND_GOAL,
            plan=build_plan("g", ["a"]).to_dict(),
        )
        out = scheduler.resume(task["task_id"])
        assert out["status"] == "error"
        assert "queued" in out["message"]

    def test_resume_requeues_a_blocked_goal_at_its_checkpoint(self, scheduler, monkeypatch):
        task = scheduler.submit(
            title="g",
            prompt="g",
            agent_id="test-agent",
            kind=KIND_GOAL,
            plan=build_plan("g", ["a", "b"]).to_dict(),
        )
        sched_task = scheduler._tasks[task["task_id"]]
        sched_task.status = ts.STATUS_BLOCKED
        sched_task.error = STOP_TOKENS
        checkpoint = GoalPlan.from_dict(sched_task.plan)
        checkpoint.milestones[0].status = MS_DONE
        checkpoint.status = PLAN_BLOCKED
        sched_task.plan = checkpoint.to_dict()

        # no running loop -> the requeue is recorded but not dispatched
        monkeypatch.setattr(scheduler, "_loop", None)
        out = scheduler.resume(task["task_id"])

        assert out["status"] == "ok"
        assert sched_task.status == ts.STATUS_QUEUED
        assert sched_task.error == ""
        assert (sched_task.plan_done, sched_task.plan_total) == (1, 2)
        assert GoalPlan.from_dict(sched_task.plan).milestones[0].status == MS_DONE

    def test_resume_rejects_a_goal_without_milestones(self, scheduler):
        task = scheduler.submit(title="g", prompt="g", agent_id="test-agent", kind=KIND_GOAL, plan={})
        sched_task = scheduler._tasks[task["task_id"]]
        sched_task.status = ts.STATUS_BLOCKED
        out = scheduler.resume(task["task_id"])
        assert out["status"] == "error"
        assert "no milestones" in out["message"]

    def test_get_local_scheduler_hides_the_unconfigured_shell(self, monkeypatch):
        """The gateway's lazily created scheduler must not masquerade as local."""
        monkeypatch.setattr(ts, "_scheduler", None)
        assert ts.get_local_scheduler() is None
        shell = ts.get_scheduler()
        assert shell is not None and ts.get_local_scheduler() is None
        shell.configure(agent_id="real")
        assert ts.get_local_scheduler() is shell


# ---------------------------------------------------------------------------
# rpc surface
# ---------------------------------------------------------------------------


class _StubScheduler:
    def __init__(self):
        self.kw = {}
        self.calls = []

    def submit(self, **kw):
        self.kw = kw
        return {"task_id": "t1", **kw}

    def resume(self, task_id):
        self.calls.append(("resume", task_id))
        return {"status": "ok", "task": {"task_id": task_id}}

    def list_tasks(self, agent_id=None):
        return []

    def get_task(self, task_id):
        return {"task_id": task_id}

    def abort(self, task_id):
        return {"status": "ok"}

    def set_approval(self, task_id, approved):
        return {"status": "ok"}

    def remove(self, task_id):
        return {"status": "ok"}


class TestRpcGoal:
    def test_resume_is_a_declared_op(self):
        assert "resume" in TASK_RPC_OPS

    def test_goal_submit_builds_a_plan_checkpoint(self):
        s = _StubScheduler()
        out = dispatch_task_op(
            s,
            "submit",
            {"title": "T", "prompt": "go", "kind": KIND_GOAL, "milestones": ["a", "b"]},
        )
        assert out["ok"] is True
        plan = s.kw["plan"]
        assert plan["plan_total"] == 2
        assert [m["title"] for m in plan["milestones"]] == ["a", "b"]

    def test_plan_submit_accepts_dicts_with_verify_and_budget(self):
        s = _StubScheduler()
        dispatch_task_op(
            s,
            "submit",
            {
                "title": "T",
                "prompt": "go",
                "kind": KIND_GOAL,
                "milestones": [{"title": "A", "verify": "green"}],
                "budget": {"max_tokens": 42, "max_attempts": 3},
            },
        )
        plan = s.kw["plan"]
        assert plan["milestones"][0]["verify"] == "green"
        assert plan["budget"]["max_tokens"] == 42
        assert plan["budget"]["max_attempts"] == 3

    def test_plain_submit_stays_planless(self):
        s = _StubScheduler()
        dispatch_task_op(s, "submit", {"title": "T", "prompt": "p"})
        assert s.kw["plan"] == {}
        assert s.kw["kind"] == KIND_TASK

    def test_an_existing_checkpoint_is_passed_through_verbatim(self):
        s = _StubScheduler()
        checkpoint = build_plan("g", ["a"]).to_dict()
        checkpoint["milestones"][0]["status"] = MS_DONE
        dispatch_task_op(s, "submit", {"title": "T", "prompt": "g", "kind": KIND_GOAL, "plan": checkpoint})
        assert s.kw["plan"]["milestones"][0]["status"] == MS_DONE

    def test_resume_op_is_routed_to_the_scheduler(self):
        s = _StubScheduler()
        out = dispatch_task_op(s, "resume", {"task_id": "t9"})
        assert out["ok"] is True
        assert s.calls == [("resume", "t9")]

    def test_resume_error_is_normalised(self):
        class S(_StubScheduler):
            def resume(self, task_id):
                return {"status": "error", "message": "only goal tasks can be resumed"}

        out = dispatch_task_op(S(), "resume", {"task_id": "t9"})
        assert out == {"ok": False, "error": "only goal tasks can be resumed"}


# ---------------------------------------------------------------------------
# hooks: the production executor
# ---------------------------------------------------------------------------


class TestHooks:
    def test_task_executor_dispatches_by_kind(self, monkeypatch):
        seen: list[str] = []

        async def fake_goal(task):
            seen.append("goal")
            return "g"

        async def fake_default(task):
            seen.append("task")
            return "t"

        monkeypatch.setattr(h, "goal_task_executor", fake_goal)
        monkeypatch.setattr(h, "default_task_executor", fake_default)

        assert _run(h.task_executor(Task(kind=KIND_GOAL))) == "g"
        assert _run(h.task_executor(Task(kind=KIND_TASK))) == "t"
        assert _run(h.task_executor(Task())) == "t"  # unset kind → plain task
        assert seen == ["goal", "task", "task"]

    def test_run_session_turn_without_a_runner_fails_softly(self, monkeypatch):
        monkeypatch.setattr(h, "_active_runner", lambda: None)
        out = _run(h.run_session_turn("s1", "hi"))
        assert out.ok is False
        assert "runner" in out.error

    def test_run_session_turn_reports_a_missing_turn_entrypoint(self, monkeypatch):
        monkeypatch.setattr(h, "_active_runner", lambda: object())
        out = _run(h.run_session_turn("s1", "hi"))
        assert out.ok is False
        assert "_parallel_session_turn" in out.error

    def test_run_session_turn_awaits_and_measures_tokens(self, monkeypatch):
        class FakeRunner:
            def __init__(self):
                self.usage = {"api": object(), "input": 100, "output": 0}
                self.seen = []

            def _round_usage_snapshot(self, sid):
                return dict(self.usage)

            async def _parallel_session_turn(self, sid, item):
                self.seen.append(item)
                self.usage = {"api": self.usage["api"], "input": 160, "output": 40}

        runner = FakeRunner()
        monkeypatch.setattr(h, "_active_runner", lambda: runner)
        monkeypatch.setattr(h, "_last_assistant_text", lambda sid: "milestone text")

        out = _run(h.run_session_turn("s1", "do it", Task(task_id="t1")))

        assert out.ok is True
        assert out.tokens == 100  # (160-100) + (40-0)
        assert out.text == "milestone text"
        assert runner.seen[0]["session_id"] == "s1"
        assert runner.seen[0]["user_id"] == "task:t1"
        assert runner.seen[0]["content"] == "do it"

    def test_token_delta_falls_back_to_totals_after_an_api_rebind(self):
        before = {"api": object(), "input": 100, "output": 50}
        after = {"api": object(), "input": 10, "output": 5}
        assert h._tokens_spent(before, after) == 15

    def test_token_delta_is_a_delta_when_the_api_is_the_same(self):
        api = object()
        assert h._tokens_spent({"api": api, "input": 100, "output": 50}, {"api": api, "input": 160, "output": 70}) == 80

    def test_goal_executor_walks_milestones_through_one_session(self, monkeypatch):
        contents: list[str] = []

        async def fake_env(task):
            return "sid-1", "/tmp/wt/t1"

        async def fake_turn(sid, content, task):
            assert sid == "sid-1"
            contents.append(content)
            return TurnOutcome(ok=True, text="ok", tokens=5)

        monkeypatch.setattr(h, "_prepare_task_env", fake_env)
        monkeypatch.setattr(h, "run_session_turn", fake_turn)
        monkeypatch.setattr(h, "_checkpoint", lambda task, plan: None)

        task = Task(
            task_id="t1",
            kind=KIND_GOAL,
            title="g",
            plan=build_plan("g", ["a", {"title": "B", "verify": "tests green"}]).to_dict(),
        )
        summary = _run(h.goal_task_executor(task))

        assert "milestones=2/2" in summary
        assert len(contents) == 2
        assert contents[1].startswith("B")
        assert "tests green" in contents[1]  # acceptance note travels with the prompt

    def test_goal_executor_treats_a_milestone_less_goal_as_one_milestone(self, monkeypatch):
        contents: list[str] = []

        async def fake_env(task):
            return "sid-1", ""

        async def fake_turn(sid, content, task):
            contents.append(content)
            return TurnOutcome(ok=True)

        monkeypatch.setattr(h, "_prepare_task_env", fake_env)
        monkeypatch.setattr(h, "run_session_turn", fake_turn)
        monkeypatch.setattr(h, "_checkpoint", lambda task, plan: None)

        task = Task(task_id="t1", kind=KIND_GOAL, title="goal title", prompt="goal prompt", plan={})
        summary = _run(h.goal_task_executor(task))

        assert contents == ["goal prompt"]
        assert "milestones=1/1" in summary

    def test_goal_executor_parks_the_task_when_the_goal_blocks(self, monkeypatch):
        async def fake_env(task):
            return "sid-1", ""

        async def fake_turn(sid, content, task):
            return TurnOutcome(ok=False, error="model refused")

        seen: list[dict] = []
        monkeypatch.setattr(h, "_prepare_task_env", fake_env)
        monkeypatch.setattr(h, "run_session_turn", fake_turn)
        monkeypatch.setattr(h, "_checkpoint", lambda task, plan: seen.append(plan.to_dict()))

        task = Task(
            task_id="t1",
            kind=KIND_GOAL,
            plan=build_plan("g", ["a"], {"max_attempts": 1}).to_dict(),
        )
        with pytest.raises(TaskBlockedError) as exc:
            _run(h.goal_task_executor(task))

        assert "model refused" in str(exc.value)
        assert seen[-1]["status"] == PLAN_BLOCKED

    def test_checkpoint_persists_plan_and_tokens(self, monkeypatch):
        """The real _checkpoint (not the stub) must write plan/tokens/progress."""
        writes: list[tuple] = []

        class Sched:
            def update_progress(self, task_id, *, plan_done=None, plan_total=None):
                writes.append((task_id, plan_done, plan_total))

        monkeypatch.setattr(ts, "get_scheduler", lambda: Sched())

        task = Task(task_id="t1", kind=KIND_GOAL)
        plan = build_plan("g", ["a", "b"])
        plan.milestones[0].status = MS_DONE
        plan.spent_tokens = 321

        h._checkpoint(task, plan)

        assert writes == [("t1", 1, 2)]
        assert task.cost["tokens"] == 321
        assert GoalPlan.from_dict(task.plan).milestones[0].status == MS_DONE
