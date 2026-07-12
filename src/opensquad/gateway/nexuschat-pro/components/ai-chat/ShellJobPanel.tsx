/**
 * ShellJobPanel — CMD-style live stdout window for system.start_job / run_session_job.
 */
import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Maximize2, Minimize2, X, Loader2, CheckCircle, XCircle, Terminal } from 'lucide-react';

export interface ShellJobPanelProps {
  open: boolean;
  onClose: () => void;
  title: string;
  command: string;
  output: string;
  running?: boolean;
  errored?: boolean;
  shellType?: string;
  jobId?: string;
}

export const ShellJobPanel: React.FC<ShellJobPanelProps> = ({
  open,
  onClose,
  title,
  command,
  output,
  running = false,
  errored = false,
  shellType,
  jobId,
}) => {
  const [maximized, setMaximized] = useState(false);
  const streamRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (!open) setMaximized(false);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open || !running) return;
    const el = streamRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [open, running, output]);

  if (!open) return null;

  const statusLabel = running
    ? 'Running…'
    : errored
      ? 'Failed / aborted'
      : 'Completed';

  const panel = (
    <div
      className="fixed inset-0 z-[180] flex items-center justify-center bg-black/40 backdrop-blur-[2px] p-3 sm:p-6"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className={`bg-[#0c0c0c] border border-emerald-500/25 shadow-2xl flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150 ${
          maximized
            ? 'w-full h-full max-w-none rounded-xl'
            : 'w-full max-w-3xl h-[min(78vh,640px)] rounded-xl'
        }`}
      >
        <div className="flex items-center gap-2 px-3 py-2 border-b border-white/10 bg-[#161616] shrink-0">
          <Terminal size={14} className="text-emerald-400/90 flex-shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="text-[13px] font-semibold text-emerald-100/95 truncate font-mono">
              {title}
            </div>
            <div className="text-[10px] text-emerald-400/70 flex items-center gap-1.5 mt-0.5 font-mono">
              {running ? (
                <Loader2 size={10} className="animate-spin" />
              ) : errored ? (
                <XCircle size={10} className="text-red-400" />
              ) : (
                <CheckCircle size={10} className="text-emerald-400" />
              )}
              <span>{statusLabel}</span>
              {shellType ? <span className="opacity-70">· {shellType}</span> : null}
              {jobId ? <span className="opacity-60">· job {jobId}</span> : null}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setMaximized((v) => !v)}
            className="p-1.5 rounded-md text-white/50 hover:text-white hover:bg-white/10 transition-colors"
            aria-label={maximized ? 'Restore' : 'Maximize'}
            title={maximized ? 'Restore' : 'Maximize'}
          >
            {maximized ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-md text-white/50 hover:text-white hover:bg-white/10 transition-colors"
            aria-label="Close"
            title="Close"
          >
            <X size={15} />
          </button>
        </div>

        <div className="px-3 pt-2.5 pb-1.5 shrink-0 border-b border-white/5">
          <div className="rounded border border-emerald-500/15 bg-black/40 px-2.5 py-1.5">
            <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-all font-mono m-0 text-emerald-200/85">
              <span className="text-emerald-500/70 select-none">{'> '}</span>
              {command || '(no command)'}
            </pre>
          </div>
        </div>

        <pre
          ref={streamRef}
          className="flex-1 min-h-0 overflow-auto px-3 py-2.5 m-0 font-mono text-[12px] leading-[1.45] whitespace-pre-wrap break-words text-[#c8e6c9] bg-[#0c0c0c]"
        >
          {output || (running ? 'Waiting for output…' : '(no output)')}
          {running ? (
            <span className="inline-block w-1.5 h-3.5 bg-emerald-400/70 animate-pulse ml-0.5 align-middle" />
          ) : null}
        </pre>
      </div>
    </div>
  );

  return createPortal(panel, document.body);
};
