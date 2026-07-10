/**
 * SoloActivityRow — Cursor-style document-flow activity for Solo UI.
 *
 * Outer fold (turn-level): collapses the whole thought+tool process once the
 * agent finishes the turn. Inner lines:
 *   - thought: faded text body
 *   - file edit/write: fold shows +N -M; expand → embedded FileDiffBlock
 *   - other tools (websearch, etc.): expand → light box with Args + Result
 */
import React, { useEffect, useMemo, useState } from 'react';
import type { WorkflowBlock, WorkflowEvent } from '../../utils/aiChatTimeline';
import { FileDiffBlock, extractFileEditInfo, type FileEditInfo } from './FileDiffBlock';

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

type LineKind = 'thought' | 'tool' | 'info';

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
}

function buildLines(block: WorkflowBlock): ActivityLine[] {
  const lines: ActivityLine[] = [];

  for (let i = 0; i < block.events.length; i++) {
    const evt = block.events[i];
    const key = evt._uid || `${evt.type}-${evt.timestamp}-${i}`;

    if (evt.type === 'thought') {
      const text = thoughtText(evt);
      if (!text.trim()) continue;
      const { primary, secondary } = thoughtLabel(text);
      lines.push({ key, kind: 'thought', primary, secondary, detail: text });
      continue;
    }

    if (evt.type === 'tool_call') {
      const running = !evt.result && !block.completed;
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
          primary = `Read ${fileEdit.fileName}`;
          secondary = fileEdit.lineRange || '';
        } else if (fileEdit.kind === 'write') {
          primary = `Wrote ${fileEdit.fileName}`;
        } else {
          primary = `Edited ${fileEdit.fileName}`;
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
      continue;
    }

    if (evt.type === 'tool_result') {
      // Usually merged into tool_call; orphan results still show
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
      continue;
    }

    if (evt.type === 'info') {
      const text =
        typeof evt.content === 'string'
          ? evt.content
          : evt.content?.text || '';
      if (!text) continue;
      lines.push({
        key,
        kind: 'info',
        primary: text.length > 60 ? `${text.slice(0, 60)}…` : text,
        secondary: '',
        detail: text,
      });
    }
  }

  return lines;
}

function outerSummary(
  block: WorkflowBlock,
  lines: ActivityLine[],
  turnStartedMs?: number,
): { primary: string; secondary: string } {
  const running = !block.completed || lines.some((l) => l.running);
  let elapsedMs: number | null =
    typeof block.elapsed_ms === 'number' ? block.elapsed_ms : null;
  if (elapsedMs == null && turnStartedMs != null) {
    elapsedMs = Math.max(0, Date.now() - turnStartedMs);
  }
  const secs = elapsedMs != null ? Math.max(1, Math.round(elapsedMs / 1000)) : null;
  const thoughts = lines.filter((l) => l.kind === 'thought').length;
  const tools = lines.filter((l) => l.kind === 'tool').length;

  if (running) {
    if (secs != null) return { primary: `Working for ${secs}s`, secondary: '' };
    return { primary: 'Working', secondary: '' };
  }
  if (tools > 0 && secs != null) return { primary: `Worked for ${secs}s`, secondary: '' };
  if (tools > 0) return { primary: 'Worked', secondary: '' };
  if (thoughts > 0) {
    if (secs != null && secs >= 2) return { primary: 'Thought', secondary: `for ${secs}s` };
    return { primary: 'Thought', secondary: 'for a bit' };
  }
  return { primary: 'Activity', secondary: '' };
}

const TextChevronToggle: React.FC<{
  primary: string;
  secondary?: string;
  open: boolean;
  onToggle: () => void;
  running?: boolean;
  muted?: boolean;
  addedLines?: number;
  removedLines?: number;
}> = ({ primary, secondary, open, onToggle, running, muted, addedLines, removedLines }) => (
  <button
    type="button"
    onClick={onToggle}
    className="group inline-flex items-baseline gap-1.5 py-0.5 text-left max-w-full bg-transparent border-0 p-0 cursor-pointer"
  >
    <span
      className={`text-[13px] leading-relaxed min-w-0 ${
        muted ? 'text-textMuted/70' : 'text-textMuted/85'
      }`}
    >
      <span className="font-normal">{primary}</span>
      {secondary ? <span className="text-textMuted/50"> {secondary}</span> : null}
      {running ? <span className="text-textMuted/40"> …</span> : null}
    </span>
    {(addedLines != null && addedLines > 0) && (
      <span className="text-[12px] font-mono font-semibold text-emerald-600/90 dark:text-emerald-400/90 shrink-0">
        +{addedLines}
      </span>
    )}
    {(removedLines != null && removedLines > 0) && (
      <span className="text-[12px] font-mono font-semibold text-red-500/90 dark:text-red-400/90 shrink-0">
        -{removedLines}
      </span>
    )}
    <span className="text-[13px] text-textMuted/55 font-normal leading-relaxed shrink-0">
      {open ? '⌄' : '>'}
    </span>
  </button>
);

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

