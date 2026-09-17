/**
 * Decode the task-plan wire payload into a shape the panel can actually render.
 *
 * `opensquad/tasks/task_scheduler.py::Task.to_dict()` serialises a `plan` for
 * *every* task — but only a goal has a checkpoint behind it. A plain task ships
 * the empty object as a placeholder (`self.plan` defaults to `{}`, and the
 * dataclass comment says so: "stays {} for plain tasks"). The TypeScript
 * interface meanwhile declared all ten `TaskPlan` fields as required, so
 * `task.plan || null` happily returned that truthy `{}`, and the detail panel
 * read `plan.budget.max_tokens` off nothing:
 *
 *     Uncaught TypeError: Cannot read properties of undefined (reading 'max_tokens')
 *
 * One absent key blanked the entire app, because an error thrown during render
 * unmounts the whole React root.
 *
 * A type describes what we expect; this decodes what actually arrived. That
 * matters here beyond the placeholder: the gateway and the agent are separate
 * processes that can run different builds (`resources/backend-win` ships a
 * packed backend), so the same field can legitimately arrive as `{}`, `null`,
 * absent, or a checkpoint from an older schema. All of them must be survivable.
 *
 * Presence rule: a real `GoalPlan.to_dict()` always carries a non-empty
 * `status` (dataclass default `queued`, and `from_dict` coerces blank → default,
 * so it is never persisted empty), while the placeholder carries no key at all.
 * Any of `status`, `goal`, `blocked_reason`, a budget object or a milestone
 * counts as a real plan, so an unexpectedly sparse checkpoint still renders
 * instead of silently disappearing.
 */
import type { TaskMilestone, TaskPlan, TaskPlanWire } from '../services/api';

/** The task carries `plan?`, so the decoder accepts "absent" too. */
export type MaybeTaskPlanWire = TaskPlanWire | undefined;

/** Finite integer, else 0 — never NaN (it would render as "NaN" in the panel). */
const int = (v: unknown): number => {
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : 0;
};

/**
 * Mirror `Budget.from_dict`'s reading of `max_attempts`: it does
 * `int(data.get("max_attempts") or 2)`, so an absent/zero value means the
 * backend default of 2 — not "never retry". Displaying 0 here would contradict
 * the checkpoint the agent is actually running with.
 */
const attempts = (v: unknown): number => int(v) || 2;

const str = (v: unknown): string => (typeof v === 'string' ? v : '');

/** A timestamp is `number | null`; 0 is a legal epoch, so only test the type. */
const stamp = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;

const obj = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

const MILESTONE_STATUSES = new Set<TaskMilestone['status']>([
  'pending',
  'running',
  'done',
  'blocked',
]);

/**
 * One milestone. The backend guarantees every field, but a checkpoint written
 * by an older build may not, and `MilestoneRow` reads `ms.status` straight into
 * a `MILESTONE_META[...]` lookup — an unknown status would fall back safely
 * there, yet a missing `title`/`attempts` would not.
 */
export const normalizeMilestone = (raw: unknown, index: number): TaskMilestone => {
  const m = obj(raw) ?? {};
  const title = str(m.title);
  const prompt = str(m.prompt);
  const status = str(m.status) as TaskMilestone['status'];
  return {
    id: str(m.id) || `m${index + 1}`,
    // `milestones_from_payload` keeps both in sync; mirror that for a sparse one
    // so a row is never rendered blank.
    title: title || prompt,
    prompt: prompt || title,
    verify: str(m.verify),
    status: MILESTONE_STATUSES.has(status) ? status : 'pending',
    attempts: int(m.attempts),
    result: str(m.result),
    error: str(m.error),
    started_at: stamp(m.started_at),
    finished_at: stamp(m.finished_at),
  };
};

/**
 * `TaskPlan | null` from whatever the wire sent. `null` means "this task has no
 * goal checkpoint" and is the only value callers should branch on.
 */
export const normalizeTaskPlan = (raw: MaybeTaskPlanWire): TaskPlan | null => {
  const p = obj(raw);
  if (!p) return null;

  const goal = str(p.goal);
  const status = str(p.status);
  const blockedReason = str(p.blocked_reason);
  const budget = obj(p.budget);
  const milestones = (Array.isArray(p.milestones) ? p.milestones : []).map(normalizeMilestone);

  // The plain-task placeholder `{}` carries none of these; a real checkpoint
  // carries at least one. Never render the M3 block for the placeholder.
  if (!goal && !status && !blockedReason && !budget && milestones.length === 0) return null;

  return {
    goal,
    status,
    spent_tokens: int(p.spent_tokens),
    started_at: stamp(p.started_at),
    finished_at: stamp(p.finished_at),
    blocked_reason: blockedReason,
    plan_done: int(p.plan_done),
    plan_total: int(p.plan_total),
    // Always a complete object: `plan.budget.max_tokens` is the exact read that
    // used to throw, so callers must never need optional chaining here.
    budget: {
      max_tokens: int(budget?.max_tokens),
      max_seconds: int(budget?.max_seconds),
      max_attempts: attempts(budget?.max_attempts),
    },
    milestones,
  };
};
