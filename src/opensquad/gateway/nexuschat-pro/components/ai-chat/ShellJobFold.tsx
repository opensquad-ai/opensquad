/**
 * ShellJobFold — main-stream entry for system.start_job / run_session_job.
 * Header row toggles an inline CMD-style fold (same look as the old
 * ShellJobPanel window) with live stdout auto-scroll.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle, XCircle, Terminal } from 'lucide-react';
import type { ShellJobBundle, ShellStreamState } from '../../utils/shellJobGrouping';
import { shellJobDoneLabel, extractShellExitCode } from '../../utils/shellJobGrouping';
import { OpenSquadLoader } from '../OpenSquadLoader';
import { Collapse, useFold } from '../Collapse';

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

/** Inline CMD-style body — same visual language as the old popup window. */
const ShellJobBody: React.FC<{
  bundle: ShellJobBundle;
  statusLabel: string;
}> = ({ bundle, statusLabel }) => {
  const streamRef = useRef<HTMLPreElement>(null);
  const exitCode = bundle.running ? null : extractShellExitCode(bundle.parent.result);

  useEffect(() => {
    if (!bundle.running) return;
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [bundle.running, bundle.output]);

  return (
    <div className="mt-1 rounded-lg border border-emerald-500/25 bg-[#0c0c0c] overflow-hidden">
      {/* Status strip */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/10 bg-[#161616]">
        <Terminal size={14} className="text-emerald-400/90 flex-shrink-0" />
        <div className="text-[10px] text-emerald-400/70 flex items-center gap-1.5 min-w-0 font-mono">
          {bundle.running ? (
            <OpenSquadLoader size={12} />
          ) : bundle.errored ? (
            <XCircle size={10} className="text-red-400" />
          ) : (
            <CheckCircle size={10} className="text-emerald-400" />
          )}
          <span className="truncate">{statusLabel}</span>
          {bundle.shellType ? <span className="opacity-70">· {bundle.shellType}</span> : null}
          {bundle.jobId ? <span className="opacity-60">· job {bundle.jobId}</span> : null}
          {exitCode != null ? (
            <span className={exitCode === 0 ? 'opacity-60' : 'text-red-400'}>· exit {exitCode}</span>
          ) : null}
        </div>
      </div>

      {/* Command */}
      <div className="px-3 pt-2.5 pb-1.5 border-b border-white/5">
        <div className="rounded border border-emerald-500/15 bg-black/40 px-2.5 py-1.5">
          <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-all font-mono m-0 text-emerald-200/85">
            <span className="text-emerald-500/70 select-none">{'> '}</span>
            {bundle.command || '(no command)'}
          </pre>
        </div>
      </div>

      {/* Live stdout */}
      <pre
        ref={streamRef}
        className="max-h-[320px] overflow-auto px-3 py-2.5 m-0 font-mono text-[12px] leading-[1.45] whitespace-pre-wrap break-words text-[#c8e6c9] bg-[#0c0c0c]"
      >
        {bundle.output || (bundle.running ? 'Waiting for output…' : '(no output)')}
        {bundle.running ? (
          <span className="inline-block w-1.5 h-3.5 bg-emerald-400/70 animate-pulse ml-0.5 align-middle" />
        ) : null}
      </pre>
    </div>
  );
};

export const ShellJobFold: React.FC<ShellJobFoldProps> = ({
  bundle: rawBundle,
  stream,
  variant = 'classic',
}) => {
  const bundle = useMemo(() => mergeBundle(rawBundle, stream), [rawBundle, stream]);
  const { t } = useTranslation();
  // Shared fold primitive. stdout can be long, so the body mounts on first
  // expand — the first expansion still animates (see `useFold`).
  const { open, mounted, toggle: toggleOpen } = useFold(false);

  const cmdShort = truncateCmd(bundle.command);
  // Agent 提供的调用目的说明：作为标题展示（缺省回退到"已执行命令 + 命令"）。
  const desc = (bundle.description || '').trim();
  const doneLabel = shellJobDoneLabel(
    bundle.running,
    bundle.errored,
    bundle.parent.result,
    stream,
    t,
  );
  const statusLabel = bundle.running ? t('aiChat.toolFlow.shell.running') : doneLabel;
  const body = mounted ? <ShellJobBody bundle={bundle} statusLabel={statusLabel} /> : null;

  if (variant === 'solo') {
    const faint = 'color-mix(in srgb, rgb(var(--color-text-muted)) 55%, transparent)';
    const accent = bundle.errored && !bundle.running
      ? 'color-mix(in srgb, rgb(var(--color-danger, #ef4444)) 75%, transparent)'
      : faint;
    return (
      <div className="min-w-0">
        <button
          type="button"
          onClick={toggleOpen}
          aria-expanded={open}
          style={{ color: accent }}
          className="group inline-flex items-center gap-1.5 py-0.5 text-left max-w-full bg-transparent border-0 p-0 cursor-pointer"
        >
          <Terminal size={13} className="shrink-0" style={{ color: accent, opacity: 0.7 }} />
          <span className="text-[13px] leading-relaxed min-w-0 truncate" style={{ color: accent }}>
            {desc ? (
              <span className="font-normal">{desc}</span>
            ) : (
              <>
                <span className="font-normal">
                  {bundle.running ? t('aiChat.toolFlow.shell.running') : bundle.errored ? doneLabel : t('aiChat.toolFlow.shell.ran')}
                </span>
                {cmdShort ? <span>{` ${cmdShort}`}</span> : null}
                {bundle.running ? <span style={{ opacity: 0.85 }}> …</span> : null}
              </>
            )}
          </span>
          <span
            className={`os-fold-chevron text-[13px] font-normal leading-relaxed shrink-0${open ? ' is-open' : ''}`}
            style={{ color: accent }}
          >
            &gt;
          </span>
        </button>
        <Collapse open={open}>{body}</Collapse>
      </div>
    );
  }

  const statusIcon = bundle.running ? (
    <OpenSquadLoader size={12} className="flex-shrink-0" />
  ) : bundle.errored ? (
    <XCircle size={12} className="text-red-500 flex-shrink-0" />
  ) : (
    <CheckCircle size={12} className="text-emerald-500 flex-shrink-0" />
  );

  return (
    <div>
      <button
        type="button"
        onClick={toggleOpen}
        aria-expanded={open}
        data-tool-expanded={open || undefined}
        className="w-full text-left rounded-md border border-emerald-500/25 bg-emerald-500/5 hover:bg-emerald-500/10 overflow-hidden transition-colors cursor-pointer"
      >
        <div className="flex items-center gap-1.5 px-2 py-1.5 select-none">
          {statusIcon}
          <Terminal size={11} className="text-emerald-400 flex-shrink-0" />
          <span className="text-[11px] text-emerald-500 font-semibold leading-none flex-shrink-0">
            Shell
          </span>
          <span
            className={`text-[11px] text-gray-800 dark:text-gray-200 font-medium truncate flex-1${desc ? '' : ' font-mono'}`}
          >
            {desc || cmdShort}
          </span>
        </div>
      </button>
      <Collapse open={open}>{body}</Collapse>
    </div>
  );
};
