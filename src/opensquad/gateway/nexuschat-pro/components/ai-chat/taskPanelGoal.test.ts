// @vitest-environment jsdom
//
// Regression lock for the M3 goal surface of the task panel.
//
// Two pure helpers carry the whole M3 submit path and nothing else tests them:
//
//   · isResumableGoal — decides whether the detail header renders the 继续
//     button. Get it wrong one way and a parked goal has no way back (the user
//     watches a violet "blocked" badge with no action); wrong the other way and
//     a plain `failed` task offers a resume that the agent will reject.
//
//   · buildSubmitPayload — turns the form into the request body. The backend
//     validates `budget` as ints, so a blank or garbage field MUST become 0
//     ("no limit"), never NaN/undefined: a NaN would fail pydantic validation
//     and surface as an opaque 422 on submit — after the user filled the form.
//     Blank must never be sent as some other magic number either, because 0 is
//     the one value the backend reads as "unlimited".
//
// The UI literal `max_attempts` fallback is pinned to 2 to match
// `opensquad/tasks/goal_runner.py::Budget.max_attempts` — if that default
// changes, this test is the tripwire.

import { describe, expect, it } from 'vitest';

import {
  buildSubmitPayload,
  EMPTY_TASK_FORM,
  isResumableGoal,
  type TaskFormState,
} from './TaskPanelPage';

const form = (over: Partial<TaskFormState> = {}): TaskFormState => ({
  ...EMPTY_TASK_FORM,
  ...over,
});

const goalForm = (over: Partial<TaskFormState> = {}): TaskFormState =>
  form({ kind: 'goal', title: 'Ship it', prompt: 'ship the thing', ...over });

describe('isResumableGoal', () => {
  it('offers resume for a goal parked on its budget', () => {
    expect(isResumableGoal({ kind: 'goal', status: 'blocked' })).toBe(true);
  });

  it('offers resume for an interrupted goal', () => {
    expect(isResumableGoal({ kind: 'goal', status: 'interrupted' })).toBe(true);
  });

  it('never offers resume for a plain task, even a blocked one', () => {
    // A plain task has no plan to continue from — resume would be a no-op.
    expect(isResumableGoal({ kind: 'task', status: 'blocked' })).toBe(false);
    expect(isResumableGoal({ kind: 'task', status: 'interrupted' })).toBe(false);
  });

  it('treats a missing kind as a plain task', () => {
    expect(isResumableGoal({ status: 'blocked' })).toBe(false);
  });

  it('does not offer resume while a goal is still working', () => {
    for (const status of ['queued', 'running', 'waiting_approval'] as const) {
      expect(isResumableGoal({ kind: 'goal', status })).toBe(false);
    }
  });

  it('does not offer resume for a goal that ended for good', () => {
    // `failed` is not resumable: the milestone could not be advanced. Only a
    // parked (`blocked`/`interrupted`) goal may legally be continued.
    for (const status of ['done', 'failed', 'aborted'] as const) {
      expect(isResumableGoal({ kind: 'goal', status })).toBe(false);
    }
  });
});

describe('buildSubmitPayload — plain task', () => {
  it('sends only title/prompt/use_worktree', () => {
    const p = buildSubmitPayload(form({ title: '  T  ', prompt: ' P ' }));
    expect(p).toEqual({ title: 'T', prompt: 'P', use_worktree: true });
  });

  it('omits the goal fields entirely', () => {
    const p = buildSubmitPayload(form({ milestones: 'a\nb', maxTokens: '100' })) as unknown as
      Record<string, unknown>;
    for (const key of ['kind', 'goal', 'milestones', 'budget']) {
      expect(key in p).toBe(false);
    }
  });
});

