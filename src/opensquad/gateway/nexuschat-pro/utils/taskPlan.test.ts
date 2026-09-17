/**
 * `normalizeTaskPlan` — the decoder that stands between the task wire format and
 * every read of a goal checkpoint.
 *
 * The bug this locks: `Task.to_dict()` serialises `plan` for *every* task, and a
 * plain task sends the empty object as its placeholder rather than `null`. The
 * TS type declared all ten fields required, so `task.plan || null` returned that
 * truthy `{}` and `TaskDetail` read `plan.budget.max_tokens` off nothing —
 * `Uncaught TypeError`, and because a render throw unmounts the React root, the
 * whole app went blank.
 *
 * The rules below are the ones a rewrite could plausibly get wrong:
 *   U1  `{}` (and every other empty-ish wire value) decodes to `null`, so the M3
 *       block is never rendered for a plain task;
 *   U2  a real checkpoint — including one that is *only* parked — survives;
 *   U3  the decoded `budget` is always a complete object (this is the exact
 *       object whose absence used to throw);
 *   U4  `max_attempts` mirrors `Budget.from_dict`'s `or 2`, so the panel shows
 *       the budget the agent actually runs with;
 *   U5  no field can ever decode to `NaN` (it would render as the string "NaN");
 *   U6  a sparse or malformed payload degrades to defaults instead of throwing.
 *
 * Mutations verified — 11/11 killed; the ones this file catches
 * (script `C:/tmp/prov/mutate_taskplan.py`, report
 * `C:/tmp/prov/mutation_report.json`):
 *   MT2  drop the all-empty-placeholder discriminator   → U1
 *   MT3  let the decoded budget be nullable             → U3
 *   MT7  stop mirroring `Budget.from_dict`              → U3, U4
 *   MT8  stop whitelisting the milestone status         → normalizeMilestone
 *   MT9  trust a non-array `milestones` field           → U1, U2
 */
import { describe, expect, it } from 'vitest';

import type { TaskPlan } from '../services/api';
import { normalizeMilestone, normalizeTaskPlan } from './taskPlan';

/**
 * Most cases below deliberately feed the decoder a *malformed* payload — that
 * is the point of the file. Its parameter is typed as the wire shape, so a
 * partial budget object would be a compile error; going through `unknown`
 * mirrors the real boundary, where nothing validates the payload before it
 * lands.
 */
const decode = (raw: unknown): TaskPlan | null => normalizeTaskPlan(raw as never);

describe('U1 — the plain-task placeholder is not a checkpoint', () => {
  it('decodes the empty object the backend ships to null', () => {
    // The exact payload of a plain task: `{"plan": {}}`.
    expect(decode({})).toBeNull();
  });

  it('decodes every absent/empty wire value to null', () => {
    for (const raw of [null, undefined, 0, '', false, [], 'plan'] as unknown[]) {
      expect(decode(raw)).toBeNull();
    }
  });

  it('decodes a placeholder whose keys are all empty to null', () => {
    // An older build could send the skeleton with no values behind it.
    expect(
      decode({
        goal: '',
        status: '',
        blocked_reason: '',
        milestones: [],
      }),
    ).toBeNull();
  });
});

describe('U2 — a real checkpoint survives', () => {
  it('keeps a full goal plan', () => {
    const plan = decode({
      goal: 'ship it',
      status: 'running',
      spent_tokens: 120,
      started_at: 10,
      finished_at: null,
      blocked_reason: '',
      plan_done: 1,
      plan_total: 3,
      budget: { max_tokens: 5000, max_seconds: 60, max_attempts: 3 },
      milestones: [],
    });
    expect(plan).not.toBeNull();
    expect(plan!.goal).toBe('ship it');
    expect(plan!.status).toBe('running');
    expect(plan!.spent_tokens).toBe(120);
    expect(plan!.plan_done).toBe(1);
    expect(plan!.plan_total).toBe(3);
    expect(plan!.started_at).toBe(10);
    expect(plan!.finished_at).toBeNull();
  });

  it('keeps a parked goal that carries only a status and a reason', () => {
    // `blocked` is resumable: hiding this block would take away the only place
    // the "why did it park" message can appear.
    const plan = decode({
      status: 'blocked',
      blocked_reason: 'token budget exhausted',
    });
    expect(plan).not.toBeNull();
    expect(plan!.blocked_reason).toBe('token budget exhausted');
  });

  it('keeps a plan that carries only milestones', () => {
    const plan = decode({ milestones: [{ title: 'a' }] });
    expect(plan).not.toBeNull();
    expect(plan!.milestones).toHaveLength(1);
  });

  it('treats a bare budget object as a checkpoint', () => {
    expect(decode({ budget: { max_tokens: 10 } })).not.toBeNull();
  });
});

describe('U3 — the decoded budget is always complete', () => {
  it('fills a missing budget with readable zeros instead of undefined', () => {
    const plan = decode({ status: 'queued' });
    expect(plan!.budget).toEqual({ max_tokens: 0, max_seconds: 0, max_attempts: 2 });
  });

  it('always returns a budget object, never undefined', () => {
    for (const raw of [{ status: 'queued' }, { goal: 'g' }, { milestones: [{ title: 't' }] }]) {
      const plan = decode(raw);
      expect(typeof plan!.budget).toBe('object');
      // The exact read that white-screened the app must be total.
      expect(typeof plan!.budget.max_tokens).toBe('number');
    }
  });

  it('reads a filled budget unchanged', () => {
    const plan = decode({
      status: 'running',
      budget: { max_tokens: 5000, max_seconds: 120, max_attempts: 5 },
    });
    expect(plan!.budget).toEqual({ max_tokens: 5000, max_seconds: 120, max_attempts: 5 });
  });
});

