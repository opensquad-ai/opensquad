// @vitest-environment jsdom
/**
 * `TaskDetail` render lock — the regression test that would have caught the
 * blank screen.
 *
 * Reported symptom: opening *any* parallel task killed the app with
 *
 *     Uncaught TypeError: Cannot read properties of undefined (reading 'max_tokens')
 *         at TaskDetail (TaskPanelPage.tsx:786:34)
 *
 * because `Task.to_dict()` ships `"plan": {}` for a plain task and the panel
 * dereferenced it: `{}` is truthy, so the `{plan && …}` guard opened the M3
 * budget grid onto an object with no `budget` key. A render throw unmounts the
 * React root, which is why the whole page went white rather than the panel.
 *
 * No pure-function test can catch a missing null-check on a wire field, so this
 * one renders the real component. `renderToStaticMarkup` is deliberate: the
 * crash happened during render, and effects (the worktree-report fetch) must not
 * run, so the test stays offline and deterministic.
 *
 * Mutations verified — 11/11 killed, this file among the catchers for four of
 * them (script `C:/tmp/prov/mutate_taskplan.py`, report
 * `C:/tmp/prov/mutation_report.json`):
 *   MT1  `normalizeTaskPlan(task.plan)` → `(task.plan || null)`   → 4 failures
 *        here, reproducing the reported `TypeError … reading 'max_tokens'` at
 *        the same column as the original bug report
 *   MT2  drop the all-empty-placeholder discriminator             → 1 failure
 *   MT3  `budget: {…}` → `budget: budget as never` (nullable)      → 1 failure
 *   MT9  trust a non-array `milestones` instead of validating it   → 1 failure
 *   MT11 `const plan = task.plan as never` (skip the decoder)      → 2 failures
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { TaskDetail } from './TaskPanelPage';
import type { ParallelTask } from '../../services/api';
import i18n from '../../i18n';

const noop = () => {};

const task = (over: Partial<ParallelTask> = {}): ParallelTask => ({
  task_id: 't1',
  session_id: 's1',
  agent_id: 'a1',
  title: '重构登录模块',
  // No worktree → the change-report section stays closed and never fetches.
  worktree_path: '',
  base_dir: '',
  status: 'done',
  origin: 'manual',
  prompt: '把登录模块拆开',
  created_at: 1_700_000_000,
  started_at: null,
  finished_at: null,
  plan_done: 0,
  plan_total: 0,
  cost: { tokens: 0, elapsed_ms: 0 },
  error: '',
  result_summary: '',
  kind: 'task',
  ...over,
});

const render = (t: ParallelTask) =>
  renderToStaticMarkup(
    React.createElement(TaskDetail, {
      task: t,
      repo: '',
      onAbort: noop,
      onApprove: noop,
      onRemove: noop,
      onResume: noop,
    }),
  );

/** The M3 budget block's own label — present only when a checkpoint exists. */
const BUDGET_LABEL = i18n.t('taskPanel.budgetTokens');

describe('P1 — a plain task renders (the exact payload that blanked the app)', () => {
  it('renders a task whose plan is the empty placeholder object', () => {
    // `{"plan": {}}` — the literal response for every non-goal task.
    const html = render(task({ plan: {} }));
    expect(html).toContain('重构登录模块');
    expect(html).not.toContain(BUDGET_LABEL);
  });

  it('renders when the plan key is absent entirely', () => {
    // Older packed backends, or a payload assembled elsewhere.
    const html = render(task({ plan: undefined }));
    expect(html).toContain('重构登录模块');
    expect(html).not.toContain(BUDGET_LABEL);
  });

  it('renders an explicit null plan', () => {
    const html = render(task({ plan: null }));
    expect(html).toContain('重构登录模块');
    expect(html).not.toContain(BUDGET_LABEL);
  });

  it('renders a placeholder carrying the progress counters but no checkpoint', () => {
    // plan_done/plan_total are top-level task fields and are set for plain
    // tasks too; they must not be mistaken for a checkpoint.
    const html = render(task({ plan: {}, plan_done: 1, plan_total: 1 }));
    expect(html).toContain('重构登录模块');
    expect(html).not.toContain(BUDGET_LABEL);
  });
});

describe('P2 — a goal renders its checkpoint', () => {
  const goal = task({
    kind: 'goal',
    status: 'blocked',
    plan_done: 1,
    plan_total: 2,
    plan: {
      goal: 'ship it',
      status: 'blocked',
      spent_tokens: 120,
      started_at: 10,
      finished_at: null,
      blocked_reason: 'token budget exhausted',
      plan_done: 1,
      plan_total: 2,
      budget: { max_tokens: 5000, max_seconds: 0, max_attempts: 3 },
      milestones: [
        {
          id: 'm1',
          title: '切分模块',
          prompt: '切分模块',
          verify: 'tests green',
          status: 'done',
          attempts: 1,
          result: '',
          error: '',
          started_at: 10,
          finished_at: 11,
        },
        {
          id: 'm2',
          title: '迁移调用方',
          prompt: '迁移调用方',
          verify: '',
          status: 'pending',
          attempts: 0,
          result: '',
          error: '',
          started_at: null,
          finished_at: null,
        },
      ],
    },
  });

  it('renders the budget, the milestones and the parked reason', () => {
    const html = render(goal);
    expect(html).toContain(BUDGET_LABEL);
    // Locale-independent assertions: the literal numbers and titles.
    expect(html).toContain('120 / 5000');
    expect(html).toContain('切分模块');
    expect(html).toContain('迁移调用方');
    expect(html).toContain('token budget exhausted');
  });

  it('renders a plan whose budget object is missing', () => {
    // A checkpoint from an older schema: real, but with no budget behind it.
    const html = render(
      task({
        kind: 'goal',
        plan: { goal: 'g', status: 'running', spent_tokens: 9 } as never,
      }),
    );
    // `max_tokens` is 0, so the label falls back to the spent count alone.
    expect(html).toContain(BUDGET_LABEL);
    expect(html).toContain('9');
  });

  it('renders a parked plan with nothing but a status and a reason', () => {
    const html = render(
      task({
        kind: 'goal',
        status: 'blocked',
        plan: { status: 'blocked', blocked_reason: 'milestone unverified' } as never,
      }),
    );
    expect(html).toContain('milestone unverified');
  });
});

/**
 * A finished task used to offer *nothing* to click: the header actions are all
 * state-gated (abort/approve/resume/remove), and `session_id` was rendered as
 * inert text, so the tool flow of a completed run was unreachable from the
 * panel. The button is the only action a done task has.
 */
describe('P3 — a finished task leads into its tool flow', () => {
  const FLOW_LABEL = i18n.t('taskPanel.viewFlow');

  it('offers the way in once a session is bound', () => {
    const html = render(task({ status: 'done', session_id: 's1' }));
    expect(html).toContain(FLOW_LABEL);
    // The session id itself is the second way in (rendered as a button).
    expect(html).toContain('s1');
  });

  it('offers it for an M3 goal too, not just a plain task', () => {
    const html = render(
      task({ kind: 'goal', status: 'blocked', session_id: 's9', plan: { status: 'blocked' } as never }),
    );
    expect(html).toContain(FLOW_LABEL);
  });

  it('stays out of the way when no session was ever bound', () => {
    // Queued, or failed before the first turn: there is no flow to show, and a
    // button that opens an empty tab is worse than no button.
    const html = render(task({ status: 'queued', session_id: '' }));
    expect(html).not.toContain(FLOW_LABEL);
    expect(html).toContain('--');
  });
});
