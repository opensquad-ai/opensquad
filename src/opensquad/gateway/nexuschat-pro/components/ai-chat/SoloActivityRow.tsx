/**
 * SoloActivityRow — compact collapsed workflow summary for Solo UI mode.
 * Default: one-line summary (thoughts / tools). Expand to show ThoughtBlock / ToolCallBlock.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Loader2, Wrench, Brain } from 'lucide-react';
import { ThoughtBlock } from './ThoughtBlock';
import { ToolCallBlock } from './ToolCallBlock';
import type { WorkflowBlock, WorkflowEvent } from '../../utils/aiChatTimeline';

interface SoloActivityRowProps {
  block: WorkflowBlock;
  /** When true, expand details by default (Header "workflow details" toggle in Solo). */
  expandDetails?: boolean;
  turnStartedMs?: number;
}

function toolName(evt: WorkflowEvent): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return data.name || data.tool || 'Tool';
}

function summarize(events: WorkflowEvent[]): { label: string; running: boolean } {
  const thoughts = events.filter((e) => e.type === 'thought').length;
  const tools = events.filter((e) => e.type === 'tool_call' || e.type === 'tool_result');
  const running = tools.some(
    (e) => e.type === 'tool_call' && !e.result,
  );
  const names = tools
    .filter((e) => e.type === 'tool_call')
    .map(toolName)
    .filter((n, i, arr) => arr.indexOf(n) === i);
  const parts: string[] = [];
  if (thoughts > 0) parts.push(thoughts === 1 ? 'Thinking' : `Thinking ×${thoughts}`);
  if (names.length > 0) {
    const shown = names.slice(0, 3).join(', ');
    parts.push(names.length > 3 ? `${shown} +${names.length - 3}` : shown);
  }
  if (parts.length === 0) {
    const infos = events.filter((e) => e.type === 'info' || e.type === 'plan' || e.type === 'summary_stream');
    if (infos.length > 0) parts.push(`${infos.length} update${infos.length > 1 ? 's' : ''}`);
  }
  return {
    label: parts.length > 0 ? parts.join(' · ') : 'Activity',
    running,
  };
}

export const SoloActivityRow: React.FC<SoloActivityRowProps> = ({
  block,
  expandDetails = false,
  turnStartedMs,
}) => {
  const [open, setOpen] = useState(expandDetails || !block.completed);
  const { label, running } = useMemo(() => summarize(block.events), [block.events]);

  useEffect(() => {
    if (expandDetails) setOpen(true);
  }, [expandDetails]);

  // Auto-expand while the turn is still running (unless user collapsed and expandDetails is false).
  useEffect(() => {
    if (!block.completed && (expandDetails || running)) {
      setOpen(true);
    }
  }, [block.completed, running, expandDetails]);

  // Force re-render for live elapsed
  const [, setTick] = useState(0);
  useEffect(() => {
    if (block.completed || turnStartedMs == null) return;
    const t = setInterval(() => setTick((n) => n + 1), 200);
    return () => clearInterval(t);
  }, [block.completed, turnStartedMs]);

  if (!block.events.length) return null;

  const elapsedLabel =
    !block.completed && turnStartedMs != null
      ? `${((Date.now() - turnStartedMs) / 1000).toFixed(1)}s`
      : block.completed && block.elapsed_ms != null
        ? `${(block.elapsed_ms / 1000).toFixed(1)}s`
        : null;

  return (
    <div className="my-2 w-full">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-1 py-1.5 text-left rounded-md hover:bg-primary/5 transition-colors group"
      >
        {open ? (
          <ChevronDown size={14} className="text-textMuted flex-shrink-0" />
        ) : (
          <ChevronRight size={14} className="text-textMuted flex-shrink-0" />
        )}
        {running && !block.completed ? (
          <Loader2 size={13} className="text-primary animate-spin flex-shrink-0" />
        ) : label.startsWith('Thinking') ? (
          <Brain size={13} className="text-textMuted flex-shrink-0" />
        ) : (
          <Wrench size={13} className="text-textMuted flex-shrink-0" />
        )}
        <span className="text-xs text-textMuted truncate flex-1 font-mono">
          {label}
          {block.status && !block.completed ? ` — ${block.status}` : ''}
        </span>
        {elapsedLabel && (
          <span className="text-[10px] text-textMuted/70 font-mono flex-shrink-0">
            {elapsedLabel}
          </span>
        )}
      </button>

      {open && (
        <div className="ml-5 pl-2 border-l border-border/60 space-y-1.5 pb-1">
          {block.events.map((evt, i) => {
            const eventKey = evt._uid || `${evt.type}-${evt.timestamp}-${i}`;
            if (evt.type === 'thought') {
              return (
                <ThoughtBlock
                  key={eventKey}
                  content={typeof evt.content === 'string' ? evt.content : JSON.stringify(evt.content)}
                  defaultOpen={false}
                />
              );
            }
            if (evt.type === 'tool_call') {
              const data = typeof evt.content === 'object' ? evt.content : {};
              const status = evt.result
                ? evt.resultStatus === 'error'
                  ? 'error'
                  : 'success'
                : 'running';
              return (
                <ToolCallBlock
                  key={eventKey}
                  persistKey={eventKey}
                  toolName={data.name || data.tool || 'Tool'}
                  args={data.arguments || data.args || data.input}
                  result={evt.result}
                  status={status}
                  subAgent={evt.subAgent}
                  subTaskLabel={evt.subTaskLabel}
                />
              );
            }
            if (evt.type === 'tool_result') {
              const data = typeof evt.content === 'object' ? evt.content : {};
              return (
                <ToolCallBlock
                  key={eventKey}
                  persistKey={eventKey}
                  toolName={data.name || data.tool || 'Tool'}
                  result={
                    typeof data.result === 'string'
                      ? data.result
                      : data.output || JSON.stringify(data)
                  }
                  status={data.error ? 'error' : 'success'}
                  subAgent={evt.subAgent}
                  subTaskLabel={evt.subTaskLabel}
                />
              );
            }
            if (evt.type === 'info') {
              const text =
                typeof evt.content === 'string'
                  ? evt.content
                  : evt.content?.text || JSON.stringify(evt.content);
              if (!text) return null;
              return (
                <div key={eventKey} className="text-[11px] text-textMuted px-1 py-0.5">
                  {text}
                </div>
              );
            }
            return null;
          })}
        </div>
      )}
    </div>
  );
};
