/**
 * Group flat workflow events into Cursor-style delegate bundles:
 * parent delegate_task + nested sub_agent children (hidden from main stream).
 */
import type { WorkflowEvent } from './aiChatTimeline';
import { extractToolResultText, isToolResultFailure } from './aiChatTimeline';

export interface DelegateBundle {
  id: string;
  parent: WorkflowEvent;
  /** Initial task / prompt shown in the delegate window */
  prompt: string;
  /** Short title for the fold + window header */
  label: string;
  children: WorkflowEvent[];
  /** Final sub-agent answer (from parent tool_result or sub_agent_result info) */
  finalResult: string;
  running: boolean;
  toolCount: number;
  /** Async submit job id when known (delegate_task_submit) */
  jobId?: string;
}

export type DisplayWorkflowItem =
  | { kind: 'event'; event: WorkflowEvent; key: string }
  | { kind: 'delegation'; bundle: DelegateBundle; key: string };

/** True parent delegate entrypoints — short, dotted, or Native FC `__` names. */
const DELEGATE_ENTRY_RE =
  /(?:^|[.__])(?:delegate_task(_submit)?|self_learn(?:[._]|__)+start_learn)$/i;
const DELEGATE_RESULT_RE =
  /(?:^|[.__])delegate_task(_result)?$/i;

function normalizeToolName(name: unknown): string {
  return typeof name === 'string' ? name.trim() : '';
}

export function isDelegateToolName(name: unknown): boolean {
  const n = normalizeToolName(name);
  if (!n) return false;
  // Exclude list/result helpers from "entry" detection
  if (/(?:^|[.__])delegate_task_(result|list)$/i.test(n)) return false;
  return DELEGATE_ENTRY_RE.test(n);
}

export function isDelegateResultToolName(name: unknown): boolean {
  const n = normalizeToolName(name);
  if (!n) return false;
  return DELEGATE_RESULT_RE.test(n) || /(?:^|[.__])delegate_task$/i.test(n);
}

export function isDelegateToolCall(evt: WorkflowEvent): boolean {
  if (evt.type !== 'tool_call' || evt.subAgent) return false;
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return isDelegateToolName(data.name || data.tool);
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

/** True when parent tool_result is only an async job ack, not the real answer. */
export function isAsyncSubmitAck(result: string | undefined | null): boolean {
  const raw = (result || '').trim();
  if (!raw) return false;
  try {
    const o = JSON.parse(raw);
    if (!o || typeof o !== 'object' || Array.isArray(o)) return false;
    const jobId = (o as any).job_id;
    if (!jobId) return false;
    const status = String((o as any).status || '').toLowerCase();
    if (status === 'done' || status === 'error' || status === 'not_found') return false;
    // Classic submit ack: status running/pending, or result still null
    if (status === 'running' || status === 'pending' || status === '') return true;
    if ((o as any).result == null && status !== 'done') return true;
    return false;
  } catch {
    return false;
  }
}

export function parseJobIdFromResult(result: string | undefined | null): string {
  const raw = (result || '').trim();
  if (!raw) return '';
  try {
    const o = JSON.parse(raw);
    if (o && typeof o === 'object' && (o as any).job_id) return String((o as any).job_id);
  } catch {
    /* ignore */
  }
  return '';
}

export function extractDelegatePrompt(evt: WorkflowEvent): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  const args = parseArgs(data.arguments ?? data.args ?? data.input) || {};
  const task = typeof args.task === 'string' ? args.task.trim() : '';
  const context = typeof args.context === 'string' ? args.context.trim() : '';
  if (task && context) return `${task}\n\n---\n\n${context}`;
  if (task) return task;
  if (context) return context;
  if (evt.subTaskLabel) return evt.subTaskLabel;
  return 'Delegated sub-task';
}

export function extractDelegateLabel(evt: WorkflowEvent, prompt: string): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  const toolName = String(data.name || data.tool || '');
  if (/self_learn/i.test(toolName)) {
    if (evt.subTaskLabel && evt.subTaskLabel.trim()) {
      const t = evt.subTaskLabel.trim();
      return t.length > 72 ? `${t.slice(0, 72)}…` : t;
    }
    return 'Self-Learn';
  }
  if (evt.subTaskLabel && evt.subTaskLabel.trim()) {
    const t = evt.subTaskLabel.trim();
    return t.length > 72 ? `${t.slice(0, 72)}…` : t;
  }
  const firstLine = prompt.split(/\r?\n/).map((l) => l.trim()).find((l) => l.length > 0) || 'Delegate';
  return firstLine.length > 72 ? `${firstLine.slice(0, 72)}…` : firstLine;
}

