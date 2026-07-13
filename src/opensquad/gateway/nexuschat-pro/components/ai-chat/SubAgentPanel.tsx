/**
 * SubAgentPanel — Cursor-style delegate window.
 * Shows the sub-agent prompt + nested thought/tool stream; maximize / close to return.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Maximize2, Minimize2, X, Loader2, CheckCircle, XCircle } from 'lucide-react';
import type { WorkflowEvent } from '../../utils/aiChatTimeline';
import { isToolResultFailure } from '../../utils/aiChatTimeline';
import { extractFileEditInfo, parsePartialFileToolArgs, applyEditDiffContext } from './FileDiffBlock';
import { MarkdownScrollBody } from './MarkdownScrollBody';

export interface SubAgentPanelProps {
  open: boolean;
  onClose: () => void;
  title: string;
  prompt: string;
  events: WorkflowEvent[];
  /** Final natural-language answer from the sub-agent */
  finalResult?: string;
  running?: boolean;
}

function toolNameOf(evt: WorkflowEvent): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return String(data.name || data.tool || 'Tool');
}

function parseArgs(raw: unknown): Record<string, unknown> | null {
  if (raw == null) return null;
  if (typeof raw === 'object' && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
    } catch {
      return null;
    }
  }
  return null;
}

function formatResult(result: unknown): string {
  if (result == null) return '';
  if (typeof result === 'string') return result;
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

function isNoiseInfo(evt: WorkflowEvent): boolean {
  if (evt.type !== 'info') return false;
  const c = evt.content;
  if (typeof c === 'object' && c && (c as any).event === 'sub_agent_result') {
    // Final answer is shown in the dedicated Result section
    return true;
  }
  const text =
    typeof c === 'string'
      ? c
      : String((c as any)?.message || (c as any)?.text || '');
  return /\[Sub-Agent\]\s*(Starting|Done)/i.test(text);
}

type NestedLine =
  | { key: string; kind: 'thought'; text: string }
  | {
      key: string;
      kind: 'tool';
      primary: string;
      secondary: string;
      running: boolean;
      status: 'running' | 'success' | 'error';
      args: string;
      result: string;
    }
  | { key: string; kind: 'info'; text: string };

function buildNestedLines(events: WorkflowEvent[]): NestedLine[] {
  const lines: NestedLine[] = [];
  for (let i = 0; i < events.length; i++) {
    const evt = events[i];
    const key = evt._uid || `${evt.type}-${evt.timestamp}-${i}`;
    if (isNoiseInfo(evt)) continue;

    if (evt.type === 'thought') {
      const text = typeof evt.content === 'string' ? evt.content : JSON.stringify(evt.content ?? '');
      if (!text.trim()) continue;
      // Coalesce consecutive thought fragments into one Thinking row
      const last = lines[lines.length - 1];
      if (last && last.kind === 'thought') {
        last.text = `${last.text}${text}`;
        continue;
      }
      lines.push({ key, kind: 'thought', text });
      continue;
    }

    if (evt.type === 'tool_call') {
      const name = toolNameOf(evt);
      const content = typeof evt.content === 'object' && evt.content ? evt.content : {};
      const rawArgs = content.arguments ?? content.args ?? content.input;
      const argsObj =
        parseArgs(rawArgs) ||
        (typeof rawArgs === 'string' ? parsePartialFileToolArgs(rawArgs) : null) ||
        (typeof rawArgs === 'object' && rawArgs ? rawArgs : null);
      const fileEdit = applyEditDiffContext(extractFileEditInfo(name, argsObj || {}), {
        diffOld: evt.diffOld,
        diffNew: evt.diffNew,
        diffStartLine: evt.diffStartLine,
      });
      const running = !evt.result;
      const resultText = formatResult(evt.result);
      const failed =
        !running &&
        (evt.resultStatus === 'error' || isToolResultFailure(evt.result) || isToolResultFailure(resultText));
      let primary = name;
      let secondary = '';
      if (fileEdit?.kind === 'read') {
        primary = running
          ? `Reading ${fileEdit.fileName}`
          : failed
            ? `Failed read ${fileEdit.fileName}`
            : `Read ${fileEdit.fileName}`;
        secondary = fileEdit.lineRange || '';
      } else if (fileEdit?.kind === 'write') {
        primary = running
          ? `Writing ${fileEdit.fileName}`
          : failed
            ? `Failed write ${fileEdit.fileName}`
            : `Wrote ${fileEdit.fileName}`;
      } else if (fileEdit) {
        primary = running
          ? `Editing ${fileEdit.fileName}`
          : failed
            ? `Failed edit ${fileEdit.fileName}`
            : `Edited ${fileEdit.fileName}`;
      } else if (failed) {
        secondary = 'fail';
      }
      lines.push({
        key,
        kind: 'tool',
        primary,
        secondary,
        running,
        status: running ? 'running' : failed ? 'error' : 'success',
        args: argsObj ? JSON.stringify(argsObj, null, 2) : formatResult(content.arguments ?? content.args ?? ''),
        result: resultText,
      });
      continue;
    }

    if (evt.type === 'tool_result') {
      const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
      const name = String(data.name || data.tool || 'Tool');
      const resultText = formatResult(data.result ?? data.output ?? data);
      const failed = !!(data.error || isToolResultFailure(resultText));
      lines.push({
        key,
        kind: 'tool',
        primary: name,
        secondary: failed ? 'fail' : '',
        running: false,
        status: failed ? 'error' : 'success',
        args: '',
        result: resultText,
      });
      continue;
    }

    if (evt.type === 'info') {
      const c = evt.content;
      const text =
        typeof c === 'string'
          ? c
          : String((c as any)?.message || (c as any)?.text || '');
      if (!text.trim()) continue;
      lines.push({ key, kind: 'info', text });
    }
  }
  return lines;
}

const NestedLineView: React.FC<{ line: NestedLine }> = ({ line }) => {
  const [open, setOpen] = useState(line.kind === 'thought');

  if (line.kind === 'thought') {
    return (
      <div className="w-full">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-baseline gap-1.5 py-0.5 text-left bg-transparent border-0 p-0 cursor-pointer text-[13px] text-textMuted"
        >
          <span className="font-medium text-textMain/80">Thinking</span>
          <span className="text-textMuted/70">{open ? '⌄' : '>'}</span>
        </button>
        {open && (
          <div className="mt-0.5 pl-3 pr-1 py-1.5 rounded-md bg-black/[0.04] dark:bg-white/[0.05]">
            <MarkdownScrollBody text={line.text} follow muted maxHeightClass="max-h-[280px]" />
          </div>
        )}
      </div>
    );
  }

  if (line.kind === 'info') {
    return (
      <div className="text-[12px] text-textMuted/80 py-0.5 whitespace-pre-wrap break-words">
        {line.text}
      </div>
    );
  }

  const statusIcon =
    line.status === 'success' ? (
      <CheckCircle size={12} className="text-emerald-500 flex-shrink-0" />
    ) : line.status === 'error' ? (
      <XCircle size={12} className="text-red-500 flex-shrink-0" />
    ) : (
      <Loader2 size={12} className="text-violet-400 animate-spin flex-shrink-0" />
    );

  return (
    <div className="w-full">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 py-0.5 text-left bg-transparent border-0 p-0 cursor-pointer max-w-full"
      >
        {statusIcon}
        <span className={`text-[13px] truncate ${line.status === 'error' ? 'text-red-600 dark:text-red-400' : 'text-textMain/85'}`}>
          {line.primary}
          {line.secondary ? (
            <span className={line.status === 'error' ? 'text-red-600/80 dark:text-red-400/80' : 'text-textMuted/70'}>
              {' '}
              {line.secondary}
            </span>
          ) : null}
          {line.running ? <span className="text-textMuted/70"> …</span> : null}
        </span>
        <span className="text-[12px] text-textMuted/60 shrink-0">{open ? '⌄' : '>'}</span>
      </button>
      {open && (line.args || line.result) && (
        <div className="mt-1 mb-1.5 ml-4 rounded-md border border-border/50 bg-black/[0.03] dark:bg-white/[0.04] overflow-hidden">
          {line.args ? (
            <div className="px-2.5 py-2 border-b border-border/30">
              <div className="text-[10px] font-medium text-textMuted/60 mb-1 uppercase tracking-wide">Args</div>
              <pre className="text-[11px] font-mono whitespace-pre-wrap break-words m-0 max-h-[180px] overflow-y-auto text-textMuted">
                {line.args}
              </pre>
            </div>
          ) : null}
          {line.result ? (
            <div className="px-2.5 py-2">
              <div className="text-[10px] font-medium text-textMuted/60 mb-1 uppercase tracking-wide">Result</div>
              <pre className="text-[11px] font-mono whitespace-pre-wrap break-words m-0 max-h-[220px] overflow-y-auto text-textMuted">
                {line.result}
              </pre>
            </div>
          ) : line.running ? (
            <div className="px-2.5 py-2 text-[12px] text-textMuted/70">Running…</div>
          ) : null}
        </div>
      )}
    </div>
  );
};

export const SubAgentPanel: React.FC<SubAgentPanelProps> = ({
  open,
  onClose,
  title,
  prompt,
  events,
  finalResult = '',
  running = false,
}) => {
  const [maximized, setMaximized] = useState(false);
  const lines = useMemo(() => buildNestedLines(events), [events]);
  const resultText = (finalResult || '').trim();
  const streamRef = useRef<HTMLDivElement>(null);

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

  // Keep the live tool stream pinned to the latest step while exploring.
  useEffect(() => {
    if (!open || !running) return;
    const el = streamRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [open, running, lines.length, resultText]);

  if (!open) return null;

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
        className={`bg-panel border border-border shadow-2xl flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150 ${
          maximized
            ? 'w-full h-full max-w-none rounded-xl'
            : 'w-full max-w-2xl h-[min(82vh,720px)] rounded-xl'
        }`}
      >
        {/* Header — title + maximize + close */}
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border/70 bg-panel shrink-0">
          <div className="flex-1 min-w-0">
            <div className="text-[14px] font-semibold text-textMain truncate">{title}</div>
            {running ? (
              <div className="text-[11px] text-violet-500/90 flex items-center gap-1 mt-0.5">
                <Loader2 size={11} className="animate-spin" />
                Exploring
              </div>
            ) : (
              <div className="text-[11px] text-textMuted mt-0.5">Delegate complete</div>
            )}
          </div>
          <button
            type="button"
            onClick={() => setMaximized((v) => !v)}
            className="p-1.5 rounded-md text-textMuted hover:text-textMain hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
            aria-label={maximized ? 'Restore' : 'Maximize'}
            title={maximized ? 'Restore' : 'Maximize'}
          >
            {maximized ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-md text-textMuted hover:text-textMain hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
            aria-label="Close"
            title="Close"
          >
            <X size={16} />
          </button>
        </div>

        {/* Prompt box */}
        <div className="px-4 pt-3 pb-2 shrink-0">
          <div className="rounded-lg bg-black/[0.045] dark:bg-white/[0.06] border border-border/40 px-3.5 py-2.5 max-h-[140px] overflow-y-auto">
            <pre className="text-[12.5px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 text-textMain/85">
              {prompt}
            </pre>
          </div>
        </div>

        {/* Nested activity stream */}
        <div ref={streamRef} className="flex-1 min-h-0 overflow-y-auto px-4 pb-4 pt-1 space-y-1.5">
          {lines.length === 0 && !resultText ? (
            <div className="text-[13px] text-textMuted/70 py-6 text-center">
              {running ? 'Waiting for sub-agent activity…' : 'No nested activity recorded.'}
            </div>
          ) : (
            <>
              {lines.length > 0 && (
                <>
                  <div className="text-[12px] font-semibold text-textMain/75 pt-1 pb-0.5">Exploring</div>
                  {lines.map((line) => (
                    <NestedLineView key={line.key} line={line} />
                  ))}
                </>
              )}
              {resultText ? (
                <div className="mt-3 pt-2 border-t border-border/50">
                  <div className="text-[12px] font-semibold text-textMain/75 pb-1.5">Result</div>
                  <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/[0.04] px-3 py-2.5">
                    <pre className="text-[12.5px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 text-textMain/90 max-h-[360px] overflow-y-auto">
                      {resultText}
                    </pre>
                  </div>
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );

  return createPortal(panel, document.body);
};
