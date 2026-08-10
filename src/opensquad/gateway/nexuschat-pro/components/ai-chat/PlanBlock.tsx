/**
 * PlanBlock - workflow-style task plan (shared by timeline + composer dock).
 *
 * Header: Plan · done/total · chevron
 * Steps: green check (done+strike) · primary arrow (running) · dashed circle (pending)
 */
import React, { useEffect, useState } from 'react';
import {
  ListTodo,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  ArrowRightCircle,
  CheckCircle2,
  XCircle,
} from 'lucide-react';

export interface PlanStep {
  content: string;
  status: 'pending' | 'running' | 'done' | 'failed';
}

interface PlanBlockProps {
  steps: PlanStep[];
  title?: string;
  className?: string;
  /** Keep expanded while a step is running */
  defaultOpen?: boolean;
}

const StepIcon: React.FC<{ status: PlanStep['status'] }> = ({ status }) => {
  const muted = 'color-mix(in srgb, rgb(var(--color-text-muted)) 55%, transparent)';
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

export const PlanBlock: React.FC<PlanBlockProps> = ({
  steps,
  title,
  className,
  defaultOpen = true,
}) => {
  const hasRunning = steps.some((s) => s.status === 'running');
  const [isOpen, setIsOpen] = useState(defaultOpen || hasRunning);

  useEffect(() => {
    if (defaultOpen || hasRunning) setIsOpen(true);
  }, [defaultOpen, hasRunning]);

  if (!steps || steps.length === 0) return null;

  const doneCount = steps.filter((s) => s.status === 'done').length;
  const total = steps.length;

  return (
    <div
      className={
        className ||
        'mb-3 border border-border/55 rounded-lg overflow-hidden bg-bgLight'
      }
    >
      <button
        type="button"
        className="w-full flex items-center gap-2 px-3.5 py-2 cursor-pointer hover:bg-primary/10 transition-colors select-none text-left bg-transparent border-0"
        onClick={() => setIsOpen(!isOpen)}
      >
        <ListTodo size={14} className="text-primary/85 flex-shrink-0" />
        <span className="text-[13px] text-textMain font-medium flex-1 truncate">
          {title || 'Plan'}
        </span>
        <span className="text-[11px] text-textMuted tabular-nums">
          {doneCount}/{total}
        </span>
        {isOpen ? (
          <ChevronDown size={14} className="text-textMuted flex-shrink-0" />
        ) : (
          <ChevronRight size={14} className="text-textMuted flex-shrink-0" />
        )}
      </button>

      {isOpen && (
        <div className="border-t border-border/50 px-3.5 py-2 space-y-1.5">
          {steps.map((step, i) => (
            <div key={i} className="flex items-start gap-2">
              <StepIcon status={step.status} />
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
                    ? { color: 'color-mix(in srgb, rgb(var(--color-text-muted)) 70%, transparent)' }
                    : undefined
                }
              >
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
      } else if (
        /\[running\]/i.test(line) ||
        /\[in.?progress\]/i.test(line) ||
        /\[current\]/i.test(line) ||
        /\[>\]/.test(line)
      ) {
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
