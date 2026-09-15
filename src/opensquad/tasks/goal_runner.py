"""M3 — GoalRunner: budgeted, resumable milestone execution.

A plain task is a single turn. A *goal* is an ordered list of milestones that
run one at a time, each charged against a token/time budget, with a checkpoint
written after every milestone — so an interruption resumes at the first
milestone that still needs work instead of starting over.

Why this is a separate module
-----------------------------
``run_goal`` is deliberately free of IO, the event bus and the runner: it takes
a ``run_turn`` callback. That keeps the controller (budget arithmetic, retry
accounting, resume semantics) fully testable with a fake turn runner, while the
production wiring — pushing a prompt into the live runner and waiting for the
round to end — lives in :mod:`opensquad.tasks.hooks`.

Resume contract
---------------
``interrupted`` / ``blocked`` plans are re-driven by feeding the *same*
:class:`GoalPlan` back into :func:`run_goal`. Three invariants make that safe:

1. a milestone whose status is ``done`` is never executed again;
2. ``attempts`` is cumulative, so resuming does not silently restore a fresh
   retry allowance;
3. the budget is re-checked before every attempt, and tripping it parks the plan
   as ``blocked`` with a reason — never as ``failed``.
"""

from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Task kinds (Task.kind)
KIND_TASK = "task"
KIND_GOAL = "goal"

# Milestone states
MS_PENDING = "pending"
MS_RUNNING = "running"
MS_DONE = "done"
MS_BLOCKED = "blocked"

# Plan states
PLAN_QUEUED = "queued"
PLAN_RUNNING = "running"
PLAN_DONE = "done"
PLAN_BLOCKED = "blocked"
PLAN_ABORTED = "aborted"

PLAN_TERMINAL = frozenset({PLAN_DONE, PLAN_BLOCKED, PLAN_ABORTED})

# Blocked reasons surfaced to the UI
STOP_TOKENS = "token budget exhausted"
STOP_SECONDS = "time budget exhausted"


@dataclass
class Milestone:
    id: str
    title: str = ""
    prompt: str = ""
    # Free-text acceptance note handed to the agent alongside ``prompt`` so it
    # knows what "done" means. Verification stays agent-side (plus the M1 diff
    # report when the task ran in a worktree).
    verify: str = ""
    status: str = MS_PENDING
    attempts: int = 0
    result: str = ""
    error: str = ""
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def is_done(self) -> bool:
        return self.status == MS_DONE

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "verify": self.verify,
            "status": self.status,
            "attempts": self.attempts,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Milestone:
        data = data if isinstance(data, dict) else {}
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            prompt=str(data.get("prompt") or ""),
            verify=str(data.get("verify") or ""),
            status=str(data.get("status") or MS_PENDING),
            attempts=int(data.get("attempts") or 0),
            result=str(data.get("result") or ""),
            error=str(data.get("error") or ""),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )


