/**
 * Outer fold for a complex-task process (between last user message and
 * to_user_end_task report). Text-style toggle — no card chrome.
 */
import React, { useState } from 'react';

export interface TaskFoldBlockProps {
  title?: string;
  messageCount: number;
  eventCount: number;
  defaultCollapsed?: boolean;
  isSolo?: boolean;
  children: React.ReactNode;
}

export const TaskFoldBlock: React.FC<TaskFoldBlockProps> = ({
  title,
  messageCount,
  eventCount,
  defaultCollapsed = true,
  isSolo = false,
  children,
}) => {
  const [open, setOpen] = useState(!defaultCollapsed);
  const parts: string[] = [];
  if (eventCount > 0) parts.push(`${eventCount} step${eventCount === 1 ? '' : 's'}`);
  if (messageCount > 0) parts.push(`${messageCount} notice${messageCount === 1 ? '' : 's'}`);
  const summary = parts.length > 0 ? parts.join(' · ') : 'process';
  const label = title ? `${title} — ${summary}` : `Task process — ${summary}`;

  return (
    <div className={`my-1 ${isSolo ? 'mx-0' : 'mx-2 sm:mx-9'}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group flex items-baseline gap-1.5 py-0.5 text-left text-[12px] text-black/40 dark:text-white/30 hover:text-black/60 dark:hover:text-white/50 transition-colors"
      >
        <span className="font-mono select-none opacity-70">{open ? '∨' : '>'}</span>
        <span>{label}</span>
      </button>
      {open && (
        <div className="mt-1 pl-4 border-l border-black/10 dark:border-white/10">
          {children}
        </div>
      )}
    </div>
  );
};
