import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { openCollabTaskWindow } from './CollabTaskCard';
import type { CollabBoardTask } from '../services/api';
import { selectStripTasks } from '../utils/taskStrip';

interface TaskStripProps {
  tasks: CollabBoardTask[];
  nowMs?: number;
}

/**
 * The tasks that are running, plus anything finished in the last day — across every group, so a
 * task opened from a message does not scroll out of reach. It lives on the composer's toolbar line
 * as a compact pill and opens upward, because the composer sits at the bottom of the screen.
 */
export function TaskStrip({ tasks, nowMs }: TaskStripProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const rows = selectStripTasks(tasks, nowMs ?? Date.now());
  const running = rows.filter((row) => row.state === 'running').length;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  if (rows.length === 0) return null;

  return (
    <div ref={rootRef} data-testid="task-strip" className="relative">
      <button
        type="button"
        data-task-strip-trigger
        aria-expanded={open}
        onClick={(e) => {
          e.preventDefault();
          setOpen((value) => !value);
        }}
        onMouseDown={(e) => e.preventDefault()}
        title={t('taskStrip.title', { defaultValue: '进行中的任务' })}
        className={`flex items-center gap-1.5 h-6 pl-1.5 pr-2 rounded-full border text-xs font-medium transition-colors ${
          open
            ? 'bg-primary/10 border-primary/40 text-primary'
            : 'bg-white/70 border-border text-textMain hover:bg-white hover:border-primary/30'
        }`}
      >
        <span
          className={`w-1.5 h-1.5 rounded-full ${running > 0 ? 'bg-emerald-500' : 'bg-gray-300'}`}
          aria-hidden="true"
        />
        <span className="tabular-nums">{running}</span>
        <svg
          viewBox="0 0 12 12"
          className={`w-2.5 h-2.5 transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden="true"
        >
          <path d="M2 7.5 6 3.5l4 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className="absolute bottom-full left-0 mb-2 w-80 max-w-[min(22rem,80vw)] z-50 rounded-2xl bg-white/95 backdrop-blur shadow-xl ring-1 ring-black/5 overflow-hidden animate-in fade-in slide-in-from-bottom-2 duration-150">
          <div className="flex items-baseline justify-between px-3.5 pt-3 pb-2">
            <span className="text-sm font-semibold text-textMain">
              {t('taskStrip.title', { defaultValue: '进行中的任务' })}
            </span>
            <span className="text-[11px] text-textSecondary">
              {t('taskStrip.within', { defaultValue: '含 24 小时内结束' })}
            </span>
          </div>
          <ul className="px-2 pb-2 max-h-72 overflow-y-auto">
            {rows.map((row) => (
              <li key={row.taskId}>
                <button
                  type="button"
                  data-task-id={row.taskId}
                  onClick={() => {
                    setOpen(false);
                    openCollabTaskWindow(row.taskId);
                  }}
                  title={row.name}
                  className="group w-full flex items-center gap-2.5 px-2 py-2 rounded-xl text-left hover:bg-primary/5 transition-colors"
                >
                  <span
                    aria-hidden="true"
                    className={`w-2 h-2 rounded-full shrink-0 ${
                      row.state === 'running'
                        ? 'bg-emerald-500 ring-4 ring-emerald-500/15'
                        : 'bg-gray-300 ring-4 ring-gray-300/20'
                    }`}
                  />
                  <span className="flex-1 min-w-0">
                    <span className="block truncate text-sm text-textMain group-hover:text-primary transition-colors">
                      {row.name}
                    </span>
                    {row.state === 'running' && (
                      <span className="mt-1 block h-1 w-full rounded-full bg-border/70 overflow-hidden">
                        <span
                          className="block h-full rounded-full bg-gradient-to-r from-emerald-400 to-primary transition-all"
                          style={{ width: `${row.progress}%` }}
                        />
                      </span>
                    )}
                  </span>
                  {row.state === 'running' ? (
                    <span className="shrink-0 text-[11px] tabular-nums text-textSecondary">
                      {row.progress}%
                    </span>
                  ) : (
                    <span className="shrink-0 text-[11px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500">
                      {t('taskStrip.finished', { defaultValue: '已结束' })}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
