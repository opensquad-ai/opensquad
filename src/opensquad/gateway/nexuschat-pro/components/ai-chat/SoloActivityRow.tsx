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
import type { WorkflowBlock, WorkflowEvent } from '../../utils/aiChatTimeline';
import { isToolResultFailure } from '../../utils/aiChatTimeline';
import { hasOpenAsyncDelegate } from '../../utils/aiChatTimeline';
import { FileDiffBlock, extractFileEditInfo, parsePartialFileToolArgs, applyEditDiffContext, type FileEditInfo } from './FileDiffBlock';
import { formatElapsedAtLeastOneSecond } from '../../utils/formatElapsed';
import { buildDisplayWorkflowItems, type DelegateBundle } from '../../utils/delegateGrouping';
import {
  attachShellJobsToDisplayItems,
  type ShellJobBundle,
  type ShellStreamState,
} from '../../utils/shellJobGrouping';
import { DelegateFold } from './DelegateFold';
import { ShellJobFold } from './ShellJobFold';
import { parsePlanContent, PlanBlock, type PlanStep } from './PlanBlock';
import { FollowScrollBox } from './FollowScrollBox';
import { MarkdownScrollBody } from './MarkdownScrollBody';
import { extractHtmlEmbed, isVisualizationToolName } from './HtmlEmbedBlock';
import { PulseDotsOrbit, PulseDotsStatus } from './PulseDotsStatus';
import {
  type WorkflowExpandLevel,
  workflowExpandFlags,
} from '../../utils/workflowExpandPref';

/** When Solo workflow step count exceeds this, nest lines in a scroll box. */
const SOLO_STEPS_SCROLL_THRESHOLD = 10;
const SOLO_STEPS_SCROLL_MAX_CLASS = 'max-h-[280px]';

interface SoloActivityRowProps {
  block: WorkflowBlock;
  /**
   * Progressive auto-expand for thought / plan / tool folds.
   * Only seeds defaults; never overrides a fold the user has toggled.
   */
  expandLevel?: WorkflowExpandLevel;
  turnStartedMs?: number;
  /** Live shell job stdout keyed by tool call_id */
  shellStreams?: Record<string, ShellStreamState>;
  /** Open a project file in the right-side files panel */
  onOpenFile?: (path: string) => void;
  /**
   * Classic Agent Web only: embed visualization.create HTML in the dialog.
   * Solo must leave this false.
   */
  embedVisualizations?: boolean;
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

type LineKind = 'thought' | 'tool' | 'info' | 'summary' | 'progress' | 'delegation' | 'plan' | 'shell_job';

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
  /** system.start_job / run_session_job live CMD panel */
  shellJob?: ShellJobBundle;
  /** Parsed <plan> steps for Solo plan fold */
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
    // Open tools stay "running" even if the block was wrongly sealed (disk
    // hydrate / mid-turn to_user). Otherwise long Agent Web turns flip to
    // "Worked" and look frozen while websearch etc. are still in flight.
    const running = !evt.result;
    const name = toolNameOf(evt);
    const content = typeof evt.content === 'object' && evt.content ? evt.content : {};
    const rawArgs = content.arguments ?? content.args ?? content.input;
    const argsObj =
      parseArgs(rawArgs) ||
      (typeof rawArgs === 'string' ? parsePartialFileToolArgs(rawArgs) : null) ||
      (typeof rawArgs === 'object' && rawArgs ? rawArgs : null);
    const resultStr = formatResult(evt.result);
    const fileEdit = applyEditDiffContext(
      extractFileEditInfo(name, argsObj || rawArgs || {}),
      {
        diffOld: evt.diffOld,
        diffNew: evt.diffNew,
        diffStartLine: evt.diffStartLine,
      },
    );
    const failed =
      !running &&
      (evt.resultStatus === 'error' || isToolResultFailure(evt.result) || isToolResultFailure(resultStr));
    const status: 'running' | 'success' | 'error' = running
      ? 'running'
      : failed
        ? 'error'
        : 'success';

