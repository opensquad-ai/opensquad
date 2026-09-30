// @vitest-environment jsdom
/**
 * 定时任务 → 任务详情 的执行历史（用户报告 2026-09-30：详情页看不到该任务跑过什么）。
 *
 * The detail pane showed Run count = 90 with no way to see any of those 90 runs;
 * the history only existed in the 执行 tab, mixed across every task. The pane now
 * lists this task's runs (newest first) and each row opens that run's workflow,
 * which is where the user can actually read what happened.
 *
 *   L1  the detail lists only the selected task's runs (the other task's run
 *       timestamp never appears)
 *   L2  the run count in the header matches the rows listed
 *   L3  each row shows its duration and status, and clicking it opens THAT run
 *   L4  an empty task says so instead of rendering a blank section
 *   L5  every key the pane uses exists in zh.json AND en.json
 *
 * `React.createElement` on purpose: the vitest `include` glob is `**\/*.test.ts`.
 */
import fs from 'node:fs';
import path from 'node:path';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../../i18n';
import type { ScheduledExecution, ScheduledTask } from '../../services/api';

const list = vi.fn();
const executions = vi.fn();

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  return {
    ...actual,
    scheduledTaskAPI: {
      list: (...args: unknown[]) => list(...args),
      executions: (...args: unknown[]) => executions(...args),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      runNow: vi.fn(),
      setEnabled: vi.fn(),
      sendFollowup: vi.fn(),
      stopExecution: vi.fn(),
      removeExecution: vi.fn(),
    },
  };
});

vi.mock('../../services/aiWebSocket', () => ({
  getAiWsService: () => ({ connect: () => undefined, on: () => () => undefined }),
}));

// The execution workflow is a different pane; this file only pins that the
// history row routes to it with the right id.
vi.mock('./ExecWorkflowView', () => ({
  ExecWorkflowView: ({ exec }: { exec: ScheduledExecution }) =>
    React.createElement('div', { 'data-testid': 'exec-view' }, exec.id),
}));

