/**
 * SoloActivityRow — Cursor-style document-flow activity lines for Solo UI.
 *
 * Collapsed: plain text + ">" (no borders / cards / icons).
 * Expanded: plain indented detail text (still no fold boxes).
 */
import React, { useEffect, useMemo, useState } from 'react';
import type { WorkflowBlock, WorkflowEvent } from '../../utils/aiChatTimeline';

interface SoloActivityRowProps {
  block: WorkflowBlock;
  /** When true, expand all event lines by default (Header lightbulb in Solo). */
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

function toolWorkLabel(
  evt: WorkflowEvent,
  elapsedMs?: number | null,
  running?: boolean,
): { primary: string; secondary: string } {
  const name = toolName(evt);
  if (running) return { primary: 'Working', secondary: name };
  if (elapsedMs != null && elapsedMs > 0) {
    const secs = Math.max(1, Math.round(elapsedMs / 1000));
    return { primary: `Worked for ${secs}s`, secondary: name };
  }
  return { primary: 'Worked', secondary: name };
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

function buildLines(block: WorkflowBlock, turnStartedMs?: number): ActivityLine[] {
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
      const started = typeof evt.timestamp === 'number' ? evt.timestamp : undefined;
      let elapsed: number | null = null;
      if (evt.result && started != null) {
        // Approximate: we don't store end ts on the event; use block elapsed when available
        elapsed = block.elapsed_ms ?? Math.max(0, Date.now() - started);
      } else if (running && started != null) {
        elapsed = Math.max(0, Date.now() - started);
      } else if (running && turnStartedMs != null) {
        elapsed = Math.max(0, Date.now() - turnStartedMs);
      } else if (block.elapsed_ms != null) {
        elapsed = block.elapsed_ms;
      }
      const { primary, secondary } = toolWorkLabel(evt, elapsed, running);
      const args = formatArgs(
        typeof evt.content === 'object' ? (evt.content.arguments || evt.content.args || evt.content.input) : '',
      );
      const result = formatResult(evt.result);
      const detailParts = [
        secondary ? `Tool: ${secondary}` : '',
        args ? `Args:\n${args}` : '',
        result ? `Result:\n${result}` : running ? 'Running…' : '',
      ].filter(Boolean);
      lines.push({
        key,
        kind: 'tool',
        primary,
        secondary: running ? secondary : '',
        detail: detailParts.join('\n\n'),
        running,
      });
      continue;
    }

    if (evt.type === 'tool_result') {
      // Usually merged into tool_call; orphan results still show as a line
      const data = typeof evt.content === 'object' ? evt.content : {};
      const name = data.name || data.tool || 'Tool';
      const result = formatResult(data.result ?? data.output ?? data);
      lines.push({
        key,
        kind: 'tool',
        primary: 'Worked',
        secondary: String(name),
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

const SoloEventLine: React.FC<{
  line: ActivityLine;
  defaultOpen?: boolean;
}> = ({ line, defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    if (defaultOpen) setOpen(true);
  }, [defaultOpen]);

  return (
    <div className="w-full select-text">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group flex items-baseline gap-1.5 py-0.5 text-left w-full max-w-full bg-transparent border-0 p-0 cursor-pointer"
      >
        <span className="text-[13px] text-textMuted/80 font-normal leading-relaxed shrink-0 w-3 text-center">
          {open ? '⌄' : '>'}
        </span>
        <span className="text-[13px] leading-relaxed text-textMain/80 min-w-0">
          <span className="font-normal">{line.primary}</span>
          {line.secondary ? (
            <span className="text-textMuted/55"> {line.secondary}</span>
          ) : null}
          {line.running ? (
            <span className="text-textMuted/45"> …</span>
          ) : null}
        </span>
      </button>
      {open && line.detail && (
        <div className="pl-4 pr-1 pb-1.5 pt-0.5">
          <pre className="text-[12px] leading-relaxed text-textMuted/75 whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[320px] overflow-y-auto">
            {line.detail}
          </pre>
        </div>
      )}
    </div>
  );
};

export const SoloActivityRow: React.FC<SoloActivityRowProps> = ({
  block,
  expandDetails = false,
  turnStartedMs,
}) => {
  const [tick, setTick] = useState(0);
  const hasRunning = block.events.some(
    (e) => e.type === 'tool_call' && !e.result && !block.completed,
  );
  useEffect(() => {
    if (block.completed || (!hasRunning && turnStartedMs == null)) return;
    const t = setInterval(() => setTick((n) => n + 1), 400);
    return () => clearInterval(t);
  }, [block.completed, hasRunning, turnStartedMs]);

  const lines = useMemo(
    () => buildLines(block, turnStartedMs),
    [block, turnStartedMs, tick],
  );

  if (!lines.length) return null;

  return (
    <div className="my-1 w-full space-y-0.5">
      {lines.map((line) => (
        <SoloEventLine
          key={line.key}
          line={line}
          defaultOpen={expandDetails}
        />
      ))}
    </div>
  );
};