    let primary = name;
    let secondary = '';
    if (fileEdit) {
      if (fileEdit.kind === 'read') {
        primary = running
          ? `Reading ${fileEdit.fileName}`
          : failed
            ? `Failed read ${fileEdit.fileName}`
            : `Read ${fileEdit.fileName}`;
        secondary = fileEdit.lineRange || '';
      } else if (fileEdit.kind === 'write') {
        primary = running
          ? `Writing ${fileEdit.fileName}`
          : failed
            ? `Failed write ${fileEdit.fileName}`
            : `Wrote ${fileEdit.fileName}`;
      } else {
        primary = running
          ? `Editing ${fileEdit.fileName}`
          : failed
            ? `Failed edit ${fileEdit.fileName}`
            : `Edited ${fileEdit.fileName}`;
      }
    } else if (running) {
      primary = 'Running';
      secondary = name;
    } else if (failed) {
      secondary = 'fail';
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
      fileEdit: failed ? (fileEdit ? { ...fileEdit, addedLines: undefined, removedLines: undefined } : null) : fileEdit,
      toolStatus: status,
    });
    return lines;
  }

  if (evt.type === 'tool_result') {
    const data = typeof evt.content === 'object' ? evt.content : {};
    const name = String(data.name || data.tool || 'Tool');
    const result = formatResult(data.result ?? data.output ?? data);
    const failed = !!(data.error || isToolResultFailure(result));
    lines.push({
      key,
      kind: 'tool',
      primary: name,
      secondary: failed ? 'fail' : '',
      detail: '',
      toolName: name,
      toolArgs: null,
      toolResult: result,
      fileEdit: null,
      toolStatus: failed ? 'error' : 'success',
    });
    return lines;
  }

  if (evt.type === 'info') {
    const text =
      typeof evt.content === 'string'
        ? evt.content
        : evt.content?.text || evt.content?.message || '';
    if (!text) return lines;
    // Lifecycle noise (also filtered in timeline convert / live WS).
    if (/^New session started$/i.test(String(text).trim()) || /^Workflow started$/i.test(String(text).trim())) {
      return lines;
    }
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
      primary: 'plan',
      secondary: String(steps.length),
      detail: done > 0 ? `${done}/${steps.length}` : '',
      running: running && !blockCompleted,
      planSteps: steps,
    });
  }

  return lines;
}