import { ScheduledTasksPage } from './ScheduledTasksPage';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;
const ROOT = path.resolve(__dirname, '../..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const DAY = 86400;
const NOW = Math.floor(Date.parse('2026-09-30T04:00:00Z') / 1000);

const taskA: ScheduledTask = {
  id: 't1',
  name: '每日a股股票市场报告',
  prompt: '每日报告a股股票市场的行情以及主要驱动事件。',
  workspace: 'C:/ai_test/work',
  delegate_agent: 'agent305',
  model_card: 'ark__glm-5.3-flash',
  skills: [],
  schedule: { type: 'daily', time: '09:00' },
  enabled: true,
  created_at: NOW - 10 * DAY,
  updated_at: NOW - DAY,
  last_run_ts: NOW - DAY,
  last_status: 'success',
  next_run_ts: NOW + DAY,
  run_count: 2,
};

const taskB: ScheduledTask = {
  ...taskA,
  id: 't2',
  name: '每周市场报告',
  prompt: '每周市场报告。',
  schedule: { type: 'weekly', time: '19:12', weekdays: '1' },
  enabled: false,
  last_run_ts: NOW - 20 * DAY,
  next_run_ts: null,
  run_count: 1,
};

const exec = (over: Partial<ScheduledExecution>): ScheduledExecution => ({
  id: 'e',
  task_id: 't1',
  task_name: taskA.name,
  started_at: NOW,
  ended_at: NOW + 1,
  status: 'success',
  session_id: null,
  error: null,
  ...over,
});

// Ascending, the order the gateway returns: the pane reverses it to newest first.
const runOlder = exec({ id: 'e-older', started_at: NOW - DAY, ended_at: NOW - DAY + 30, status: 'failed', error: 'boom' });
const runNewer = exec({ id: 'e-newer', started_at: NOW - 3600, ended_at: NOW - 3600 + 90, status: 'success', manual: true });
const runOtherTask = exec({
  id: 'e-other',
  task_id: 't2',
  task_name: taskB.name,
  started_at: NOW - 20 * DAY,
  ended_at: null,
  status: 'running',
});

let container: HTMLDivElement;
let root: Root;

async function mount(): Promise<void> {
  list.mockResolvedValue({ tasks: [taskA, taskB] });
  executions.mockResolvedValue({ executions: [runOlder, runNewer, runOtherTask] });
  await act(async () => {
    root.render(h(ScheduledTasksPage, { agentName: 'agent305', rootPath: 'C:/ai_test/work' }));
  });
  // The first task is selected by default; its runs come from the same payload.
  await act(async () => {});
}

const historyRows = () => [...container.querySelectorAll<HTMLElement>('button[title*="查看这次执行"]')];

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    await i18n.changeLanguage('zh');
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe('task detail run history', () => {
  it('L1+L2: lists only this task runs, newest first, with a matching count', async () => {
    await mount();
    const rows = historyRows();
    expect(rows, 'the detail pane listed no runs').toHaveLength(2);
    expect(rows[0].textContent, 'newest run must come first').toContain('2026-09-30');
    expect(rows[1].textContent).toContain('2026-09-29');
    // The other task's run (20 days back) must not leak into this pane.
    expect(container.textContent).not.toContain('2026-09-10');
    expect(container.textContent).toContain('共 2 次');
  });

  it('L3: shows duration + status, and opens that run when clicked', async () => {
    await mount();
    const rows = historyRows();
    expect(rows[0].textContent, 'the 90s run must show its duration').toContain('1m 30s');
    expect(rows[0].textContent).toContain('成功');
    expect(rows[1].textContent).toContain('失败');
    expect(rows[0].textContent, 'a manual run must be marked').toContain('手动');

    await act(async () => {
      rows[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const view = container.querySelector('[data-testid="exec-view"]');
    expect(view?.textContent, 'the row did not open its own run').toBe('e-newer');
  });

  it('L4: a task with no runs says so', async () => {
    list.mockResolvedValue({ tasks: [{ ...taskA, id: 't9', name: '从未跑过', run_count: 0 }] });
    executions.mockResolvedValue({ executions: [] });
    await act(async () => {
      root.render(h(ScheduledTasksPage, { agentName: 'agent305', rootPath: 'C:/ai_test/work' }));
    });
    await act(async () => {});
    expect(container.textContent).toContain('执行历史');
    expect(container.textContent).toContain('暂无执行记录');
    expect(historyRows()).toHaveLength(0);
  });

  it('L6: a trimmed history says how many of the total it shows', async () => {
    // The store keeps a window (≤200) while run_count keeps counting: the pane
    // must not claim "共 2 次" for a task that has run 90 times.
    list.mockResolvedValue({ tasks: [{ ...taskA, run_count: 90 }] });
    executions.mockResolvedValue({ executions: [runOlder, runNewer] });
    await act(async () => {
      root.render(h(ScheduledTasksPage, { agentName: 'agent305', rootPath: 'C:/ai_test/work' }));
    });
    await act(async () => {});
    expect(historyRows()).toHaveLength(2);
    expect(container.textContent).toContain('显示最近 2 次，共 90 次');
    expect(container.textContent).not.toContain('共 2 次');
  });

  it('L7: the history is a bounded box split by day', async () => {
    await mount();
    const box = container.querySelector<HTMLElement>('[data-testid="run-history"]');
    expect(box, 'the history is not its own scroll box').toBeTruthy();
    expect(box!.className, 'the history box must cap its height').toContain('max-h-');
    expect(box!.className, 'the history box must scroll').toContain('overflow-y-auto');

    // One divider per local day, each ABOVE the runs it introduces.
    const kids = [...box!.children];
    expect(
      kids.map(k => k.getAttribute('data-testid') || 'row'),
      'runs are not split by day dividers',
    ).toEqual(['run-history-day', 'row', 'run-history-day', 'row']);
    const dayLabels = kids
      .filter(k => k.getAttribute('data-testid') === 'run-history-day')
      .map(k => (k.textContent || '').trim());
    expect(dayLabels.every(l => l.length > 0)).toBe(true);
    expect(new Set(dayLabels).size, 'both days must be labelled distinctly').toBe(2);
    // The 90s run is the newer one, so it lands in the first (newest) day group.
    expect(kids[1].textContent).toContain('1m 30s');
    expect(kids[3].textContent).toContain('30s');
  });

  it('L5: every scheduledTasks key the page uses exists in both locales', () => {
    const zh = JSON.parse(read('locales/zh.json'));
    const en = JSON.parse(read('locales/en.json'));
    for (const key of ['manual', 'runNow']) {
      expect(zh.scheduledTasks[key], `zh.scheduledTasks.${key}`).toBeTruthy();
      expect(en.scheduledTasks[key], `en.scheduledTasks.${key}`).toBeTruthy();
    }
    for (const group of ['info', 'history', 'status'] as const) {
      for (const key of Object.keys(zh.scheduledTasks[group])) {
        expect(en.scheduledTasks[group][key], `en.scheduledTasks.${group}.${key} is missing`).toBeTruthy();
      }
    }
    // The new labels must be Chinese in zh, not the English text copied over.
    for (const key of ['title', 'open'] as const) {
      expect(zh.scheduledTasks.history[key]).toMatch(/[\u4e00-\u9fff]/);
    }
  });
});