const SoloEventLine: React.FC<{
  line: ActivityLine;
  defaultOpen?: boolean;
}> = ({ line, defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen);
  const isThought = line.kind === 'thought';
  const isFileEdit = !!(line.fileEdit && (line.fileEdit.kind === 'edit' || line.fileEdit.kind === 'write'));
  const isFileRead = line.fileEdit?.kind === 'read';

  useEffect(() => {
    if (defaultOpen) setOpen(true);
  }, [defaultOpen]);

  const added = line.fileEdit?.addedLines;
  const removed = line.fileEdit?.removedLines;

  return (
    <div
      className={`w-full select-text ${
        isThought && open ? 'rounded-sm bg-black/[0.03] dark:bg-white/[0.04] px-1.5 py-1 -mx-0.5' : ''
      }`}
    >
      <TextChevronToggle
        primary={line.primary}
        secondary={line.secondary}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        running={line.running}
        muted={!isThought}
        addedLines={isFileEdit ? added : undefined}
        removedLines={isFileEdit ? removed : undefined}
      />

      {open && isThought && line.detail && (
        <div className="pl-0 pr-1 pb-0.5 pt-0.5">
          <pre className="text-[12px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[320px] overflow-y-auto text-textMuted/45 dark:text-textMuted/40">
            {line.detail}
          </pre>
        </div>
      )}

      {open && isFileEdit && line.fileEdit && (
        <div className="mt-1 mb-1.5">
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
        <div className="mt-1 mb-1.5">
          <FileDiffBlock
            info={line.fileEdit}
            status={line.toolStatus || 'success'}
            resultContent={line.toolResult}
            embedded
          />
        </div>
      )}

      {open && line.kind === 'tool' && !isFileEdit && !isFileRead && (
        <SoloToolExpandPanel
          toolName={line.toolName || line.primary}
          args={line.toolArgs}
          result={line.toolResult || ''}
          running={line.running}
        />
      )}

      {open && line.kind === 'info' && line.detail && (
        <div className="mt-1 mb-1.5 rounded-md border border-border/40 bg-black/[0.03] px-2.5 py-2">
          <pre className="text-[12px] text-textMuted/70 whitespace-pre-wrap break-words font-sans m-0">
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
  const turnDone = block.completed && !hasRunning;

  const [outerOpen, setOuterOpen] = useState(() => expandDetails || !turnDone);

  useEffect(() => {
    if (expandDetails) {
      setOuterOpen(true);
      return;
    }
    if (turnDone) setOuterOpen(false);
    else setOuterOpen(true);
  }, [turnDone, expandDetails]);

  useEffect(() => {
    if (turnDone || (!hasRunning && turnStartedMs == null)) return;
    const t = setInterval(() => setTick((n) => n + 1), 400);
    return () => clearInterval(t);
  }, [turnDone, hasRunning, turnStartedMs]);

  const lines = useMemo(() => buildLines(block), [block, tick]);
  const summary = useMemo(
    () => outerSummary(block, lines, turnStartedMs),
    [block, lines, turnStartedMs, tick],
  );

  if (!lines.length) return null;

  return (
    <div className="my-1.5 w-full select-text">
      <TextChevronToggle
        primary={summary.primary}
        secondary={summary.secondary}
        open={outerOpen}
        onToggle={() => setOuterOpen((v) => !v)}
        running={!turnDone}
      />
      {outerOpen && (
        <div className="mt-0.5 space-y-0.5 pl-0">
          {lines.map((line) => (
            <SoloEventLine
              key={line.key}
              line={line}
              defaultOpen={expandDetails}
            />
          ))}
        </div>
      )}
    </div>
  );
};