function buildLines(
  block: WorkflowBlock,
  shellStreams: Record<string, ShellStreamState> = {},
): ActivityLine[] {
  const lines: ActivityLine[] = [];
  const baseItems = buildDisplayWorkflowItems(block.events);
  const items = attachShellJobsToDisplayItems(baseItems, shellStreams);

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
    if (item.kind === 'shell_job') {
      lines.push({
        key: item.key,
        kind: 'shell_job',
        primary: item.bundle.running ? 'Running' : item.bundle.errored ? 'Shell failed' : 'Ran',
        secondary: item.bundle.command,
        detail: item.bundle.output,
        running: item.bundle.running,
        shellJob: item.bundle,
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

function frozenElapsedMs(block: WorkflowBlock, turnStartedMs?: number): number | null {
  if (typeof block.elapsed_ms === 'number') return Math.max(0, block.elapsed_ms);
  const start =
    (typeof turnStartedMs === 'number' ? turnStartedMs : undefined)
    ?? (typeof block.started_ms === 'number' ? block.started_ms : undefined)
    ?? (typeof block.events[0]?.timestamp === 'number' ? block.events[0].timestamp : undefined);
  if (typeof start !== 'number') return null;
  let end: number | undefined;
  for (let i = block.events.length - 1; i >= 0; i--) {
    const ts = block.events[i]?.timestamp;
    if (typeof ts === 'number' && ts >= start) {
      end = ts;
      break;
    }
  }
  // Completed turns must NEVER use Date.now() — that keeps "Worked for"
  // climbing after stop / no assistant reply (e.g. 11h ghosts).
  return Math.max(0, (end ?? start) - start);
}

function outerSummary(
  block: WorkflowBlock,
  lines: ActivityLine[],
  turnStartedMs?: number,
): { primary: string; secondary: string } {
  const thoughts = lines.filter((l) => l.kind === 'thought').length;
  const tools = lines.filter((l) => l.kind === 'tool' || l.kind === 'delegation' || l.kind === 'shell_job').length;
  const plans = lines.filter((l) => l.kind === 'plan');
  const summaries = lines.filter((l) => l.kind === 'summary');
  const liveSummary = summaries.some((l) => l.running);
  const liveTool = lines.some((l) => l.kind === 'tool' && l.running);
  const livePlan = plans.some((l) => l.running);
  const hasLiveLine = lines.some((l) => l.running);
  const stillLive = !!(liveTool || livePlan || liveSummary || (hasLiveLine && !block.completed) || !block.completed);
  let elapsedMs: number | null = null;
  if (stillLive) {
    if (turnStartedMs != null) {
      elapsedMs = Math.max(0, Date.now() - turnStartedMs);
    } else if (typeof block.started_ms === 'number') {
      elapsedMs = Math.max(0, Date.now() - block.started_ms);
    } else if (typeof block.elapsed_ms === 'number') {
      elapsedMs = Math.max(0, block.elapsed_ms);
    }
  } else if (typeof block.elapsed_ms === 'number') {
    elapsedMs = Math.max(0, block.elapsed_ms);
  } else if (block.completed) {
    elapsedMs = frozenElapsedMs(block, turnStartedMs);
  }
  const elapsedLabel =
    elapsedMs != null ? formatElapsedAtLeastOneSecond(elapsedMs) : null;

  if (liveSummary) {
    if (elapsedLabel != null) return { primary: `Compressing for ${elapsedLabel}`, secondary: '' };
    return { primary: 'Compressing context', secondary: '' };
  }
  // Summary finished (even if workflow block not yet marked completed).
  if (summaries.length > 0 && summaries.every((l) => !l.running) && block.completed) {
    return { primary: 'Context compressed', secondary: '' };
  }
  if (liveTool || livePlan || (hasLiveLine && !block.completed)) {
    if (elapsedLabel != null) return { primary: `Working for ${elapsedLabel}`, secondary: '' };
    return { primary: 'Working', secondary: '' };
  }
  // Incomplete block = still working (even between tool rounds / without turnStartedMs).
  if (!block.completed) {
    if (elapsedLabel != null) return { primary: `Working for ${elapsedLabel}`, secondary: '' };
    return { primary: 'Working', secondary: '' };
  }
  if (tools > 0 && elapsedLabel != null) return { primary: `Worked for ${elapsedLabel}`, secondary: '' };
  if (tools > 0) return { primary: 'Worked', secondary: '' };
  if (plans.length > 0) {
    const n = plans.reduce((sum, l) => sum + (l.planSteps?.length || 0), 0);
    if (n > 0) return { primary: 'plan', secondary: String(n) };
    return { primary: 'Planned', secondary: '' };
  }
  if (thoughts > 0) {
    if (elapsedMs != null && elapsedMs >= 2000 && elapsedLabel != null) {
      return { primary: 'Thought', secondary: `for ${elapsedLabel}` };
    }
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
  <span
    className={`solo-text-shimmer ${className || ''}`}
    style={{ ['--solo-shimmer-base' as string]: color, color }}
  >
    {children}
  </span>
);

/** Waiting buffer while the next thought / tool call is being prepared. */
const NextPlanningPlaceholder: React.FC<{
  depth?: 0 | 1;
  /** Classic Agent Web: Manus-style pulse dots instead of shimmer ellipsis */
  classic?: boolean;
  startedMs?: number;
}> = ({ depth = 1, classic = false, startedMs }) => {
  if (classic) {
    return (
      <div className="w-full select-none py-0.5">
        <PulseDotsStatus kind="preparing" startedMs={startedMs} />
      </div>
    );
  }
  const faint =
    depth === 0
      ? 'color-mix(in srgb, var(--color-text-muted) 72%, transparent)'
      : 'color-mix(in srgb, var(--color-text-muted) 55%, transparent)';
  return (
    <div className="w-full select-none py-0.5">
      <ShimmerLabel color={faint} className="text-[13px] leading-relaxed font-normal">
        next planning...
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
  /** Failed tool outcome — red title + fail cue */
  errored?: boolean;
  /** Filename substring in primary — dashed underline + click (does not toggle) */
  fileLabel?: string;
  onFileClick?: () => void;
  /** Dot-matrix orbit before the title (e.g. live “Working for”) */
  leadingPulse?: boolean;
}> = ({ primary, secondary, open, onToggle, running, shimmer, depth = 0, addedLines, removedLines, errored, fileLabel, onFileClick, leadingPulse }) => {
  // Inline color-mix: Tailwind opacity utilities were not reliably fading
  // primary labels (inherited theme muted stayed too strong).
  const faint = errored
    ? 'color-mix(in srgb, #dc2626 78%, transparent)'
    : depth === 0
      ? 'color-mix(in srgb, var(--color-text-muted) 72%, transparent)'
      : 'color-mix(in srgb, var(--color-text-muted) 55%, transparent)';

  const fileIdx = fileLabel && onFileClick ? primary.indexOf(fileLabel) : -1;
  const primaryNode =
    fileIdx >= 0 && fileLabel && onFileClick ? (
      <span className="font-normal">
        {primary.slice(0, fileIdx)}
        <span
          role="link"
          tabIndex={0}
          className="underline decoration-dashed decoration-white/30 underline-offset-2 hover:decoration-white/50"
          onClick={(e) => {
            e.stopPropagation();
            e.preventDefault();
            onFileClick();
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.stopPropagation();
              e.preventDefault();
              onFileClick();
            }
          }}
        >
          {fileLabel}
        </span>
        {primary.slice(fileIdx + fileLabel.length)}
      </span>
    ) : (
      <span className="font-normal">{primary}</span>
    );

  const title = (
    <>
      {primaryNode}
      {secondary ? (
        <span className={errored && secondary === 'fail' ? 'font-medium' : undefined}>
          {' '}
          {secondary}
        </span>
      ) : null}
    </>
  );

  return (
  <button
    type="button"
    onClick={onToggle}
    style={{ color: faint }}
    className="group inline-flex items-center gap-1.5 py-0.5 text-left max-w-full bg-transparent border-0 p-0 cursor-pointer"
  >
    {leadingPulse ? <PulseDotsOrbit size={depth === 0 ? 16 : 14} /> : null}
    <span className="text-[13px] leading-relaxed min-w-0" style={{ color: faint }}>
      {shimmer ? <ShimmerLabel color={faint}>{title}</ShimmerLabel> : title}
    </span>
    {!errored && (addedLines != null && addedLines > 0) && (
      <span className="text-[12px] font-mono font-semibold shrink-0" style={{ color: 'color-mix(in srgb, #059669 55%, transparent)' }}>
        +{addedLines}
      </span>
    )}
    {!errored && (removedLines != null && removedLines > 0) && (
      <span className="text-[12px] font-mono font-semibold shrink-0" style={{ color: 'color-mix(in srgb, #ef4444 55%, transparent)' }}>
        -{removedLines}
      </span>
    )}
    {errored && secondary !== 'fail' ? (
      <span className="text-[11px] font-medium shrink-0" style={{ color: faint }}>
        fail
      </span>
    ) : null}
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
  /** Hide bulky html arg when the iframe is rendered below the assistant reply */
  hideHtmlArg?: boolean;
}> = ({ toolName, args, result, running, hideHtmlArg = false }) => {
  const hasArgs = !!(args && Object.keys(args).length > 0);
  const hasResult = !!(result && result.trim());
  const displayArgs = useMemo(() => {
    if (!args || !hideHtmlArg) return args;
    const next = { ...args };
    if ('html' in next) {
      const raw = next.html;
      const len = typeof raw === 'string' ? raw.length : JSON.stringify(raw ?? '').length;
      next.html = `[HTML ${len} chars — rendered below reply]`;
    }
    return next;
  }, [args, hideHtmlArg]);

  // Prefer short host message from html_embed result
  const displayResult = useMemo(() => {
    if (!hasResult) return '';
    try {
      const o = JSON.parse(result);
      if (o && typeof o === 'object' && o.kind === 'html_embed') {
        if (typeof o.text === 'string' && o.text.trim()) return o.text;
        if (Array.isArray(o.content) && o.content[0]?.text) return String(o.content[0].text);
        return `Interactive visualization "${o.filename || o.title || 'viz'}" was created.`;
      }
    } catch {
      /* fall through */
    }
    return result;
  }, [hasResult, result]);

  return (
    <div className="mt-1 mb-1.5 rounded-md border border-border/50 bg-black/[0.035] dark:bg-white/[0.05] overflow-hidden">
      <div className="px-2.5 py-1.5 border-b border-border/40 bg-black/[0.02] dark:bg-white/[0.03]">
        <span className="text-[11px] font-mono text-textMuted/80 truncate block">{toolName}</span>
      </div>
      {hasArgs && (
        <div className="px-2.5 py-2 border-b border-border/30">
          <div className="text-[10px] font-medium text-textMuted/60 mb-1 uppercase tracking-wide">Input</div>
          <div className="space-y-1 max-h-[220px] overflow-y-auto">
            {Object.entries(displayArgs || {}).map(([k, v]) => {
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
          {running && !hasResult ? 'Status' : 'Output'}
        </div>
        {running && !hasResult ? (
          <div className="text-[12px] text-textMuted/55">Running…</div>
        ) : hasResult ? (
          <pre className="text-[11px] text-textMuted/75 whitespace-pre-wrap break-words font-mono m-0 leading-relaxed max-h-[280px] overflow-y-auto">
            {prettyJson(displayResult)}
          </pre>
        ) : (
          <div className="text-[12px] text-textMuted/45">No result</div>
        )}
      </div>
    </div>
  );
};

/** Workflow-style Plan card inside Solo activity stream. */
const SoloPlanFold: React.FC<{
  steps: PlanStep[];
  running?: boolean;
  defaultOpen?: boolean;
}> = ({ steps, running, defaultOpen = true }) => (
  <div className="w-full select-text my-0.5">
    <PlanBlock
      steps={steps}
      defaultOpen={defaultOpen || !!running}
      className="mb-0 border border-border/55 rounded-lg overflow-hidden bg-black/[0.02] dark:bg-white/[0.03]"
    />
  </div>
);

const SoloEventLine: React.FC<{
  line: ActivityLine;
  defaultOpen?: boolean;
  shellStreamFor?: (callId: string) => ShellStreamState | null | undefined;
  onOpenFile?: (path: string) => void;
  /** @deprecated Embeds render below the assistant reply; tool stream stays a normal tool row. */
  embedVisualizations?: boolean;
}> = ({ line, defaultOpen = false, shellStreamFor, onOpenFile, embedVisualizations: _embedVisualizations = false }) => {
  void _embedVisualizations;
  const isSummary = line.kind === 'summary';
  const isProgress = line.kind === 'progress';
  // Keep compression summary open while streaming so text is visible live.
  const [open, setOpen] = useState(defaultOpen || !!(isSummary && line.running));
  /** Once the user toggles this fold, preference changes must not fight them. */
  const userTouchedRef = useRef(false);
  const isThought = line.kind === 'thought';
  const isFileEdit = !!(line.fileEdit && (line.fileEdit.kind === 'edit' || line.fileEdit.kind === 'write'));
  const isFileRead = line.fileEdit?.kind === 'read';
  // Hide bulky html arg/result text in the tool expand panel; iframe renders below the reply.
  const hideVizHtml =
    line.kind === 'tool' &&
    (!!extractHtmlEmbed(line.toolName, line.toolArgs, line.toolResult) ||
      isVisualizationToolName(line.toolName));

  useEffect(() => {
    if (userTouchedRef.current) return;
    // Preference only seeds defaults: open when asked, never force-close.
    if (defaultOpen) {
      setOpen(true);
      return;
    }
    if ((isSummary && line.running) || (isFileEdit && line.running)) setOpen(true);
  }, [defaultOpen, isSummary, isFileEdit, line.running]);

  const toggleOpen = () => {
    userTouchedRef.current = true;
    setOpen((v) => !v);
  };

  const added = line.fileEdit?.addedLines;
  const removed = line.fileEdit?.removedLines;

  // Delegate: open Cursor-style sub-agent window (not an inline expand).
  if (line.kind === 'delegation' && line.delegation) {
    return <DelegateFold bundle={line.delegation} variant="solo" />;
  }

  // Shell job: CMD-style live panel
  if (line.kind === 'shell_job' && line.shellJob) {
    return (
      <ShellJobFold
        bundle={line.shellJob}
        stream={shellStreamFor?.(line.shellJob.id)}
        variant="solo"
      />
    );
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
        onToggle={toggleOpen}
        running={line.running}
        shimmer={!!line.running && (line.kind === 'tool' || line.kind === 'thought' || line.kind === 'summary')}
        depth={1}
        addedLines={isFileEdit ? added : undefined}
        removedLines={isFileEdit ? removed : undefined}
        errored={line.kind === 'tool' && line.toolStatus === 'error'}
        fileLabel={line.fileEdit?.fileName}
        onFileClick={
          line.fileEdit && onOpenFile
            ? () => onOpenFile(line.fileEdit!.filePath)
            : undefined
        }
      />

      {/* Thought body only — title stays outside the faded panel (same as thought-only fold). */}
      {open && isThought && line.detail && (
        <div className="mt-0.5 pl-4 pr-1 rounded-sm bg-black/[0.025] dark:bg-white/[0.035] py-1">
          <MarkdownScrollBody
            text={line.detail}
            follow={!!line.running}
            muted
            maxHeightClass="max-h-[320px]"
          />
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
            <FollowScrollBox
              as="pre"
              contentKey={(line.detail || '').length}
              follow={!!line.running}
              className="text-[12px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[360px] overflow-y-auto"
              style={{ color: 'color-mix(in srgb, var(--color-text-muted) 70%, transparent)' }}
            >
              {line.detail || 'Summarizing…'}
              {line.running && !line.summaryPending ? (
                <span className="inline-block w-1.5 h-3.5 bg-indigo-400/50 animate-pulse ml-0.5 align-middle" />
              ) : null}
            </FollowScrollBox>
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
            hideHtmlArg={hideVizHtml}
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
  expandLevel = 'thoughts',
  turnStartedMs,
  shellStreams = {},
  onOpenFile,
  embedVisualizations = false,
}) => {
  const expand = workflowExpandFlags(expandLevel);
  const [tick, setTick] = useState(0);
  const hasOpenTools = block.events.some(
    (e) => e.type === 'tool_call' && !e.result,
  );
  // Async delegate_task_submit keeps the sub-agent window live after the parent
  // turn seals — treat it as still running so the outer fold / panel stay mounted.
  const hasAsyncDelegate = hasOpenAsyncDelegate(block.events);
  const hasLiveShell = Object.values(shellStreams).some((s) => s.state === 'running');
  const hasRunning = hasOpenTools || hasAsyncDelegate || hasLiveShell;
  const hasLiveCompression = block.events.some((e) => {
    if (block.completed && !hasAsyncDelegate) return false;
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
  // Treat any incomplete block as live so gaps between tool rounds do not
  // flip the header to "Worked" and auto-collapse while the agent is still going.
  const isLiveTurn = !block.completed || hasLiveCompression || hasRunning;

  const [outerOpen, setOuterOpen] = useState(isLiveTurn);
  const wasLiveRef = useRef(isLiveTurn);
  /** User pin: 'open' | 'closed' | null (follow auto open/collapse). */
  const userOverrideRef = useRef<'open' | 'closed' | null>(null);
  const stepsScrollRef = useRef<HTMLDivElement>(null);
  const stepsAtBottomRef = useRef(true);
  const rootRef = useRef<HTMLDivElement>(null);

  const toggleOuter = () => {
    setOuterOpen((v) => {
      const next = !v;
      userOverrideRef.current = next ? 'open' : 'closed';
      return next;
    });
  };

  useEffect(() => {
    if (isLiveTurn && !wasLiveRef.current) {
      // Rising edge of a live turn: auto-open only when the user has not
      // pinned open/closed. Do NOT clear the pin — soft-poll history rebuilds
      // can flicker completed↔live and would otherwise wipe a deliberate
      // collapse and snap the fold shut/open against the user.
      if (userOverrideRef.current == null) setOuterOpen(true);
    } else if (isLiveTurn) {
      if (userOverrideRef.current !== 'closed') setOuterOpen(true);
    } else if (userOverrideRef.current !== 'open') {
      // Turn finished → auto-collapse unless user pinned the fold open
      setOuterOpen(false);
    }
    wasLiveRef.current = isLiveTurn;
  }, [isLiveTurn]);

  useEffect(() => {
    if (!isLiveTurn && !hasRunning && !hasLiveCompression) return;
    const t = setInterval(() => {
      // Elapsed-time header only — never rebuild line bodies on this tick.
      // Skip entirely while the user has a text selection (any pane).
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed) return;
      setTick((n) => n + 1);
    }, 1000);
    return () => clearInterval(t);
  }, [isLiveTurn, hasRunning, hasLiveCompression]);

  // Do NOT put `tick` in buildLines deps — that remounted thought/tool text every
  // 400ms and cleared mouse selections in scheduled-task / live panes.
  const lines = useMemo(() => buildLines(block, shellStreams), [block, shellStreams]);
  const summary = useMemo(
    () => outerSummary(block, lines, turnStartedMs),
    [block, lines, turnStartedMs, tick],
  );

  // Active phase detection: while the latest step is still thought / plan /
  // compression / a running tool, do NOT show "next planning…".
  // That placeholder is only for the idle gap *after* real work has settled,
  // waiting on the agent's next move — never on an empty / lifecycle-only block
  // (new session, mode switch, Workflow started with no thoughts yet).
  const lastActivity = useMemo(() => {
    for (let i = lines.length - 1; i >= 0; i--) {
      const l = lines[i];
      if (l.kind === 'info') continue;
      return l;
    }
    return null;
  }, [lines]);

  const thinkingActive =
    isLiveTurn && !block.completed && lastActivity?.kind === 'thought';
  const planningActive =
    isLiveTurn &&
    !block.completed &&
    (lastActivity?.kind === 'plan' || lines.some((l) => l.kind === 'plan' && !!l.running));

  const displayLines = useMemo(() => {
    if (!thinkingActive) return lines;
    let lastThoughtIdx = -1;
    for (let i = lines.length - 1; i >= 0; i--) {
      if (lines[i].kind === 'thought') {
        lastThoughtIdx = i;
        break;
      }
    }
    if (lastThoughtIdx < 0) return lines;
    return lines.map((l, i) => (i === lastThoughtIdx ? { ...l, running: true } : l));
  }, [lines, thinkingActive]);

  const useStepsScrollBox = displayLines.length > SOLO_STEPS_SCROLL_THRESHOLD;

  // Live turns: keep the steps box pinned to the bottom while the user hasn't scrolled up.
  useEffect(() => {
    if (!outerOpen || !useStepsScrollBox || !isLiveTurn) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    const el = stepsScrollRef.current;
    if (!el || !stepsAtBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [displayLines.length, outerOpen, useStepsScrollBox, isLiveTurn, tick]);

  const hasSettledActivity = displayLines.some(
    (l) =>
      (l.kind === 'thought' ||
        l.kind === 'tool' ||
        l.kind === 'plan' ||
        l.kind === 'summary' ||
        l.kind === 'progress' ||
        l.kind === 'delegation' ||
        l.kind === 'shell_job') &&
      !l.running,
  );

  const showNextPlanning =
    isLiveTurn &&
    !hasRunning &&
    !hasLiveCompression &&
    !thinkingActive &&
    !planningActive &&
    !displayLines.some((l) => !!l.running) &&
    hasSettledActivity;

  const liveStartedMs =
    turnStartedMs ??
    (typeof block.started_ms === 'number' ? block.started_ms : undefined);

  const renderOuterToggle = (opts?: { shimmer?: boolean; running?: boolean }) => {
    const running = opts?.running ?? isLiveTurn;
    return (
      <TextChevronToggle
        primary={summary.primary}
        secondary={summary.secondary}
        open={outerOpen}
        onToggle={toggleOuter}
        running={running}
        shimmer={opts?.shimmer}
        depth={0}
        leadingPulse={!!running}
      />
    );
  };

  // Empty / lifecycle-only incomplete blocks: render nothing (matches classic
  // WorkflowBlockView). Never show a lone "next planning…" on a blank session.
  if (!lines.length) {
    return null;
  }

  // Thought-only (no tools / compression / delegate / plan): single fold → body text.
  const isThoughtOnly = !displayLines.some(
    (l) =>
      l.kind === 'tool' ||
      l.kind === 'summary' ||
      l.kind === 'progress' ||
      l.kind === 'delegation' ||
      l.kind === 'shell_job' ||
      l.kind === 'plan',
  );
  // Compression-only: one fold → summary body (avoid "Context compressed" + "Context summary done").
  const isCompressionOnly =
    !isThoughtOnly &&
    displayLines.every((l) => l.kind === 'summary' || l.kind === 'progress') &&
    displayLines.some((l) => l.kind === 'summary');
  // Plan-only (+ optional thoughts): outer fold opens to To-dos box.
  const isPlanOnly =
    !isThoughtOnly &&
    !isCompressionOnly &&
    displayLines.every((l) => l.kind === 'plan' || l.kind === 'thought') &&
    displayLines.some((l) => l.kind === 'plan');

  const thoughtBodies = displayLines
    .filter((l) => l.kind === 'thought' && l.detail.trim())
    .map((l) => l.detail);

  if (isThoughtOnly) {
    // Info-only / empty chrome used to render a bare "Activity" fold on new session.
    if (thoughtBodies.length === 0 && !showNextPlanning) return null;
    if (thoughtBodies.length === 0 && showNextPlanning) {
      return (
        <div ref={rootRef} className="my-1.5 w-full select-text">
          <NextPlanningPlaceholder
            depth={0}
            classic={embedVisualizations}
            startedMs={liveStartedMs}
          />
        </div>
      );
    }

    return (
      <div ref={rootRef} className="my-1.5 w-full select-text">
        {renderOuterToggle({ running: isLiveTurn, shimmer: thinkingActive })}
        {outerOpen && thoughtBodies.length > 0 && (
          <div className="mt-0.5 pl-4 pr-1 rounded-sm bg-black/[0.025] dark:bg-white/[0.035] py-1">
            {thoughtBodies.map((text, i) => (
              <MarkdownScrollBody
                key={i}
                text={text}
                follow={thinkingActive && i === thoughtBodies.length - 1}
                muted
                maxHeightClass="max-h-[320px]"
              />
            ))}
          </div>
        )}
        {showNextPlanning ? (
          <div className={outerOpen ? 'pl-4' : undefined}>
            <NextPlanningPlaceholder
              depth={outerOpen ? 1 : 0}
              classic={embedVisualizations}
              startedMs={liveStartedMs}
            />
          </div>
        ) : null}
      </div>
    );
  }

  if (isCompressionOnly) {
    // Prefer the latest summary line (streaming updates merge into one event usually).
    const summaryLines = displayLines.filter((l) => l.kind === 'summary');
    const summaryLine = summaryLines[summaryLines.length - 1];
    const live = !!(summaryLine?.running || hasLiveCompression);

    return (
      <div ref={rootRef} className="my-1.5 w-full select-text">
        {renderOuterToggle({ running: live, shimmer: live })}
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
              <FollowScrollBox
                as="pre"
                contentKey={(summaryLine.detail || '').length}
                follow={!!summaryLine.running}
                className="text-[12px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[360px] overflow-y-auto"
                style={{ color: 'color-mix(in srgb, var(--color-text-muted) 70%, transparent)' }}
              >
                {summaryLine.detail || 'Summarizing…'}
                {summaryLine.running && !summaryLine.summaryPending ? (
                  <span className="inline-block w-1.5 h-3.5 bg-indigo-400/50 animate-pulse ml-0.5 align-middle" />
                ) : null}
              </FollowScrollBox>
            )}
          </div>
        )}
        {showNextPlanning ? (
          <div className={outerOpen ? 'pl-4' : undefined}>
            <NextPlanningPlaceholder
              depth={outerOpen ? 1 : 0}
              classic={embedVisualizations}
              startedMs={liveStartedMs}
            />
          </div>
        ) : null}
      </div>
    );
  }

  // Plan-only: show optional thought body + Cursor-style To-dos fold (no extra outer wrapper).
  if (isPlanOnly) {
    const planLines = displayLines.filter((l) => l.kind === 'plan' && l.planSteps && l.planSteps.length > 0);
    return (
      <div ref={rootRef} className="my-1.5 w-full select-text space-y-0.5">
        {thoughtBodies.length > 0 && (
          <div className="mb-1">
            {renderOuterToggle({ running: thinkingActive, shimmer: thinkingActive })}
            {outerOpen && (
              <div className="mt-0.5 pl-4 pr-1 rounded-sm bg-black/[0.025] dark:bg-white/[0.035] py-1">
                {thoughtBodies.map((text, i) => (
                  <MarkdownScrollBody
                    key={i}
                    text={text}
                    follow={thinkingActive && i === thoughtBodies.length - 1}
                    muted
                    maxHeightClass="max-h-[320px]"
                  />
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
        {showNextPlanning ? (
          <NextPlanningPlaceholder classic={embedVisualizations} startedMs={liveStartedMs} />
        ) : null}
      </div>
    );
  }

  return (
    <div ref={rootRef} className="my-1.5 w-full select-text">
      {renderOuterToggle({
        running: isLiveTurn || hasRunning || hasLiveCompression,
        shimmer: thinkingActive || hasRunning || hasLiveCompression,
      })}
      {/* Depth 1: event lines indented under the outer fold.
          >10 steps → fixed-height scroll box so the page doesn't grow forever.
          Delegate folds stay mounted (hidden when collapsed) so an open
          SubAgentPanel keeps receiving live job_id updates after the turn seals. */}
      <div
        ref={stepsScrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stepsAtBottomRef.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < 28;
        }}
        className={
          !outerOpen
            ? 'hidden'
            : useStepsScrollBox
              ? `mt-0.5 space-y-0.5 pl-3 pr-1 py-1 ${SOLO_STEPS_SCROLL_MAX_CLASS} overflow-y-auto overscroll-contain rounded-md border border-border/45 bg-black/[0.02] dark:bg-white/[0.03]`
              : 'mt-0.5 space-y-0.5 pl-4'
        }
        aria-hidden={!outerOpen}
      >
        {displayLines.map((line) => (
          <SoloEventLine
            key={line.key}
            line={line}
            shellStreamFor={(id) => shellStreams[id]}
            onOpenFile={onOpenFile}
            embedVisualizations={embedVisualizations}
            defaultOpen={
              (line.kind === 'thought' && expand.thoughts) ||
              (line.kind === 'plan' && expand.plan) ||
              (line.kind === 'tool' && expand.tools) ||
              !!(line.kind === 'summary' && line.running) ||
              !!(line.kind === 'tool' && line.running && line.fileEdit &&
                (line.fileEdit.kind === 'write' || line.fileEdit.kind === 'edit'))
            }
          />
        ))}
        {showNextPlanning ? (
          <NextPlanningPlaceholder classic={embedVisualizations} startedMs={liveStartedMs} />
        ) : null}
      </div>
    </div>
  );
};
