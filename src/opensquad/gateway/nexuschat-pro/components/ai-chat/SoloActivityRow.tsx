/**
 * SoloActivityRow — Cursor-style document-flow activity for Solo UI.
 *
 * Outer fold (turn-level): collapses the whole thought+tool process once the
 * agent finishes the turn. Inner lines:
 *   - thought: faded text body
 *   - file edit/write: fold shows +N -M; expand → embedded FileDiffBlock
 *   - other tools (websearch, etc.): expand → light box with Args + Result
 */
import React, { useEffect, useMemo, useState, useRef } from 'react';
import { CircleDashed, CheckCircle2, XCircle, ListTodo, ArrowRightCircle } from 'lucide-react';
import type { WorkflowBlock, WorkflowEvent } from '../../utils/aiChatTimeline';
import { FileDiffBlock, extractFileEditInfo, type FileEditInfo } from './FileDiffBlock';
import { buildDisplayWorkflowItems, type DelegateBundle } from '../../utils/delegateGrouping';
import { DelegateFold } from './DelegateFold';
import { parsePlanContent, type PlanStep } from './PlanBlock';

interface SoloActivityRowProps {
  block: WorkflowBlock;
  /** When true, expand outer + inner details (Header lightbulb in Solo). */
  expandDetails?: boolean;
  turnStartedMs?: number;
}

function toolNameOf(evt: WorkflowEvent): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return String(data.name || data.tool || 'Tool');
}

function thoughtText(evt: WorkflowEvent): string {
  return typeof evt.content === 'string' ? evt.content : JSON.stringify(evt.content ?? '');
}