function parentCallId(evt: WorkflowEvent): string | null {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return (data.id || data.tool_use_id || null) as string | null;
}

function isMatchingParentResult(evt: WorkflowEvent, callId: string | null): boolean {
  if (evt.type !== 'tool_result' || evt.subAgent) return false;
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  const resultId = data.id || data.tool_use_id || null;
  if (callId && resultId) return callId === resultId;
  const name = String(data.name || data.tool || '');
  return isDelegateToolName(name) || isDelegateResultToolName(name) || !name;
}

function extractSubAgentFinalFromChildren(children: WorkflowEvent[]): string {
  for (let i = children.length - 1; i >= 0; i--) {
    const evt = children[i];
    if (evt.type !== 'info') continue;
    const c = typeof evt.content === 'object' && evt.content ? (evt.content as Record<string, unknown>) : null;
    if (!c || c.event !== 'sub_agent_result') continue;
    if (typeof c.result === 'string' && c.result.trim()) return c.result.trim();
    if (typeof c.text === 'string' && c.text.trim()) return c.text.trim();
  }
  return '';
}

function bundleJobId(bundle: DelegateBundle): string {
  return (
    bundle.jobId ||
    bundle.parent.jobId ||
    parseJobIdFromResult(bundle.parent.result) ||
    ''
  );
}

function refreshBundleDerived(bundle: DelegateBundle): DelegateBundle {
  const fromChildren = extractSubAgentFinalFromChildren(bundle.children);
  const fromParent = (bundle.parent.result || '').trim();
  const submitAck = fromParent && isAsyncSubmitAck(fromParent);
  // Submit ack JSON is not the real answer — keep waiting for sub_agent_result
  // or a later non-ack parent result (e.g. delegate_task_result).
  const finalResult = fromChildren || (submitAck ? '' : fromParent);
  const jobId = bundleJobId(bundle) || undefined;
  return {
    ...bundle,
    jobId,
    finalResult,
    toolCount: bundle.children.filter((c) => c.type === 'tool_call' || c.type === 'tool_result').length,
    running: !finalResult && (submitAck || !fromParent),
  };
}

function labelsOverlap(a: string, b: string): boolean {
  const x = (a || '').trim();
  const y = (b || '').trim();
  if (!x || !y) return false;
  if (x === y) return true;
  const ax = x.slice(0, 40);
  const ay = y.slice(0, 40);
  return x.includes(ay) || y.includes(ax);
}

function findHostDelegation(
  out: DisplayWorkflowItem[],
  event: WorkflowEvent,
): Extract<DisplayWorkflowItem, { kind: 'delegation' }> | null {
  // Prefer job_id match (parallel async submits)
  if (event.jobId) {
    for (let k = out.length - 1; k >= 0; k--) {
      const item = out[k];
      if (item.kind !== 'delegation') continue;
      if (bundleJobId(item.bundle) === event.jobId) return item;
    }
  }
  // Then subTaskLabel vs prompt/label
  if (event.subTaskLabel) {
    for (let k = out.length - 1; k >= 0; k--) {
      const item = out[k];
      if (item.kind !== 'delegation') continue;
      if (
        labelsOverlap(event.subTaskLabel, item.bundle.label) ||
        labelsOverlap(event.subTaskLabel, item.bundle.prompt)
      ) {
        return item;
      }
    }
  }
  // Fallback: nearest preceding open (or any) delegation
  for (let k = out.length - 1; k >= 0; k--) {
    if (out[k].kind === 'delegation') {
      return out[k] as Extract<DisplayWorkflowItem, { kind: 'delegation' }>;
    }
  }
  return null;
}

/**
 * Build display items: nest sub_agent events under their parent delegate_task.
 *
 * While the parent delegate is still open (no result yet), untagged thought/info
 * events are also absorbed as children — sub-agent ChatAPI historically emitted
 * native reasoning without a sub_agent flag, which used to truncate the nest and
 * hide subsequent tagged tool_calls as orphans.
 *
 * Async submit (`delegate_task_submit`) returns a job ack immediately; the nest
 * stays open so later tagged sub-agent events (matched by job_id / label) still
 * appear in the delegate window like a sync delegate_task.
 */
