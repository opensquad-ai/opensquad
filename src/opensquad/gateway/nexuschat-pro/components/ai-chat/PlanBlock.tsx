/**
 * PlanBlock - displays AI task plan with step status indicators.
 *
 * Each step can be: pending, running, done, or failed.
 * Steps are parsed from the plan content.
 */
import React, { useState } from 'react';
import { ListChecks, ChevronDown, ChevronRight, Circle, Loader2, CheckCircle2, XCircle } from 'lucide-react';

export interface PlanStep {
  content: string;
  status: 'pending' | 'running' | 'done' | 'failed';
}

interface PlanBlockProps {
  steps: PlanStep[];
  title?: string;
  className?: string;
}

const StepIcon: React.FC<{ status: PlanStep['status'] }> = ({ status }) => {
  switch (status) {
    case 'done':
      return <CheckCircle2 size={12} className="text-emerald-500 flex-shrink-0" />;
    case 'running':
      return <Loader2 size={12} className="text-primary animate-spin flex-shrink-0" />;
    case 'failed':
      return <XCircle size={12} className="text-red-500 flex-shrink-0" />;
    default:
      return <Circle size={12} className="text-textMuted flex-shrink-0" />;
  }
};

export const PlanBlock: React.FC<PlanBlockProps> = ({ steps, title, className }) => {
  const [isOpen, setIsOpen] = useState(true);

  if (!steps || steps.length === 0) return null;

  const doneCount = steps.filter(s => s.status === 'done').length;
  const total = steps.length;

  return (
    <div className={className || "mb-3 ml-9 border border-border rounded-lg overflow-hidden bg-panel/50"}>
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-bgLight/50 transition-colors select-none"
        onClick={() => setIsOpen(!isOpen)}
      >
        <ListChecks size={14} className="text-primary" />
        <span className="text-xs text-textMain font-medium flex-1 truncate">
          {title || 'Plan'}
        </span>
        <span className="text-[10px] text-textMuted">{doneCount}/{total}</span>
        {isOpen
          ? <ChevronDown size={14} className="text-textMuted" />
          : <ChevronRight size={14} className="text-textMuted" />
        }
      </div>

      {/* Steps */}
      {isOpen && (
        <div className="border-t border-border px-3 py-2 space-y-1.5">
          {steps.map((step, i) => (
            <div key={i} className="flex items-start gap-2">
              <StepIcon status={step.status} />
              <span className={`text-[11px] leading-tight ${
                step.status === 'done' ? 'text-textMuted line-through' :
                step.status === 'running' ? 'text-textMain font-medium' :
                step.status === 'failed' ? 'text-red-500' :
                'text-textMuted'
              }`}>
                {step.content}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

/**
 * Parse a plan content string (from Gateway) into PlanStep array.
 * Expected format: lines like "- [x] step" or "1. [running] step" etc.
 * Falls back to treating each line as a pending step.
 */
export function parsePlanContent(content: any): PlanStep[] {
  if (!content) return [];

  // If content is already an array of steps
  if (Array.isArray(content)) {
    return content
      .map((item: any) => {
        if (typeof item === 'string') {
          return { content: sanitizePlanStepText(item), status: 'pending' as const };
        }
        return {
          content: sanitizePlanStepText(item.content || item.text || item.step || String(item)),
          status: (item.status as PlanStep['status']) || 'pending',
        };
      })
      .filter((s) => s.content.length > 0);
  }

  // String content: isolate real <plan> body, drop leaked think fragments.
  let text = typeof content === 'string' ? content : JSON.stringify(content);
  text = isolatePlanBody(text);
  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);

  return lines
    .map((line: string) => {
      const trimmed = line.replace(/^[-*\d.)\s]+/, '').trim();
      let status: PlanStep['status'] = 'pending';

      if (/\[x\]/i.test(line) || /\[done\]/i.test(line) || /\[completed\]/i.test(line)) {
        status = 'done';
      } else if (/\[running\]/i.test(line) || /\[in.?progress\]/i.test(line) || /\[current\]/i.test(line) || /\[>\]/.test(line)) {
        status = 'running';
      } else if (/\[failed\]/i.test(line) || /\[error\]/i.test(line)) {
        status = 'failed';
      }

      const cleaned = sanitizePlanStepText(
        trimmed
          .replace(/\[(x|done|completed|running|in.?progress|current|failed|error|>|\s)\]/gi, '')
          .trim(),
      );

      return { content: cleaned, status };
    })
    .filter((s) => s.content.length > 0);
}

/** Prefer the real <plan>...</plan> body; strip think/thought wrappers. */
function isolatePlanBody(raw: string): string {
  let text = raw
    .replace(/<\/?think\b[^>]*>/gi, '\n')
    .replace(/<\/?thought\b[^>]*>/gi, '\n');

  const openIdx = text.toLowerCase().lastIndexOf('<plan');
  if (openIdx >= 0) {
    const after = text.slice(openIdx);
    const m = after.match(/<plan\b[^>]*>([\s\S]*?)(?:<\/plan\s*>|$)/i);
    if (m) text = m[1];
  } else {
    text = text.replace(/<\/?plan\b[^>]*>/gi, '');
  }
  return text;
}

function sanitizePlanStepText(value: string): string {
  return String(value || '')
    .replace(/<\/?think\b[^>]*>/gi, '')
    .replace(/<\/?thought\b[^>]*>/gi, '')
    .replace(/<\/?plan\b[^>]*>/gi, '')
    .replace(/^`+|`+$/g, '')
    .trim();
}
