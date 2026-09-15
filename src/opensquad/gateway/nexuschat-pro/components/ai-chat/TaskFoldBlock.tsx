/**
 * Outer fold for a complex-task process (between last user message and
 * to_user_end_task report). Text-style toggle — no card chrome.
 *
 * The body can hold the whole run (dozens of tool blocks), so it mounts lazily;
 * the shared fold primitive still animates the very first expansion.
 */
import React from 'react';
import { Collapse, useFold } from '../Collapse';

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
  const { open, mounted, toggle } = useFold(!defaultCollapsed);
  const parts: string[] = [];
  if (eventCount > 0) parts.push(`${eventCount} step${eventCount === 1 ? '' : 's'}`);
  if (messageCount > 0) parts.push(`${messageCount} notice${messageCount === 1 ? '' : 's'}`);
  const summary = parts.length > 0 ? parts.join(' · ') : 'process';
  const label = title ? `${title} — ${summary}` : `Task process — ${summary}`;

  return (
    <div className={`my-1 ${isSolo ? 'mx-0' : 'mx-2 sm:mx-9'}`}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="group flex items-baseline gap-1.5 py-0.5 text-left text-[12px] text-black/40 dark:text-white/30 hover:text-black/60 dark:hover:text-white/50 transition-colors"
      >
        {/* The glyph ROTATES instead of swapping ∨/<  — a changed glyph
            cannot transition. */}
        <span className={`os-fold-chevron font-mono select-none opacity-70 leading-none${open ? ' is-open' : ''}`}>
          &gt;
        </span>
        <span>{label}</span>
      </button>
      <Collapse open={open}>
        {mounted ? <div className="mt-1 pl-4 border-l border-border/60">{children}</div> : null}
      </Collapse>
    </div>
  );
};