describe('U4 — max_attempts mirrors Budget.from_dict', () => {
  it('defaults an absent or zero max_attempts to 2', () => {
    // goal_runner does `int(data.get("max_attempts") or 2)`: 0 is not "never
    // retry", it is the backend default. Rendering 0 here would misreport the
    // budget the agent is actually running with.
    for (const raw of [undefined, null, 0, '', 'x']) {
      const plan = decode({
        status: 'running',
        budget: { max_attempts: raw },
      });
      expect(plan!.budget.max_attempts).toBe(2);
    }
  });

  it('keeps a real max_attempts', () => {
    const plan = decode({ status: 'running', budget: { max_attempts: 7 } });
    expect(plan!.budget.max_attempts).toBe(7);
  });
});

describe('U5 — nothing decodes to NaN', () => {
  it('never yields NaN from junk numerics', () => {
    const plan = decode({
      goal: 'g',
      spent_tokens: 'lots',
      plan_done: {},
      plan_total: NaN,
      budget: { max_tokens: 'many', max_seconds: undefined, max_attempts: [] },
    });
    for (const n of [
      plan!.spent_tokens,
      plan!.plan_done,
      plan!.plan_total,
      plan!.budget.max_tokens,
      plan!.budget.max_seconds,
      plan!.budget.max_attempts,
    ]) {
      expect(Number.isNaN(n)).toBe(false);
      expect(Number.isInteger(n)).toBe(true);
    }
  });

  it('keeps a legitimate zero (0 means unlimited, not missing)', () => {
    const plan = decode({
      status: 'running',
      spent_tokens: 0,
      budget: { max_tokens: 0, max_seconds: 0 },
    });
    expect(plan!.spent_tokens).toBe(0);
    expect(plan!.budget.max_tokens).toBe(0);
    expect(plan!.budget.max_seconds).toBe(0);
  });

  it('keeps an epoch-0 timestamp (0 is a legal time)', () => {
    const plan = decode({ status: 'running', started_at: 0, finished_at: 12 });
    expect(plan!.started_at).toBe(0);
    expect(plan!.finished_at).toBe(12);
  });
});

describe('U6 — sparse and malformed payloads degrade instead of throwing', () => {
  it('turns a non-array milestones field into an empty list', () => {
    for (const raw of [null, undefined, {}, 'a', 3]) {
      const plan = decode({ status: 'running', milestones: raw });
      expect(plan!.milestones).toEqual([]);
    }
  });

  it('keeps non-string scalars out of the string fields', () => {
    const plan = decode({ goal: 42, status: {}, blocked_reason: [] });
    // Nothing survived as a string, so this is a placeholder after all.
    expect(plan).toBeNull();
  });

  it('does not mutate the payload it was handed', () => {
    const raw = { status: 'running', budget: { max_tokens: 10 }, milestones: [{ title: 'a' }] };
    const snapshot = JSON.stringify(raw);
    decode(raw);
    expect(JSON.stringify(raw)).toBe(snapshot);
  });
});

describe('normalizeMilestone', () => {
  it('defaults every field of an empty milestone', () => {
    const ms = normalizeMilestone({}, 2);
    expect(ms).toEqual({
      id: 'm3',
      title: '',
      prompt: '',
      verify: '',
      status: 'pending',
      attempts: 0,
      result: '',
      error: '',
      started_at: null,
      finished_at: null,
    });
  });

  it('mirrors title and prompt when only one is present', () => {
    // `milestones_from_payload` keeps them in sync; a sparse checkpoint should
    // not render a blank row.
    const a = normalizeMilestone({ title: 'only title' }, 0);
    expect(a.prompt).toBe('only title');
    const b = normalizeMilestone({ prompt: 'only prompt' }, 0);
    expect(b.title).toBe('only prompt');
  });

  it('rejects a status outside the known set', () => {
    // MilestoneRow feeds this straight into a MILESTONE_META lookup.
    for (const raw of ['running', 'done', 'blocked', 'pending']) {
      expect(normalizeMilestone({ status: raw }, 0).status).toBe(raw);
    }
    expect(normalizeMilestone({ status: 'exploded' }, 0).status).toBe('pending');
    expect(normalizeMilestone({ status: 7 }, 0).status).toBe('pending');
  });

  it('keeps an explicit id and coerces junk timestamps to null', () => {
    const ms = normalizeMilestone({ id: 'm9', started_at: 'x', finished_at: 4 }, 0);
    expect(ms.id).toBe('m9');
    expect(ms.started_at).toBeNull();
    expect(ms.finished_at).toBe(4);
  });

  it('accepts a non-object entry without throwing', () => {
    for (const raw of [null, undefined, 'x', 1, []]) {
      expect(normalizeMilestone(raw, 0).status).toBe('pending');
    }
  });
});