describe('buildSubmitPayload — goal', () => {
  it('marks the kind and mirrors the prompt as the goal', () => {
    const p = buildSubmitPayload(goalForm({ prompt: '  ship the thing  ' }));
    expect(p.kind).toBe('goal');
    expect(p.title).toBe('Ship it');
    // Same string: the backend may read either field, they must not diverge.
    expect(p.goal).toBe(p.prompt);
    expect(p.prompt).toBe('ship the thing');
  });

  it('splits milestones one per line, trimming and dropping blanks', () => {
    const p = buildSubmitPayload(
      goalForm({ milestones: '  first \n\n\t\n second  \nthird' }),
    );
    expect(p.milestones).toEqual(['first', 'second', 'third']);
  });

  it('sends an empty milestone list when the box is untouched', () => {
    // The backend treats that as one implicit milestone covering the goal.
    expect(buildSubmitPayload(goalForm()).milestones).toEqual([]);
  });

  it('defaults attempts to 2, matching Budget.max_attempts in goal_runner.py', () => {
    const p = buildSubmitPayload(goalForm());
    expect(p.budget?.max_attempts).toBe(2);
  });

  it('reads a filled budget', () => {
    const p = buildSubmitPayload(
      goalForm({ maxTokens: '5000', maxSeconds: '120', maxAttempts: '5' }),
    );
    expect(p.budget).toEqual({ max_tokens: 5000, max_seconds: 120, max_attempts: 5 });
  });

  it('maps every unusable budget value to 0 (the backend reads 0 as no limit)', () => {
    for (const junk of ['', '   ', 'abc', 'NaN', 'Infinity', '-3', '0']) {
      const p = buildSubmitPayload(
        goalForm({ maxTokens: junk, maxSeconds: junk, maxAttempts: junk }),
      );
      // max_attempts keeps its 2 fallback; the others must be the literal 0.
      expect(Number.isNaN(p.budget?.max_tokens)).toBe(false);
      expect(Number.isNaN(p.budget?.max_seconds)).toBe(false);
      expect(p.budget?.max_tokens).toBe(0);
      expect(p.budget?.max_seconds).toBe(0);
      expect(p.budget?.max_attempts).toBe(2);
    }
  });

  it('truncates a decimal instead of dropping the field', () => {
    // `num` parses the leading integer, so 3.9 → 3 rather than 0. A number
    // input cannot emit '3.9px', but pinning the leniency stops a rewrite of
    // `num` from silently turning a real budget into "unlimited".
    const p = buildSubmitPayload(goalForm({ maxTokens: '3.9', maxSeconds: '2.5' }));
    expect(p.budget?.max_tokens).toBe(3);
    expect(p.budget?.max_seconds).toBe(2);
  });

  it('never lets a NaN reach the request body', () => {
    const p = buildSubmitPayload(goalForm({ maxTokens: 'x', maxSeconds: 'x' }));
    expect(JSON.stringify(p)).not.toContain('null');
    expect(JSON.stringify(p)).not.toContain('NaN');
    for (const n of [p.budget?.max_tokens, p.budget?.max_seconds, p.budget?.max_attempts]) {
      expect(Number.isInteger(n)).toBe(true);
    }
  });
});

describe('buildSubmitPayload — purity', () => {
  it('does not mutate the form it was handed', () => {
    const f = goalForm({ milestones: 'a\nb', maxTokens: '9' });
    const snapshot = { ...f };
    buildSubmitPayload(f);
    expect(f).toEqual(snapshot);
  });

  it('is not tripped by a form whose kind flips back to task', () => {
    // The UI toggles kind on the same state object; stale goal fields must not
    // leak into a plain-task submit.
    const p = buildSubmitPayload(
      goalForm({ milestones: 'a', maxTokens: '10', kind: 'task' }),
    ) as unknown as Record<string, unknown>;
    expect('budget' in p).toBe(false);
    expect('milestones' in p).toBe(false);
  });
});

describe('EMPTY_TASK_FORM', () => {
  it('starts as an unlimited plain task with a worktree', () => {
    expect(EMPTY_TASK_FORM).toEqual({
      title: '',
      prompt: '',
      use_worktree: true,
      kind: 'task',
      milestones: '',
      maxTokens: '',
      maxSeconds: '',
      maxAttempts: '',
    });
  });
});
