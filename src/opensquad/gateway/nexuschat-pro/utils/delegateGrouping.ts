/**
 * Group flat workflow events into Cursor-style delegate bundles:
 * parent delegate_task + nested sub_agent children (hidden from main stream).
 */
import type { WorkflowEvent } from './aiChatTimeline';
import { extractToolResultText } from './aiChatTimeline';

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
}

export type DisplayWorkflowItem =
  | { kind: 'event'; event: WorkflowEvent; key: string }
  | { kind: 'delegation'; bundle: DelegateBundle; key: string };

const DELEGATE_NAME_RE = /delegate_task/i;

export function isDelegateToolName(name: unknown): boolean {
  if (typeof name !== 'string') return false;
  return DELEGATE_NAME_RE.test(name);
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
  return isDelegateToolName(name) || !name;
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

function refreshBundleDerived(bundle: DelegateBundle): DelegateBundle {
  const fromChildren = extractSubAgentFinalFromChildren(bundle.children);
  const fromParent = (bundle.parent.result || '').trim();
  const finalResult = fromChildren || fromParent;
  return {
    ...bundle,
    finalResult,
    toolCount: bundle.children.filter((c) => c.type === 'tool_call' || c.type === 'tool_result').length,
    running: !(bundle.parent.result || fromChildren),
  };
}

/**
 * Build display items: nest sub_agent events under their parent delegate_task.
 *
 * While the parent delegate is still open (no result yet), untagged thought/info
 * events are also absorbed as children — sub-agent ChatAPI historically emitted
 * native reasoning without a sub_agent flag, which used to truncate the nest and
 * hide subsequent tagged tool_calls as orphans.
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

        if (next.subAgent) {
          children.push(next);
          j += 1;
          continue;
        }

        if (isMatchingParentResult(next, callId)) {
          const resStr = extractToolResultText(next.content);
          parent = {
            ...parent,
            result: resStr || parent.result,
            resultStatus: (typeof next.content === 'object' && next.content && (next.content as any).error)
              ? 'error'
              : 'success',
          };
          j += 1;
          break;
        }

        // Open nest: absorb untagged thought/info (sub ChatAPI leak) so we do
        // not truncate before the real sub tool_calls arrive.
        if (!parent.result && (next.type === 'thought' || next.type === 'info')) {
          children.push({ ...next, subAgent: true });
          j += 1;
          continue;
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
      const bundle = refreshBundleDerived({
        id: callId || key,
        parent,
        prompt,
        label,
        children,
        finalResult: '',
        running: !parent.result,
        toolCount: 0,
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
      if (isDelegateToolName(data.name || data.tool)) {
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

  // Attach orphan subAgent events to the nearest preceding delegation
  const out: DisplayWorkflowItem[] = [];
  for (const item of items) {
    if (item.kind === 'event' && item.event.subAgent) {
      let host: Extract<DisplayWorkflowItem, { kind: 'delegation' }> | null = null;
      for (let k = out.length - 1; k >= 0; k--) {
        if (out[k].kind === 'delegation') {
          host = out[k] as Extract<DisplayWorkflowItem, { kind: 'delegation' }>;
          break;
        }
      }
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
