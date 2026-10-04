/**
 * Which tasks the strip shows: everything still running, plus what finished in the last day.
 */
import { describe, expect, it } from 'vitest';

import { RECENT_FINISHED_MS, selectStripTasks } from './taskStrip';

const NOW = Date.parse('2026-10-04T12:00:00Z');
const hoursAgo = (h: number) => new Date(NOW - h * 60 * 60 * 1000).toISOString();

const task = (over: any) => ({
  task_id: 'AAAAAA',
  task_name: 'a task',
  status: 'active',
  progress: 0,
  created_at: hoursAgo(48),
  updated_at: hoursAgo(1),
  ...over,
});

describe('selectStripTasks', () => {
  it('keeps every running task', () => {
    const rows = selectStripTasks([task({ task_id: 'A' }), task({ task_id: 'B', progress: 40 })], NOW);

    expect(rows.map((r) => r.taskId)).toEqual(['A', 'B']);
    expect(rows.every((r) => r.state === 'running')).toBe(true);
    expect(rows[1].progress).toBe(40);
  });

  it('keeps a task finished within the last day', () => {
    const rows = selectStripTasks(
      [task({ task_id: 'DONE', status: 'done', ended_at: hoursAgo(2) })],
      NOW,
    );

    expect(rows).toHaveLength(1);
    expect(rows[0].state).toBe('finished');
    expect(rows[0].progress).toBe(100);
  });

  it('drops a task that finished longer ago than that, and one with no end time', () => {
    const rows = selectStripTasks(
      [
        task({ task_id: 'OLD', status: 'done', ended_at: hoursAgo(30) }),
        task({ task_id: 'NOEND', status: 'done' }),
      ],
      NOW,
    );

    expect(rows).toEqual([]);
    expect(RECENT_FINISHED_MS).toBe(24 * 60 * 60 * 1000);
  });

  it('takes closed_at when there is no ended_at', () => {
    const rows = selectStripTasks(
      [task({ task_id: 'CLOSED', status: 'failed', closed_at: hoursAgo(3) })],
      NOW,
    );

    expect(rows.map((r) => r.taskId)).toEqual(['CLOSED']);
  });

  it('leaves out stale tasks and entries without an id', () => {
    const rows = selectStripTasks([task({ task_id: 'S', status: 'stale' }), task({ task_id: '' })], NOW);

    expect(rows).toEqual([]);
  });

  it('shows running tasks first, then the most recently finished', () => {
    const rows = selectStripTasks(
      [
        task({ task_id: 'F-OLD', status: 'done', ended_at: hoursAgo(20) }),
        task({ task_id: 'RUN', task_name: 'zzz' }),
        task({ task_id: 'F-NEW', status: 'done', ended_at: hoursAgo(1) }),
      ],
      NOW,
    );

    expect(rows.map((r) => r.taskId)).toEqual(['RUN', 'F-NEW', 'F-OLD']);
  });

  it('rounds and clamps progress', () => {
    const rows = selectStripTasks(
      [task({ task_id: 'P1', progress: 12.6 }), task({ task_id: 'P2', progress: 250 }), task({ task_id: 'P3', progress: -5 })],
      NOW,
    );

    expect(rows.map((r) => r.progress)).toEqual([13, 100, 0]);
  });

  it('survives a task with no status at all, and an empty list', () => {
    const rows = selectStripTasks([task({ task_id: 'X', status: undefined })], NOW);

    expect(rows.map((r) => r.taskId)).toEqual(['X']);
    expect(selectStripTasks(null, NOW)).toEqual([]);
    expect(selectStripTasks([], NOW)).toEqual([]);
  });
});
