/**
 * The rows for the task strip above the composer.
 *
 * The strip is an aggregate across groups: every task still running, plus anything that finished
 * within the last day, so a completed task stays visible long enough to be looked at without
 * crowding the strip forever.
 */
import type { CollabBoardTask } from '../services/api';

export const RECENT_FINISHED_MS = 24 * 60 * 60 * 1000;

export type TaskStripState = 'running' | 'finished';

export interface TaskStripRow {
  taskId: string;
  name: string;
  state: TaskStripState;
  progress: number;
  endedAtMs: number | null;
}

const FINISHED_STATES = new Set(['done', 'failed', 'archived']);

function endedAtMs(task: CollabBoardTask): number | null {
  const raw = String(task?.ended_at || task?.closed_at || '');
  if (!raw) return null;
  const ms = Date.parse(raw);
  return Number.isFinite(ms) ? ms : null;
}

export function selectStripTasks(
  tasks: CollabBoardTask[] | null | undefined,
  nowMs: number,
  withinMs: number = RECENT_FINISHED_MS,
): TaskStripRow[] {
  const rows: TaskStripRow[] = [];
  for (const task of tasks ?? []) {
    const taskId = String(task?.task_id ?? '').trim();
    if (!taskId) continue;
    const status = String(task?.status ?? '');
    if (FINISHED_STATES.has(status)) {
      const ended = endedAtMs(task);
      if (ended === null || nowMs - ended > withinMs) continue;
      rows.push({
        taskId,
        name: String(task?.task_name ?? taskId),
        state: 'finished',
        progress: 100,
        endedAtMs: ended,
      });
      continue;
    }
    if (status === 'stale') continue;
    const progress = Number(task?.progress ?? 0);
    rows.push({
      taskId,
      name: String(task?.task_name ?? taskId),
      state: 'running',
      progress: Number.isFinite(progress) ? Math.max(0, Math.min(100, Math.round(progress))) : 0,
      endedAtMs: null,
    });
  }
  return rows.sort((a, b) => {
    if (a.state !== b.state) return a.state === 'running' ? -1 : 1;
    if (a.state === 'finished') return (b.endedAtMs ?? 0) - (a.endedAtMs ?? 0);
    return a.name.localeCompare(b.name);
  });
}
