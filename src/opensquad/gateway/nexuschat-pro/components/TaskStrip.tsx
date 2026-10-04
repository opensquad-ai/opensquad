import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { openCollabTaskWindow } from './CollabTaskCard';
import type { CollabBoardTask } from '../services/api';
import { selectStripTasks } from '../utils/taskStrip';

interface TaskStripProps {
  tasks: CollabBoardTask[];
  nowMs?: number;
}

/**
 * Where the group's tasks live while messages pile up above them. A task window opened from a
 * message scrolls away; this stays put, and shows every task, not only this group's.
 */
export function TaskStrip({ tasks, nowMs }: TaskStripProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(true);
  const rows = selectStripTasks(tasks, nowMs ?? Date.now());

  if (rows.length === 0) return null;

  return (
    <div
      data-testid="task-strip"
      className="flex flex-col mb-1 md:mb-2 px-2 py-1.5 md:p-3 bg-indigo-50 rounded-md md:rounded-lg border-l-2 md:border-l-4 border-primary animate-in fade-in slide-in-from-bottom-2 duration-200"
    >
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          className="flex items-center gap-1.5 text-xs md:text-sm font-medium text-textMain"
        >
          <span>{t('taskStrip.title', { defaultValue: '进行中的任务' })}</span>
          <span className="text-textSecondary tabular-nums">{rows.length}</span>
          <span className={`transition-transform ${expanded ? 'rotate-180' : ''}`}>⌄</span>
        </button>
        <span className="text-[11px] text-textSecondary">
          {t('taskStrip.within', { defaultValue: '含 24 小时内结束' })}
        </span>
      </div>
      {expanded && (
        <ul className="mt-1.5 space-y-1 max-h-32 overflow-y-auto">
          {rows.map((row) => (
            <li key={row.taskId}>
              <button
                type="button"
                data-task-id={row.taskId}
                onClick={() => openCollabTaskWindow(row.taskId)}
                title={row.name}
                className="w-full flex items-center gap-2 px-1 py-0.5 rounded text-left text-xs md:text-sm hover:bg-white/70 transition-colors"
              >
                <span
                  aria-hidden="true"
                  className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                    row.state === 'running' ? 'bg-emerald-500' : 'bg-gray-400'
                  }`}
                />
                <span className="flex-1 truncate">{row.name}</span>
                {row.state === 'running' && (
                  <span className="text-textSecondary tabular-nums">{row.progress}%</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