function parseArgs(raw: unknown): Record<string, unknown> | null {
  if (raw == null) return null;
  if (typeof raw === 'object' && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
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

function prettyJson(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function thoughtLabel(text: string): { primary: string; secondary: string } {
  const trimmed = text.trim();
  if (!trimmed) return { primary: 'Thought', secondary: '' };
  if (trimmed.length < 40) return { primary: 'Thought', secondary: 'briefly' };
  if (trimmed.length < 120) return { primary: 'Thought', secondary: 'for a moment' };
  return { primary: 'Thought', secondary: 'for a bit' };
}

type LineKind = 'thought' | 'tool' | 'info' | 'summary' | 'progress' | 'delegation' | 'plan';

interface ActivityLine {
  key: string;
  kind: LineKind;
  primary: string;
  secondary: string;
  detail: string;
  running?: boolean;
  /** Structured tool payload for rich Solo expand panels */
  toolName?: string;
  toolArgs?: Record<string, unknown> | null;
  toolResult?: string;
  fileEdit?: FileEditInfo | null;
  toolStatus?: 'running' | 'success' | 'error';
  /** Context-compression summary flags */
  summaryDone?: boolean;
  summaryPending?: boolean;
  /** Cursor-style delegate bundle (opens SubAgentPanel) */
  delegation?: DelegateBundle;
  /** Parsed <plan> steps for Solo To-dos fold */
  planSteps?: PlanStep[];
}

function eventToLines(evt: WorkflowEvent, key: string, blockCompleted: boolean): ActivityLine[] {
  const lines: ActivityLine[] = [];

  if (evt.type === 'thought') {
    const text = thoughtText(evt);
    if (!text.trim()) return lines;
    const { primary, secondary } = thoughtLabel(text);
    lines.push({ key, kind: 'thought', primary, secondary, detail: text });
    return lines;
  }

  if (evt.type === 'summary_stream') {
    const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
    const text = typeof data.text === 'string' ? data.text : '';
    const done = !!data.done;
    const pending = !!data.pending;
    lines.push({
      key,
      kind: 'summary',
      primary: done
        ? (text ? 'Context summary' : 'Compression completed')
        : (pending ? 'Waiting for compression' : 'Compressing context'),
      secondary: done ? 'done' : 'live',
      detail: pending ? '' : (text || (done ? '' : 'Summarizing…')),
      running: !done,
      summaryDone: done,
      summaryPending: pending,
    });
    return lines;
  }

  if (evt.type === 'compression_progress') {
    const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
    const text =
      typeof evt.content === 'string'
        ? evt.content
        : String(data.text || data.message || '');
    if (!text.trim()) return lines;
    const isFinal = !!data.is_final;
    lines.push({
      key,
      kind: 'progress',
      primary: text.length > 72 ? `${text.slice(0, 72)}…` : text,
      secondary: isFinal ? 'done' : '',
      detail: text,
      running: !isFinal && !blockCompleted,
    });
    return lines;
  }

  if (evt.type === 'tool_call') {
    const running = !evt.result && !blockCompleted;
    const name = toolNameOf(evt);
    const content = typeof evt.content === 'object' && evt.content ? evt.content : {};
    const rawArgs = content.arguments ?? content.args ?? content.input;
    const argsObj = parseArgs(rawArgs);
    const resultStr = formatResult(evt.result);
    const fileEdit = extractFileEditInfo(name, argsObj || rawArgs || {});
    const status: 'running' | 'success' | 'error' = running
      ? 'running'
      : evt.resultStatus === 'error'
        ? 'error'
        : 'success';

    let primary = name;
    let secondary = '';
    if (fileEdit) {
      if (fileEdit.kind === 'read') {
        primary = running ? `Reading ${fileEdit.fileName}` : `Read ${fileEdit.fileName}`;
        secondary = fileEdit.lineRange || '';
      } else if (fileEdit.kind === 'write') {
        primary = running ? `Writing ${fileEdit.fileName}` : `Wrote ${fileEdit.fileName}`;
      } else {
        primary = running ? `Editing ${fileEdit.fileName}` : `Edited ${fileEdit.fileName}`;
      }
    } else if (running) {
      primary = 'Running';
      secondary = name;
    }

    lines.push({
      key,
      kind: 'tool',
      primary,
      secondary,
      detail: '',
      running,
      toolName: name,
      toolArgs: argsObj,
      toolResult: resultStr,
      fileEdit,
      toolStatus: status,
    });
    return lines;
  }

  if (evt.type === 'tool_result') {
    const data = typeof evt.content === 'object' ? evt.content : {};
    const name = String(data.name || data.tool || 'Tool');
    const result = formatResult(data.result ?? data.output ?? data);
    lines.push({
      key,
      kind: 'tool',
      primary: name,
      secondary: '',
      detail: '',
      toolName: name,
      toolArgs: null,
      toolResult: result,
      fileEdit: null,
      toolStatus: data.error ? 'error' : 'success',
    });
    return lines;
  }

  if (evt.type === 'info') {
    const text =
      typeof evt.content === 'string'
        ? evt.content
        : evt.content?.text || evt.content?.message || '';
    if (!text) return lines;
    lines.push({
      key,
      kind: 'info',
      primary: text.length > 60 ? `${text.slice(0, 60)}…` : text,
      secondary: '',
      detail: text,
    });
    return lines;
  }

  if (evt.type === 'plan') {
    const steps = parsePlanContent(evt.content);
    if (steps.length === 0) return lines;
    const running = steps.some((s) => s.status === 'running');
    const done = steps.filter((s) => s.status === 'done').length;
    lines.push({
      key,
      kind: 'plan',
      primary: 'To-dos',
      secondary: String(steps.length),
      detail: done > 0 ? `${done}/${steps.length}` : '',
      running: running && !blockCompleted,
      planSteps: steps,
    });
  }

  return lines;
}

function buildLines(block: WorkflowBlock): ActivityLine[] {
  const lines: ActivityLine[] = [];
  const items = buildDisplayWorkflowItems(block.events);

  for (const item of items) {
    if (item.kind === 'delegation') {
      lines.push({
        key: item.key,
        kind: 'delegation',
        primary: item.bundle.running ? 'Exploring' : 'Explored',
        secondary: item.bundle.label,
        detail: '',
        running: item.bundle.running,
        delegation: item.bundle,
      });
      continue;
    }
    // Skip orphan sub-agent events that somehow weren't nested (still hide from main stream
    // when they carry the flag — they belong in a delegate window).
    if (item.event.subAgent) continue;
    lines.push(...eventToLines(item.event, item.key, !!block.completed));
  }

  return lines;
}

function outerSummary(
  block: WorkflowBlock,
  lines: ActivityLine[],
  turnStartedMs?: number,
): { primary: string; secondary: string } {
  let elapsedMs: number | null =
    typeof block.elapsed_ms === 'number' ? block.elapsed_ms : null;
  if (elapsedMs == null && turnStartedMs != null) {
    elapsedMs = Math.max(0, Date.now() - turnStartedMs);
  }
  const secs = elapsedMs != null ? Math.max(1, Math.round(elapsedMs / 1000)) : null;
  const thoughts = lines.filter((l) => l.kind === 'thought').length;
  const tools = lines.filter((l) => l.kind === 'tool' || l.kind === 'delegation').length;
  const plans = lines.filter((l) => l.kind === 'plan');
  const summaries = lines.filter((l) => l.kind === 'summary');
  const liveSummary = summaries.some((l) => l.running);
  const liveTool = lines.some((l) => l.kind === 'tool' && l.running);
  const livePlan = plans.some((l) => l.running);
  const hasLiveLine = lines.some((l) => l.running);

  if (liveSummary) {
    if (secs != null) return { primary: `Compressing for ${secs}s`, secondary: '' };
    return { primary: 'Compressing context', secondary: '' };
  }
  // Summary finished (even if workflow block not yet marked completed).
  if (summaries.length > 0 && summaries.every((l) => !l.running)) {
    return { primary: 'Context compressed', secondary: '' };
  }
  if (liveTool || livePlan || (hasLiveLine && !block.completed)) {
    if (secs != null) return { primary: `Working for ${secs}s`, secondary: '' };
    return { primary: 'Working', secondary: '' };
  }
  if (!block.completed && turnStartedMs != null) {
    if (secs != null) return { primary: `Working for ${secs}s`, secondary: '' };
    return { primary: 'Working', secondary: '' };
  }
  if (tools > 0 && secs != null) return { primary: `Worked for ${secs}s`, secondary: '' };
  if (tools > 0) return { primary: 'Worked', secondary: '' };
  if (plans.length > 0) {
    const n = plans.reduce((sum, l) => sum + (l.planSteps?.length || 0), 0);
    if (n > 0) return { primary: 'To-dos', secondary: String(n) };
    return { primary: 'Planned', secondary: '' };
  }
  if (thoughts > 0) {
    if (secs != null && secs >= 2) return { primary: 'Thought', secondary: `for ${secs}s` };
    return { primary: 'Thought', secondary: 'for a bit' };
  }
  return { primary: 'Activity', secondary: '' };
}

/** Soft white-light sweep across live activity title text. */
const ShimmerLabel: React.FC<{
  children: React.ReactNode;
  color: string;
  className?: string;
}> = ({ children, color, className }) => (
  <span className={`solo-text-shimmer ${className || ''}`} style={{ color }}>
    {children}
  </span>
);

/** Waiting buffer while the next thought / tool call is being prepared. */
const NextPlanningPlaceholder: React.FC<{ depth?: 0 | 1 }> = ({ depth = 1 }) => {
  const faint =
    depth === 0
      ? 'color-mix(in srgb, var(--color-text-muted) 72%, transparent)'
      : 'color-mix(in srgb, var(--color-text-muted) 55%, transparent)';
  return (
    <div className="w-full select-none py-0.5">
      <ShimmerLabel color={faint} className="text-[13px] leading-relaxed font-normal">
        next planing...
      </ShimmerLabel>
    </div>
  );
};

/** Cursor-like faint activity chrome: outer slightly stronger, nested lighter. */
const TextChevronToggle: React.FC<{
  primary: string;
  secondary?: string;
  open: boolean;
  onToggle: () => void;
  running?: boolean;
  /** Soft light sweep on the title while this line is in progress */
  shimmer?: boolean;
  /** 0 = outer turn, 1 = event line */
  depth?: 0 | 1;
  addedLines?: number;
  removedLines?: number;
}> = ({ primary, secondary, open, onToggle, running, shimmer, depth = 0, addedLines, removedLines }) => {
  // Inline color-mix: Tailwind opacity utilities were not reliably fading
  // primary labels (inherited theme muted stayed too strong).
  const faint =
    depth === 0
      ? 'color-mix(in srgb, var(--color-text-muted) 72%, transparent)'
      : 'color-mix(in srgb, var(--color-text-muted) 55%, transparent)';

  const title = (
    <>
      <span className="font-normal">{primary}</span>
      {secondary ? <span>{' '}{secondary}</span> : null}
    </>
  );

  return (
  <button
    type="button"
    onClick={onToggle}
    style={{ color: faint }}
    className="group inline-flex items-baseline gap-1.5 py-0.5 text-left max-w-full bg-transparent border-0 p-0 cursor-pointer"
  >
    <span className="text-[13px] leading-relaxed min-w-0" style={{ color: faint }}>
      {shimmer ? <ShimmerLabel color={faint}>{title}</ShimmerLabel> : title}
      {running && !shimmer ? <span style={{ color: faint, opacity: 0.85 }}> …</span> : null}
    </span>
    {(addedLines != null && addedLines > 0) && (
      <span className="text-[12px] font-mono font-semibold shrink-0" style={{ color: 'color-mix(in srgb, #059669 55%, transparent)' }}>
        +{addedLines}
      </span>
    )}
    {(removedLines != null && removedLines > 0) && (
      <span className="text-[12px] font-mono font-semibold shrink-0" style={{ color: 'color-mix(in srgb, #ef4444 55%, transparent)' }}>
        -{removedLines}
      </span>
    )}
    <span className="text-[13px] font-normal leading-relaxed shrink-0" style={{ color: faint }}>
      {open ? '⌄' : '>'}
    </span>
  </button>
  );
};

const SoloToolExpandPanel: React.FC<{
  toolName: string;
  args: Record<string, unknown> | null | undefined;
  result: string;
  running?: boolean;
}> = ({ toolName, args, result, running }) => {
  const hasArgs = !!(args && Object.keys(args).length > 0);
  const hasResult = !!(result && result.trim());

  return (
    <div className="mt-1 mb-1.5 rounded-md border border-border/50 bg-black/[0.035] dark:bg-white/[0.05] overflow-hidden">
      <div className="px-2.5 py-1.5 border-b border-border/40 bg-black/[0.02] dark:bg-white/[0.03]">
        <span className="text-[11px] font-mono text-textMuted/80 truncate block">{toolName}</span>
      </div>
      {hasArgs && (
        <div className="px-2.5 py-2 border-b border-border/30">
          <div className="text-[10px] font-medium text-textMuted/60 mb-1 uppercase tracking-wide">Args</div>
          <div className="space-y-1 max-h-[220px] overflow-y-auto">
            {Object.entries(args!).map(([k, v]) => {
              const valStr = typeof v === 'string' ? v : prettyJson(v);
              const isLong = valStr.length > 120 || valStr.includes('\n');
              return (
                <div key={k} className={isLong ? 'space-y-0.5' : 'flex items-start gap-2'}>
                  <span className="text-[11px] font-mono font-semibold text-amber-700/80 dark:text-amber-400/80 flex-shrink-0">
                    {k}
                  </span>
                  {isLong ? (
                    <pre className="text-[11px] text-textMuted/70 whitespace-pre-wrap break-all font-mono m-0 leading-relaxed max-h-[140px] overflow-y-auto">
                      {valStr}
                    </pre>
                  ) : (
                    <span className="text-[11px] text-textMuted/70 font-mono break-all leading-relaxed">
                      {valStr}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
      <div className="px-2.5 py-2">
        <div className="text-[10px] font-medium text-textMuted/60 mb-1 uppercase tracking-wide">
          {running && !hasResult ? 'Status' : 'Result'}
        </div>
        {running && !hasResult ? (
          <div className="text-[12px] text-textMuted/55">Running…</div>
        ) : hasResult ? (
          <pre className="text-[11px] text-textMuted/75 whitespace-pre-wrap break-words font-mono m-0 leading-relaxed max-h-[280px] overflow-y-auto">
            {prettyJson(result)}
          </pre>
        ) : (
          <div className="text-[12px] text-textMuted/45">No result</div>
        )}
      </div>
    </div>
  );
};

const SoloPlanStepIcon: React.FC<{ status: PlanStep['status'] }> = ({ status }) => {
  const muted = 'color-mix(in srgb, var(--color-text-muted) 55%, transparent)';
  switch (status) {
    case 'done':
      return <CheckCircle2 size={14} className="text-emerald-500/80 flex-shrink-0 mt-0.5" />;
    case 'running':
      return <ArrowRightCircle size={14} className="text-primary/80 flex-shrink-0 mt-0.5" />;
    case 'failed':
      return <XCircle size={14} className="text-red-500/80 flex-shrink-0 mt-0.5" />;
    default:
      return <CircleDashed size={14} className="flex-shrink-0 mt-0.5" style={{ color: muted }} strokeWidth={1.5} />;
  }
};

/** Cursor-style To-dos fold inside Solo activity stream. */
const SoloPlanFold: React.FC<{
  steps: PlanStep[];
  running?: boolean;
  defaultOpen?: boolean;
}> = ({ steps, running, defaultOpen = true }) => {
  const [open, setOpen] = useState(defaultOpen || !!running);
  useEffect(() => {
    if (defaultOpen || running) setOpen(true);
  }, [defaultOpen, running]);

  const faint = 'color-mix(in srgb, var(--color-text-muted) 55%, transparent)';

  return (
    <div className="w-full select-text my-0.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{ color: faint }}
        className="group inline-flex items-center gap-1.5 py-0.5 text-left max-w-full bg-transparent border-0 p-0 cursor-pointer"
      >
        <ListTodo size={13} className="shrink-0 opacity-80" style={{ color: faint }} />
        <span className="text-[13px] leading-relaxed" style={{ color: faint }}>
          <span className="font-medium">To-dos</span>
          <span>{' '}{steps.length}</span>
          {running ? <span style={{ opacity: 0.85 }}> …</span> : null}
        </span>
        <span className="text-[13px] shrink-0" style={{ color: faint }}>
          {open ? '⌄' : '>'}
        </span>
      </button>

      {open && (
        <div className="mt-1 mb-1 rounded-lg border border-border/55 bg-black/[0.02] dark:bg-white/[0.03] overflow-hidden">
          <div className="px-3 py-2 space-y-1.5">
            {steps.map((step, i) => (
              <div key={i} className="flex items-start gap-2">
                <SoloPlanStepIcon status={step.status} />
                <span
                  className={`text-[12.5px] leading-snug break-words ${
                    step.status === 'done'
                      ? 'line-through'
                      : step.status === 'running'
                        ? 'font-medium text-textMain/90'
                        : step.status === 'failed'
                          ? 'text-red-500/90'
                          : ''
                  }`}
                  style={
                    step.status === 'done' || step.status === 'pending'
                      ? { color: 'color-mix(in srgb, var(--color-text-muted) 70%, transparent)' }
                      : undefined
                  }
                >
                  {step.content}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

const SoloEventLine: React.FC<{
  line: ActivityLine;
  defaultOpen?: boolean;
}> = ({ line, defaultOpen = false }) => {
  const isSummary = line.kind === 'summary';
  const isProgress = line.kind === 'progress';
  // Keep compression summary open while streaming so text is visible live.
  const [open, setOpen] = useState(defaultOpen || !!(isSummary && line.running));
  const isThought = line.kind === 'thought';
  const isFileEdit = !!(line.fileEdit && (line.fileEdit.kind === 'edit' || line.fileEdit.kind === 'write'));
  const isFileRead = line.fileEdit?.kind === 'read';

  useEffect(() => {
    if (defaultOpen || (isSummary && line.running)) setOpen(true);
  }, [defaultOpen, isSummary, line.running]);

  const added = line.fileEdit?.addedLines;
  const removed = line.fileEdit?.removedLines;

  // Delegate: open Cursor-style sub-agent window (not an inline expand).
  if (line.kind === 'delegation' && line.delegation) {
    return <DelegateFold bundle={line.delegation} variant="solo" />;
  }

  // Plan / To-dos: Cursor-style bordered list fold.
  if (line.kind === 'plan' && line.planSteps && line.planSteps.length > 0) {
    return (
      <SoloPlanFold
        steps={line.planSteps}
        running={line.running}
        defaultOpen={defaultOpen || !!line.running}
      />
    );
  }

  // Progress lines are compact status rows (no nested fold needed).
  if (isProgress) {
    return (
      <div
        className="w-full select-text py-0.5 text-[12px] leading-relaxed"
        style={{ color: 'color-mix(in srgb, var(--color-text-muted) 62%, transparent)' }}
      >
        {line.running ? <span className="opacity-80">… </span> : null}
        <span className="whitespace-pre-wrap break-words">{line.detail || line.primary}</span>
      </div>
    );
  }

  return (
    <div className="w-full select-text">
      <TextChevronToggle
        primary={line.primary}
        secondary={line.secondary}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        running={line.running}
        shimmer={!!line.running && line.kind === 'tool'}
        depth={1}
        addedLines={isFileEdit ? added : undefined}
        removedLines={isFileEdit ? removed : undefined}
      />

      {/* Thought body only — title stays outside the faded panel (same as thought-only fold). */}
      {open && isThought && line.detail && (
        <div className="mt-0.5 pl-4 pr-1 rounded-sm bg-black/[0.025] dark:bg-white/[0.035] py-1">
          <pre
            className="text-[12px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[320px] overflow-y-auto"
            style={{ color: 'color-mix(in srgb, var(--color-text-muted) 42%, transparent)' }}
          >
            {line.detail}
          </pre>
        </div>
      )}

      {/* Context compression summary — live streaming text (classic-parity). */}
      {open && isSummary && (line.detail || line.running) && (
        <div
          className={`mt-0.5 pl-4 pr-1 py-1.5 rounded-md border ${
            line.summaryDone
              ? 'border-emerald-500/25 bg-emerald-500/[0.04]'
              : 'border-indigo-500/25 bg-indigo-500/[0.04]'
          }`}
        >
          {line.summaryPending && !line.detail ? (
            <div
              className="text-[12px] animate-pulse"
              style={{ color: 'color-mix(in srgb, var(--color-text-muted) 55%, transparent)' }}
            >
              Waiting for context compression…
            </div>
          ) : (
            <pre
              className="text-[12px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[360px] overflow-y-auto"
              style={{ color: 'color-mix(in srgb, var(--color-text-muted) 70%, transparent)' }}
            >
              {line.detail || 'Summarizing…'}
              {line.running && !line.summaryPending ? (
                <span className="inline-block w-1.5 h-3.5 bg-indigo-400/50 animate-pulse ml-0.5 align-middle" />
              ) : null}
            </pre>
          )}
        </div>
      )}

      {open && isFileEdit && line.fileEdit && (
        <div className="mt-1 mb-1.5 pl-4">
          <FileDiffBlock
            info={line.fileEdit}
            status={line.toolStatus || 'success'}
            note={
              line.toolResult
                ? line.toolResult.split('\n').map((l) => l.trim()).find((l) => l.length > 0)?.slice(0, 120)
                : undefined
            }
            embedded
          />
        </div>
      )}

      {open && isFileRead && line.fileEdit && (
        <div className="mt-1 mb-1.5 pl-4">
          <FileDiffBlock
            info={line.fileEdit}
            status={line.toolStatus || 'success'}
            resultContent={line.toolResult}
            embedded
          />
        </div>
      )}

      {open && line.kind === 'tool' && !isFileEdit && !isFileRead && (
        <div className="pl-4">
          <SoloToolExpandPanel
            toolName={line.toolName || line.primary}
            args={line.toolArgs}
            result={line.toolResult || ''}
            running={line.running}
          />
        </div>
      )}

      {open && line.kind === 'info' && line.detail && (
        <div className="mt-1 mb-1.5 pl-4 rounded-md border border-border/40 bg-black/[0.03] px-2.5 py-2">
          <pre
            className="text-[12px] whitespace-pre-wrap break-words font-sans m-0"
            style={{ color: 'color-mix(in srgb, var(--color-text-muted) 42%, transparent)' }}
          >
            {line.detail}
          </pre>
        </div>
      )}
    </div>
  );
};

export function mergeWorkflowBlocks(blocks: WorkflowBlock[]): WorkflowBlock {
  if (blocks.length === 1) return blocks[0];
  const events = blocks.flatMap((b) => b.events);
  const completed = blocks.every((b) => b.completed);
  const elapsed = blocks.reduce((sum, b) => sum + (typeof b.elapsed_ms === 'number' ? b.elapsed_ms : 0), 0);
  const started = blocks.find((b) => typeof b.started_ms === 'number')?.started_ms;
  const status = completed
    ? null
    : (blocks.find((b) => !b.completed)?.status ?? null);
  return {
    events,
    status,
    completed,
    elapsed_ms: elapsed > 0 ? elapsed : undefined,
    started_ms: started,
  };
}

export const SoloActivityRow: React.FC<SoloActivityRowProps> = ({
  block,
  expandDetails = false,
  turnStartedMs,
}) => {
  const [tick, setTick] = useState(0);
  const hasRunning = block.events.some(
    (e) => e.type === 'tool_call' && !e.result && !block.completed,
  );
  const hasLiveCompression = block.events.some((e) => {
    if (block.completed) return false;
    if (e.type === 'summary_stream') {
      const data = typeof e.content === 'object' && e.content ? e.content : {};
      return !data.done;
    }
    if (e.type === 'compression_progress') {
      const data = typeof e.content === 'object' && e.content ? e.content : {};
      return !data.is_final;
    }
    return false;
  });
  // Parent only passes turnStartedMs for the active incomplete group.
  // Also keep open during context compression (no turn_start / turnStartedMs).
  const isLiveTurn =
    (!block.completed && turnStartedMs != null) || hasLiveCompression || hasRunning;

  const [outerOpen, setOuterOpen] = useState(isLiveTurn || expandDetails);
  const prevExpandRef = useRef(expandDetails);

  useEffect(() => {
    if (expandDetails) {
      setOuterOpen(true);
      prevExpandRef.current = expandDetails;
      return;
    }
    // User just turned expandDetails off → collapse
    if (prevExpandRef.current && !expandDetails) {
      setOuterOpen(false);
      prevExpandRef.current = expandDetails;
      return;
    }
    prevExpandRef.current = expandDetails;

    if (isLiveTurn) setOuterOpen(true);
    else setOuterOpen(false);
  }, [isLiveTurn, expandDetails]);

  useEffect(() => {
    if (!isLiveTurn && !hasRunning && !hasLiveCompression) return;
    const t = setInterval(() => setTick((n) => n + 1), 400);
    return () => clearInterval(t);
  }, [isLiveTurn, hasRunning, hasLiveCompression]);

  const lines = useMemo(() => buildLines(block), [block, tick]);
  const summary = useMemo(
    () => outerSummary(block, lines, turnStartedMs),
    [block, lines, turnStartedMs, tick],
  );

  // Between steps: turn is live, but no concrete tool / compression / plan is in flight.
  const showNextPlanning =
    isLiveTurn &&
    !hasRunning &&
    !hasLiveCompression &&
    !lines.some((l) => !!l.running);

  if (!lines.length) {
    if (!showNextPlanning) return null;
    return (
      <div className="my-1.5 w-full select-text">
        <NextPlanningPlaceholder depth={0} />
      </div>
    );
  }

  // Thought-only (no tools / compression / delegate / plan): single fold → body text.
  const isThoughtOnly = !lines.some(
    (l) =>
      l.kind === 'tool' ||
      l.kind === 'summary' ||
      l.kind === 'progress' ||
      l.kind === 'delegation' ||
      l.kind === 'plan',
  );
  // Compression-only: one fold → summary body (avoid "Context compressed" + "Context summary done").
  const isCompressionOnly =
    !isThoughtOnly &&
    lines.every((l) => l.kind === 'summary' || l.kind === 'progress') &&
    lines.some((l) => l.kind === 'summary');
  // Plan-only (+ optional thoughts): outer fold opens to To-dos box.
  const isPlanOnly =
    !isThoughtOnly &&
    !isCompressionOnly &&
    lines.every((l) => l.kind === 'plan' || l.kind === 'thought') &&
    lines.some((l) => l.kind === 'plan');

  const thoughtBodies = lines
    .filter((l) => l.kind === 'thought' && l.detail.trim())
    .map((l) => l.detail);

  if (isThoughtOnly) {
    return (
      <div className="my-1.5 w-full select-text">
        <TextChevronToggle
          primary={summary.primary}
          secondary={summary.secondary}
          open={outerOpen}
          onToggle={() => setOuterOpen((v) => !v)}
          running={isLiveTurn}
          depth={0}
        />
        {outerOpen && thoughtBodies.length > 0 && (
          <div className="mt-0.5 pl-4 pr-1 rounded-sm bg-black/[0.025] dark:bg-white/[0.035] py-1">
            {thoughtBodies.map((text, i) => (
              <pre
                key={i}
                className="text-[12px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[320px] overflow-y-auto"
                style={{ color: 'color-mix(in srgb, var(--color-text-muted) 42%, transparent)' }}
              >
                {text}
              </pre>
            ))}
          </div>
        )}
        {showNextPlanning ? (
          <div className={outerOpen ? 'pl-4' : undefined}>
            <NextPlanningPlaceholder depth={outerOpen ? 1 : 0} />
          </div>
        ) : null}
      </div>
    );
  }

  if (isCompressionOnly) {
    // Prefer the latest summary line (streaming updates merge into one event usually).
    const summaryLines = lines.filter((l) => l.kind === 'summary');
    const summaryLine = summaryLines[summaryLines.length - 1];
    const live = !!(summaryLine?.running || hasLiveCompression);

    return (
      <div className="my-1.5 w-full select-text">
        <TextChevronToggle
          primary={summary.primary}
          secondary={summary.secondary}
          open={outerOpen}
          onToggle={() => setOuterOpen((v) => !v)}
          running={live}
          depth={0}
        />
        {outerOpen && summaryLine && (summaryLine.detail || summaryLine.running) && (
          <div
            className={`mt-0.5 pl-4 pr-1 py-1.5 rounded-md border ${
              summaryLine.summaryDone
                ? 'border-emerald-500/25 bg-emerald-500/[0.04]'
                : 'border-indigo-500/25 bg-indigo-500/[0.04]'
            }`}
          >
            {summaryLine.summaryPending && !summaryLine.detail ? (
              <div
                className="text-[12px] animate-pulse"
                style={{ color: 'color-mix(in srgb, var(--color-text-muted) 55%, transparent)' }}
              >
                Waiting for context compression…
              </div>
            ) : (
              <pre
                className="text-[12px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[360px] overflow-y-auto"
                style={{ color: 'color-mix(in srgb, var(--color-text-muted) 70%, transparent)' }}
              >
                {summaryLine.detail || 'Summarizing…'}
                {summaryLine.running && !summaryLine.summaryPending ? (
                  <span className="inline-block w-1.5 h-3.5 bg-indigo-400/50 animate-pulse ml-0.5 align-middle" />
                ) : null}
              </pre>
            )}
          </div>
        )}
        {showNextPlanning ? (
          <div className={outerOpen ? 'pl-4' : undefined}>
            <NextPlanningPlaceholder depth={outerOpen ? 1 : 0} />
          </div>
        ) : null}
      </div>
    );
  }

  // Plan-only: show optional thought body + Cursor-style To-dos fold (no extra outer wrapper).
  if (isPlanOnly) {
    const planLines = lines.filter((l) => l.kind === 'plan' && l.planSteps && l.planSteps.length > 0);
    return (
      <div className="my-1.5 w-full select-text space-y-0.5">
        {thoughtBodies.length > 0 && (
          <div className="mb-1">
            <TextChevronToggle
              primary={summary.primary.startsWith('To-dos') ? 'Thought' : summary.primary}
              secondary={summary.primary.startsWith('To-dos') ? 'for a bit' : summary.secondary}
              open={outerOpen}
              onToggle={() => setOuterOpen((v) => !v)}
              running={false}
              depth={0}
            />
            {outerOpen && (
              <div className="mt-0.5 pl-4 pr-1 rounded-sm bg-black/[0.025] dark:bg-white/[0.035] py-1">
                {thoughtBodies.map((text, i) => (
                  <pre
                    key={i}
                    className="text-[12px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[320px] overflow-y-auto"
                    style={{ color: 'color-mix(in srgb, var(--color-text-muted) 42%, transparent)' }}
                  >
                    {text}
                  </pre>
                ))}
              </div>
            )}
          </div>
        )}
        {planLines.map((line) => (
          <SoloPlanFold
            key={line.key}
            steps={line.planSteps!}
            running={line.running}
            defaultOpen
          />
        ))}
        {showNextPlanning ? <NextPlanningPlaceholder /> : null}
      </div>
    );
  }

  return (
    <div className="my-1.5 w-full select-text">
      <TextChevronToggle
        primary={summary.primary}
        secondary={summary.secondary}
        open={outerOpen}
        onToggle={() => setOuterOpen((v) => !v)}
        running={isLiveTurn || hasRunning || hasLiveCompression}
        depth={0}
      />
      {/* Depth 1: event lines indented under the outer fold */}
      {outerOpen && (
        <div className="mt-0.5 space-y-0.5 pl-4">
          {lines.map((line) => (
            <SoloEventLine
              key={line.key}
              line={line}
              defaultOpen={
                expandDetails ||
                !!(line.kind === 'summary' && line.running) ||
                line.kind === 'plan'
              }
            />
          ))}
          {showNextPlanning ? <NextPlanningPlaceholder /> : null}
        </div>
      )}
    </div>
  );
};