@dataclass
class Budget:
    """Caps for one goal. ``0`` means "no limit" for each dimension.

    ``max_tokens`` is cumulative over the goal's life; ``max_seconds`` bounds
    one run and is re-armed by :func:`resume_plan`. See the note there for why
    the two dimensions differ.
    """

    max_tokens: int = 0
    # Wall clock of a single run, re-armed on resume.
    max_seconds: int = 0
    # Per-milestone retry allowance, cumulative across resumes.
    max_attempts: int = 2

    def check(self, *, spent_tokens: int, elapsed_s: float) -> str:
        """Return a stop reason when a cap is reached, else ``""``."""
        if self.max_tokens > 0 and spent_tokens >= self.max_tokens:
            return STOP_TOKENS
        if self.max_seconds > 0 and elapsed_s >= self.max_seconds:
            return STOP_SECONDS
        return ""

    def attempts_allowed(self) -> int:
        return max(1, int(self.max_attempts or 1))

    def to_dict(self) -> dict:
        return {
            "max_tokens": self.max_tokens,
            "max_seconds": self.max_seconds,
            "max_attempts": self.max_attempts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Budget:
        data = data if isinstance(data, dict) else {}
        return cls(
            max_tokens=int(data.get("max_tokens") or 0),
            max_seconds=int(data.get("max_seconds") or 0),
            max_attempts=int(data.get("max_attempts") or 2),
        )


@dataclass
class TurnOutcome:
    """What one milestone attempt produced."""

    ok: bool
    text: str = ""
    tokens: int = 0
    error: str = ""


@dataclass
class GoalPlan:
    goal: str = ""
    milestones: list[Milestone] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    status: str = PLAN_QUEUED
    spent_tokens: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    blocked_reason: str = ""

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.milestones)

    @property
    def done(self) -> int:
        return sum(1 for m in self.milestones if m.status == MS_DONE)

    def progress(self) -> tuple[int, int]:
        return self.done, self.total

    @property
    def is_terminal(self) -> bool:
        return self.status in PLAN_TERMINAL

    def next_milestone(self) -> Milestone | None:
        """The first milestone that still needs work (``None`` = all done)."""
        for ms in self.milestones:
            if ms.status != MS_DONE:
                return ms
        return None

    def resume_index(self) -> int:
        """Index the next run would start from (``len`` when nothing is left)."""
        for i, ms in enumerate(self.milestones):
            if ms.status != MS_DONE:
                return i
        return len(self.milestones)

    def elapsed_s(self, *, now: float | None = None) -> float:
        # ``0.0`` is a legitimate timestamp — testing falsiness here silently
        # disabled the time budget for any pinned/epoch-0 clock.
        if self.started_at is None:
            return 0.0
        return max(0.0, (now if now is not None else time.time()) - float(self.started_at))

    # ------------------------------------------------------------------
    # serialisation (the checkpoint)
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        done, total = self.progress()
        return {
            "goal": self.goal,
            "status": self.status,
            "spent_tokens": self.spent_tokens,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "blocked_reason": self.blocked_reason,
            "plan_done": done,
            "plan_total": total,
            "budget": self.budget.to_dict(),
            "milestones": [m.to_dict() for m in self.milestones],
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> GoalPlan:
        data = data if isinstance(data, dict) else {}
        raw = data.get("milestones")
        milestones = [Milestone.from_dict(m) for m in raw] if isinstance(raw, list) else []
        return cls(
            goal=str(data.get("goal") or ""),
            milestones=milestones,
            budget=Budget.from_dict(data.get("budget") or {}),
            status=str(data.get("status") or PLAN_QUEUED),
            spent_tokens=int(data.get("spent_tokens") or 0),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            blocked_reason=str(data.get("blocked_reason") or ""),
        )


# ---------------------------------------------------------------------------
# building a plan from a REST payload
# ---------------------------------------------------------------------------


def milestones_from_payload(items) -> list[Milestone]:
    """Accept ``["do x", ...]`` or ``[{title|prompt, id?, verify?}, ...]``."""
    out: list[Milestone] = []
    if not isinstance(items, list):
        return out
    for i, item in enumerate(items):
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            out.append(Milestone(id=f"m{i + 1}", title=text, prompt=text))
            continue
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not (title or prompt):
            continue
        out.append(
            Milestone(
                id=str(item.get("id") or f"m{i + 1}"),
                title=title or prompt,
                prompt=prompt or title,
                verify=str(item.get("verify") or ""),
            )
        )
    return out


def build_plan(goal: str, milestones=None, budget=None) -> GoalPlan:
    """Assemble a fresh plan from a REST submission."""
    return GoalPlan(
        goal=str(goal or ""),
        milestones=milestones_from_payload(milestones),
        budget=Budget.from_dict(budget or {}),
        status=PLAN_QUEUED,
    )


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------


async def _notify(callback, plan: GoalPlan) -> None:
    """Fire the checkpoint callback; a broken one must not abort the run."""
    if callback is None:
        return
    try:
        result = callback(plan)
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.debug("[goal] update callback failed", exc_info=True)


def _now_value(now) -> float:
    """Read the clock. ``now`` may be a callable (the default) or a number.

    Tests pin the clock by passing a lambda; production just passes
    ``time.time``. Accepting both keeps the budget check honest instead of
    subtracting a function object from a timestamp.
    """
    return float(now() if callable(now) else now)


def _stop_reason(plan: GoalPlan, *, now) -> str:
    return plan.budget.check(
        spent_tokens=plan.spent_tokens,
        elapsed_s=plan.elapsed_s(now=_now_value(now)),
    )


async def run_goal(
    plan: GoalPlan,
    run_turn,
    *,
    on_update=None,
    now=time.time,
) -> GoalPlan:
    """Drive ``plan`` to completion, one milestone at a time.

    ``run_turn(prompt, milestone) -> TurnOutcome`` is injected. ``on_update``
    (sync or async) runs after every milestone and every state change so the
    caller can persist a checkpoint; exceptions from it are swallowed.
    """
    if not plan.milestones:
        # Nothing to verify — an empty goal is vacuously done rather than stuck.
        plan.status = PLAN_DONE
        plan.finished_at = _now_value(now)
        await _notify(on_update, plan)
        return plan

    if plan.started_at is None:
        plan.started_at = _now_value(now)
    plan.status = PLAN_RUNNING
    plan.blocked_reason = ""
    await _notify(on_update, plan)

    while True:
        reason = _stop_reason(plan, now=now)
        if reason:
            plan.status = PLAN_BLOCKED
            plan.blocked_reason = reason
            plan.finished_at = _now_value(now)
            await _notify(on_update, plan)
            return plan

        milestone = plan.next_milestone()
        if milestone is None:
            plan.status = PLAN_DONE
            plan.blocked_reason = ""
            plan.finished_at = _now_value(now)
            await _notify(on_update, plan)
            return plan

        await _run_milestone(plan, milestone, run_turn, on_update=on_update, now=now)

        if milestone.status != MS_DONE:
            plan.status = PLAN_BLOCKED
            plan.blocked_reason = milestone.error or f"milestone {milestone.id} did not complete"
            plan.finished_at = _now_value(now)
            await _notify(on_update, plan)
            return plan

        # Checkpoint after every completed milestone — this is what makes a
        # resume start at the next one rather than at the beginning.
        await _notify(on_update, plan)


async def _run_milestone(plan: GoalPlan, milestone: Milestone, run_turn, *, on_update, now) -> None:
    allowed = plan.budget.attempts_allowed()
    while milestone.attempts < allowed:
        milestone.status = MS_RUNNING
        if milestone.started_at is None:
            milestone.started_at = _now_value(now)
        milestone.attempts += 1
        await _notify(on_update, plan)

        try:
            outcome = await run_turn(milestone.prompt, milestone)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[goal] milestone %s raised", milestone.id, exc_info=True)
            outcome = TurnOutcome(ok=False, error=str(exc))

        if outcome is None:
            outcome = TurnOutcome(ok=False, error="turn runner returned nothing")
        plan.spent_tokens += max(0, int(outcome.tokens or 0))

        if outcome.ok:
            milestone.status = MS_DONE
            milestone.result = outcome.text or ""
            milestone.error = ""
            milestone.finished_at = _now_value(now)
            return

        milestone.error = outcome.error or "milestone run failed"
        # No point spending the rest of the retry allowance once the token or
        # time budget is gone.
        if _stop_reason(plan, now=now):
            break

    milestone.status = MS_BLOCKED
    milestone.finished_at = _now_value(now)


def resume_plan(plan: GoalPlan) -> GoalPlan:
    """Prepare an interrupted/blocked plan to be driven again.

    Only the bookkeeping changes: the completed milestones (and their results)
    stay exactly as they are, which is what makes the second run continue rather
    than restart.

    ``spent_tokens`` and each milestone's ``attempts`` are deliberately kept —
    money spent stays spent, and a retry allowance is per milestone, not per
    run. The *time* baseline is the exception: it is cleared so that the next
    run re-stamps it, which makes ``max_seconds`` bound one run's wall clock
    rather than the goal's whole life. Without that, a goal parked on its time
    budget could never make progress again — the hours it spent sitting parked
    would already exceed the cap — and "a budget trip is resumable" would be a
    lie for the time dimension.
    """
    if plan.status == PLAN_DONE:
        return plan
    for ms in plan.milestones:
        if ms.status in (MS_RUNNING, MS_BLOCKED):
            # A blocked milestone gets another chance; its cumulative
            # ``attempts`` counter still applies.
            ms.status = MS_PENDING
            ms.error = ""
    plan.status = PLAN_QUEUED
    plan.finished_at = None
    plan.blocked_reason = ""
    plan.started_at = None
    return plan