export function buildDisplayWorkflowItems(events: WorkflowEvent[]): DisplayWorkflowItem[] {
  const items: DisplayWorkflowItem[] = [];
  let i = 0;

  while (i < events.length) {
    const evt = events[i];
    const key = evt._uid || `${evt.type}-${evt.timestamp}-${i}`;

    if (isDelegateToolCall(evt)) {
      const callId = parentCallId(evt);
      const children: WorkflowEvent[] = [];
      let parent: WorkflowEvent = { ...evt };
      let j = i + 1;

      while (j < events.length) {
        const next = events[j];
        const asyncOpen = !!(parent.result && isAsyncSubmitAck(parent.result));

        if (next.subAgent) {
          // When multiple async submits are in flight, only absorb events that
          // match this bundle's job / label; leave others for orphan attach.
          const thisJob = parent.jobId || parseJobIdFromResult(parent.result);
          if (thisJob && next.jobId && next.jobId !== thisJob) {
            break;
          }
          if (
            !thisJob &&
            next.subTaskLabel &&
            parent.subTaskLabel &&
            !labelsOverlap(next.subTaskLabel, parent.subTaskLabel) &&
            !labelsOverlap(next.subTaskLabel, extractDelegatePrompt(parent))
          ) {
            break;
          }
          children.push(next);
          j += 1;
          continue;
        }

        if (isMatchingParentResult(next, callId)) {
          const resStr = extractToolResultText(next.content);
          parent = {
            ...parent,
            result: resStr || parent.result,
            resultStatus:
              (typeof next.content === 'object' && next.content && (next.content as any).error) ||
              isToolResultFailure(resStr || parent.result)
                ? 'error'
                : 'success',
            jobId: parent.jobId || parseJobIdFromResult(resStr) || undefined,
          };
          j += 1;
          // Async ack: keep scanning for immediate subAgent children
          if (isAsyncSubmitAck(resStr || parent.result)) {
            continue;
          }
          break;
        }

        // Open nest: absorb untagged thought/info (sub ChatAPI leak) so we do
        // not truncate before the real sub tool_calls arrive.
        if (!parent.result && (next.type === 'thought' || next.type === 'info')) {
          children.push({ ...next, subAgent: true });
          j += 1;
          continue;
        }

        // Async submit still running: stop at next parent tool / narration
        if (asyncOpen) {
          if (next.type === 'tool_call') break;
          if (next.type === 'thought' || next.type === 'info') break;
          break;
        }

        // Parent result already merged onto the tool_call: keep collecting only
        // explicit subAgent events; stop at the next parent-level tool boundary.
        if (parent.result && next.type === 'tool_call') {
          break;
        }
        if (parent.result && (next.type === 'thought' || next.type === 'info')) {
          // Likely parent post-delegate narration — leave for main stream
          break;
        }

        break;
      }

      const prompt = extractDelegatePrompt(parent);
      const label = extractDelegateLabel(parent, prompt);
      const jobId = parent.jobId || parseJobIdFromResult(parent.result) || undefined;
      const bundle = refreshBundleDerived({
        id: callId || key,
        parent: jobId ? { ...parent, jobId } : parent,
        prompt,
        label,
        children,
        finalResult: '',
        running: !parent.result,
        toolCount: 0,
        jobId,
      });

      items.push({
        kind: 'delegation',
        key: `delegate-${bundle.id}`,
        bundle,
      });
      i = j;
      continue;
    }

    if (evt.type === 'tool_result' && !evt.subAgent) {
      const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
      if (isDelegateToolName(data.name || data.tool) || isDelegateResultToolName(data.name || data.tool)) {
        const last = items[items.length - 1];
        if (last?.kind === 'delegation' && last.bundle.parent.result) {
          i += 1;
          continue;
        }
      }
    }

    items.push({ kind: 'event', event: evt, key });
    i += 1;
  }

  // Attach orphan subAgent events to the best matching preceding delegation
  const out: DisplayWorkflowItem[] = [];
  for (const item of items) {
    if (item.kind === 'event' && item.event.subAgent) {
      const host = findHostDelegation(out, item.event);
      if (host) {
        host.bundle = refreshBundleDerived({
          ...host.bundle,
          children: [...host.bundle.children, item.event],
        });
        continue;
      }
    }
    out.push(item);
  }

  return out;
}
