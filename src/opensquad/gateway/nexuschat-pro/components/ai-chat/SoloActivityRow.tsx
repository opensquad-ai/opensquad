/**
 * SoloActivityRow — Cursor-style document-flow activity for Solo UI.
 *
 * Outer fold (turn-level): collapses the whole thought+tool process once the
 * agent finishes the turn (block.completed). Plain text + ">" / "⌄".
 * Inner lines: per-event thought/tool folds, also text-only (no cards).
 */
import React, { useEffect, useMemo, useState } from 'react';
import type { WorkflowBlock, WorkflowEvent } from '../../utils/aiChatTimeline';

interface SoloActivityRowProps {
  block: WorkflowBlock;
  /** When true, expand outer + inner details (Header lightbulb in Solo). */
  expandDetails?: boolean;
  turnStartedMs?: number;
}

function toolName(evt: WorkflowEvent): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return String(data.name || data.tool || 'Tool');
}

function thoughtText(evt: WorkflowEvent): string {
  return typeof evt.content === 'string' ? evt.content : JSON.stringify(evt.content ?? '');
}

function formatArgs(args: unknown): string {
  if (args == null) return '';
  if (typeof args === 'string') return args;
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
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
      const name = toolName(evt);
      const args = formatArgs(
        typeof evt.content === 'object' ? (evt.content.arguments || evt.content.args || evt.content.input) : '',
      );
      const result = formatResult(evt.result);
      const detailParts = [
        `Tool: ${name}`,
        args ? `Args:\n${args}` : '',
        result ? `Result:\n${result}` : running ? 'Running…' : '',
      ].filter(Boolean);
      // Inner lines: tool name only (turn duration lives on the outer fold)
      lines.push({
        key,
        kind: 'tool',
        primary: running ? 'Running' : name,
        secondary: running ? name : '',
        detail: detailParts.join('\n\n'),
        running,
      });
      continue;
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
        detail: result ? `Result:\n${result}` : '',
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
}> = ({ primary, secondary, open, onToggle, running, muted }) => (
  <button
    type="button"
    onClick={onToggle}
    className="group inline-flex items-baseline gap-1 py-0.5 text-left max-w-full bg-transparent border-0 p-0 cursor-pointer"
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
    <span className="text-[13px] text-textMuted/55 font-normal leading-relaxed shrink-0">
      {open ? '⌄' : '>'}
    </span>
  </button>
);

const SoloEventLine: React.FC<{
  line: ActivityLine;
  defaultOpen?: boolean;
}> = ({ line, defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen);
  const isThought = line.kind === 'thought';

  useEffect(() => {
    if (defaultOpen) setOpen(true);
  }, [defaultOpen]);

  return (
    <div className={`w-full select-text ${isThought && open ? 'rounded-sm bg-black/[0.03] dark:bg-white/[0.04] px-1.5 py-1 -mx-0.5' : ''}`}>
      <TextChevronToggle
        primary={line.primary}
        secondary={line.secondary}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        running={line.running}
        // Thought title stays readable; body below is intentionally washed out.
        muted={!isThought}
      />
      {open && line.detail && (
        <div className="pl-0 pr-1 pb-0.5 pt-0.5">
          <pre
            className={`text-[12px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[320px] overflow-y-auto ${
              isThought
                ? 'text-textMuted/45 dark:text-textMuted/40'
                : 'text-textMuted/70'
            }`}
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
  const turnDone = block.completed && !hasRunning;

  // Outer fold: open while working; collapse when the turn finishes (agent reply expected).
  const [outerOpen, setOuterOpen] = useState(() => expandDetails || !turnDone);

  useEffect(() => {
    if (expandDetails) {
      setOuterOpen(true);
      return;
    }
    // Auto-collapse when the workflow completes (task done → hide process).
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

  // Single-line turns: still use outer fold so completed turns stay compact.
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
