/**
 * ShellJobFold — main-stream entry for system.start_job / run_session_job.
 * Spinning while live; click opens CMD-style ShellJobPanel.
 */
import React, { useState, useCallback, useMemo } from 'react';
import { Loader2, CheckCircle, XCircle, Terminal } from 'lucide-react';
import type { ShellJobBundle, ShellStreamState } from '../../utils/shellJobGrouping';
import { ShellJobPanel } from './ShellJobPanel';

export interface ShellJobFoldProps {
  bundle: ShellJobBundle;
  /** Live stdout / status overlay from WS (keyed by call_id on parent) */
  stream?: ShellStreamState | null;
  variant?: 'solo' | 'classic';
}

function truncateCmd(cmd: string, max = 56): string {
  const t = (cmd || '').replace(/\s+/g, ' ').trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

function mergeBundle(bundle: ShellJobBundle, stream?: ShellStreamState | null): ShellJobBundle {
  if (!stream) return bundle;
  const streamDone =
    stream.state === 'done' || stream.state === 'error' || stream.state === 'aborted';
  const running = streamDone ? false : stream.state === 'running' || bundle.running;
  const output =
    stream.output && stream.output.length > 0 ? stream.output : bundle.output;
  return {
    ...bundle,
    command: stream.command || bundle.command,
    jobId: stream.jobId || bundle.jobId,
    sessionId: stream.sessionId || bundle.sessionId,
    shellType: stream.shellType || bundle.shellType,
    output,
    running,
    errored:
      stream.state === 'error' || stream.state === 'aborted' || (!running && bundle.errored),
  };
}

export const ShellJobFold: React.FC<ShellJobFoldProps> = ({
  bundle: rawBundle,
  stream,
  variant = 'classic',
}) => {
  const bundle = useMemo(() => mergeBundle(rawBundle, stream), [rawBundle, stream]);
  const [panelOpen, setPanelOpen] = useState(false);
  const openPanel = useCallback(() => setPanelOpen(true), []);
  const closePanel = useCallback(() => setPanelOpen(false), []);

  const cmdShort = truncateCmd(bundle.command);
  const title = bundle.shellType
    ? `${bundle.shellType}: ${cmdShort}`
    : cmdShort || 'Shell job';

  const panel = (
    <ShellJobPanel
      open={panelOpen}
      onClose={closePanel}
      title={title}
      command={bundle.command}
      output={bundle.output}
      running={bundle.running}
      errored={bundle.errored}
      shellType={bundle.shellType}
      jobId={bundle.jobId}
    />
  );

  if (variant === 'solo') {
    const faint = 'color-mix(in srgb, var(--color-text-muted) 55%, transparent)';
    const accent = bundle.errored && !bundle.running
      ? 'color-mix(in srgb, var(--color-danger, #ef4444) 75%, transparent)'
      : faint;
    return (
      <>
        <button
          type="button"
          onClick={openPanel}
          style={{ color: accent }}
          className="group inline-flex items-baseline gap-1.5 py-0.5 text-left max-w-full bg-transparent border-0 p-0 cursor-pointer"
          title="Open terminal window"
        >
          <span className="text-[13px] leading-relaxed min-w-0 truncate" style={{ color: accent }}>
            <span className="font-normal">
              {bundle.running ? 'Running' : bundle.errored ? 'Shell failed' : 'Ran'}
            </span>
            {cmdShort ? <span>{` ${cmdShort}`}</span> : null}
            {bundle.running ? <span style={{ opacity: 0.85 }}> …</span> : null}
          </span>
          <span className="text-[13px] shrink-0" style={{ color: accent }}>↗</span>
        </button>
        {panel}
      </>
    );
  }

  const statusIcon = bundle.running ? (
    <Loader2 size={12} className="text-emerald-400 animate-spin flex-shrink-0" />
  ) : bundle.errored ? (
    <XCircle size={12} className="text-red-500 flex-shrink-0" />
  ) : (
    <CheckCircle size={12} className="text-emerald-500 flex-shrink-0" />
  );

  return (
    <>
      <button
        type="button"
        onClick={openPanel}
        data-tool-expanded={panelOpen || undefined}
        className="w-full text-left rounded-md border border-emerald-500/25 bg-emerald-500/5 hover:bg-emerald-500/10 overflow-hidden transition-colors cursor-pointer"
        title="Open terminal window"
      >
        <div className="flex items-center gap-1.5 px-2 py-1.5 select-none">
          {statusIcon}
          <Terminal size={11} className="text-emerald-400 flex-shrink-0" />
          <span className="text-[11px] text-emerald-500 font-semibold leading-none flex-shrink-0">
            Shell
          </span>
          <span className="text-[11px] text-gray-800 dark:text-gray-200 font-mono font-medium truncate flex-1">
            {cmdShort}
          </span>
          <span className="text-[10px] text-textMuted flex-shrink-0">↗</span>
        </div>
      </button>
      {panel}
    </>
  );
};
