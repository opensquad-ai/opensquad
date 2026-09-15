/**
 * ShellTerminalsBar — composer-top indicator for background terminals.
 * Pill (N terminals) → click expands the running-terminals list
 * (command + live elapsed) → click a row opens the CMD-style live
 * window (ShellJobPanel) with realtime stdout.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Terminal, Trash2, X } from 'lucide-react';
import type { RunningShellJobInfo } from '../../utils/shellJobGrouping';
import { ShellJobPanel } from './ShellJobPanel';
import { formatElapsed } from '../../utils/formatElapsed';

function truncateCmd(cmd: string, max = 48): string {
  const t = (cmd || '').replace(/\s+/g, ' ').trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

export const ShellTerminalsBar: React.FC<{
  jobs: RunningShellJobInfo[];
  /** Kill one background terminal (trash icon). Row shows a stopping state until it disappears. */
  onStopJob?: (job: RunningShellJobInfo) => void;
}> = ({ jobs, onStopJob }) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [stoppingIds, setStoppingIds] = useState<Set<string>>(new Set());
  // Bump once per second while the list is open so elapsed labels keep up.
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!open || jobs.length === 0) return;
    const timer = window.setInterval(() => setTick((v) => v + 1), 1000);
    return () => window.clearInterval(timer);
  }, [open, jobs.length]);

  // Keep the popup pinned to a live job; drop it if the job vanished.
  const active = activeId ? jobs.find((j) => j.id === activeId) : null;
  useEffect(() => {
    if (activeId && !active) setActiveId(null);
  }, [activeId, active]);

  const activeTitle = active
    ? active.shellType
      ? `${active.shellType}: ${truncateCmd(active.command)}`
      : truncateCmd(active.command)
    : '';

  const handleStop = (job: RunningShellJobInfo) => {
    setStoppingIds((prev) => {
      const next = new Set(prev);
      next.add(job.id);
      return next;
    });
    onStopJob?.(job);
  };

  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="self-start inline-flex items-center gap-1.5 rounded-lg border border-border bg-bgLight px-2.5 py-1 text-xs text-textMuted hover:text-textMain hover:bg-border/40 transition-colors cursor-pointer"
      >
        <Terminal size={12} className="shrink-0" />
        <span className="font-medium">{t('aiChat.terminals.count', { count: jobs.length })}</span>
        <span className={`text-[9px] leading-none transition-transform ${open ? 'rotate-90' : ''}`}>›</span>
      </button>

      {open ? (
        <div className="rounded-xl border border-border bg-panel shadow-[0_4px_24px_rgba(0,0,0,0.12)] overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 border-b border-border/70">
            <span className="text-[12px] font-medium text-textMain flex-1 min-w-0 truncate">
              {t('aiChat.terminals.running', { count: jobs.length })}
            </span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="p-1 rounded-md text-textMuted hover:text-textMain hover:bg-border/40 transition-colors cursor-pointer"
              aria-label={t('aiChat.terminals.close')}
              title={t('aiChat.terminals.close')}
            >
              <X size={13} />
            </button>
          </div>
          <div className="py-1">
            {jobs.map((j) => (
              <button
                key={j.id}
                type="button"
                onClick={() => setActiveId(j.id)}
                className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-bgLight transition-colors cursor-pointer border-0 bg-transparent"
                title={t('aiChat.terminals.openTerminal')}
              >
                <Terminal size={13} className={`shrink-0 ${j.errored ? 'text-red-500' : 'text-emerald-500/80'}`} />
                <span className="text-[12px] text-textMain truncate flex-1 min-w-0">
                  {truncateCmd(j.command) || j.jobId || j.id}
                </span>
                <span className="text-[11px] text-textMuted shrink-0 font-mono">
                  {stoppingIds.has(j.id)
                    ? t('aiChat.terminals.stopping')
                    : j.startedMs
                      ? formatElapsed(Date.now() - j.startedMs)
                      : ''}
                </span>
                {onStopJob && j.jobId ? (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!stoppingIds.has(j.id)) handleStop(j);
                    }}
                    disabled={stoppingIds.has(j.id)}
                    className="p-1 rounded-md text-textMuted hover:text-red-500 hover:bg-red-500/10 disabled:opacity-40 disabled:pointer-events-none transition-colors cursor-pointer"
                    aria-label={t('aiChat.terminals.stop')}
                    title={t('aiChat.terminals.stop')}
                  >
                    <Trash2 size={13} />
                  </button>
                ) : null}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {active ? (
        <ShellJobPanel
          open
          onClose={() => setActiveId(null)}
          title={activeTitle}
          command={active.command}
          output={active.output}
          running={active.running}
          errored={active.errored}
          statusLabel={
            active.running
              ? t('aiChat.toolFlow.shell.running')
              : t('aiChat.toolFlow.shell.completed')
          }
          shellType={active.shellType}
          jobId={active.jobId}
        />
      ) : null}
    </div>
  );
};
