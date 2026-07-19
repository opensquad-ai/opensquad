/**
 * Pure timeline helpers extracted from AIChatPage for unit testing.
 */
import type { ChatMessage, FileAttachment } from '../components/ai-chat/MessageBubble';
import { parsePlanContent } from '../components/ai-chat/PlanBlock';

export function genTimelineUID(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
  }
}

/**
 * Collapse skill payloads for chat display.
 * - `<user_send_skill>name</user_send_skill>` → `/name …`
 * - Expanded SKILL.md bodies (BEGIN/END SKILL) → `/name` + user request only
 */
export function formatUserSkillDisplayContent(content: string): string {
  if (!content || typeof content !== 'string') return content;

  // Live voice turns: unwrap <realtime_voice> for chat display (agent still sees the tag).
  const voiceRe = /<realtime_voice(?:\s[^>]*)?>\s*([\s\S]*?)\s*<\/realtime_voice>/i;
  const voiceMatch = content.match(voiceRe);
  if (voiceMatch) {
    content = (voiceMatch[1] || '').trim() || content;
  }
  // Legacy VoiceAsk preamble (older sessions)
  const legacyAsk = content.match(
    /^\[VoiceAsk:[^\]]+\][\s\S]*?\nUser said:\n([\s\S]+)$/i,
  );
  if (legacyAsk) {
    content = (legacyAsk[1] || '').trim() || content;
  }

  const tagRe = /<user_send_skill>\s*([^<]+?)\s*<\/user_send_skill>/i;
  const tagMatch = content.match(tagRe);
  if (tagMatch) {
    const name = (tagMatch[1] || '').trim();
    const rest = content.replace(tagRe, '').trim();
    if (!name) return rest || content;
    return rest ? `/${name} ${rest}` : `/${name}`;
  }

  const looksExpanded =
    /----- BEGIN SKILL -----/i.test(content) ||
    /\[User-selected skill:/i.test(content);
  if (!looksExpanded) return content;

  let skillName = '';
  const tickName = content.match(/\[User-selected skill:[^\]]*?\(`([^`]+)`\)/i);
  if (tickName) {
    skillName = (tickName[1] || '').trim();
  } else {
    const plain = content.match(/\[User-selected skill:\s*([^\]]+?)\]/i);
    if (plain) skillName = (plain[1] || '').trim().replace(/`/g, '');
  }

  let userReq = '';
  const reqMatch = content.match(/\[User request\]\s*([\s\S]*)$/i);
  if (reqMatch) {
    userReq = (reqMatch[1] || '').trim();
    if (/^\(Apply the .+ skill\.\)$/i.test(userReq)) userReq = '';
  }

  if (skillName) {
    return userReq ? `/${skillName} ${userReq}` : `/${skillName}`;
  }

  const stripped = content
    .replace(/----- BEGIN SKILL -----[\s\S]*?----- END SKILL -----/gi, '')
    .replace(/\[User-selected skill:[^\]]*\]/gi, '')
    .replace(/Follow the skill instructions below[^\n]*/gi, '')
    .replace(/\[User request\]/gi, '')
    .trim();
  return stripped || '/skill';
}

export interface WorkflowEvent {
  _uid?: string;
  type: 'thought' | 'tool_call' | 'tool_result' | 'info' | 'plan' | 'summary_stream' | 'compression_progress';
  content: any;
  timestamp: number;
  result?: any;
  resultStatus?: 'success' | 'error';
  /** Expanded file-edit snippets (±context) from replace_in_file for UI diffs. */
  diffOld?: string;
  diffNew?: string;
  diffStartLine?: number;
  subAgent?: boolean;
  subTaskLabel?: string;
  /** Async delegate_task_submit job id (nests parallel sub-agents). */
  jobId?: string;
}

/** Scope key so parent / sub-agent / job thoughts do not merge across each other. */
export function thoughtScopeKey(e: Pick<WorkflowEvent, 'subAgent' | 'subTaskLabel' | 'jobId'>): string {
  return `${e.subAgent ? 1 : 0}\0${e.subTaskLabel || ''}\0${e.jobId || ''}`;
}

export interface WorkflowBlock {
  events: WorkflowEvent[];
  status: string | null;
  completed: boolean;
  started_ms?: number;
  elapsed_ms?: number;
}

/** Parent-level async/sync delegate entry tool (not nested under a sub-agent). */
function isParentDelegateToolCall(evt: WorkflowEvent): boolean {
  if (evt.type !== 'tool_call' || evt.subAgent) return false;
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  const n = String(data.name || data.tool || '').trim();
  if (!n) return false;
  if (/(?:^|[.__])delegate_task_(result|list)$/i.test(n)) return false;
  return /(?:^|[.__])(?:delegate_task(_submit)?|self_learn(?:[._]|__)+start_learn)$/i.test(n);
}

function parseJobIdFromToolResult(result: unknown): string {
  const raw = typeof result === 'string' ? result.trim() : '';
  if (!raw) return '';
  try {
    const o = JSON.parse(raw);
    if (o && typeof o === 'object' && (o as any).job_id) return String((o as any).job_id);
  } catch {
    /* ignore */
  }
  return '';
}

/** True when parent tool_result is only an async job ack, not the real answer. */
function isAsyncDelegateAck(result: unknown): boolean {
  const raw = typeof result === 'string' ? result.trim() : '';
  if (!raw) return false;
  try {
    const o = JSON.parse(raw);
    if (!o || typeof o !== 'object' || Array.isArray(o)) return false;
    if (!(o as any).job_id) return false;
    const status = String((o as any).status || '').toLowerCase();
    if (status === 'done' || status === 'error' || status === 'not_found') return false;
    if (status === 'running' || status === 'pending' || status === '') return true;
    if ((o as any).result == null && status !== 'done') return true;
    return false;
  } catch {
    return false;
  }
}

function eventJobId(evt: Pick<WorkflowEvent, 'jobId' | 'result'>): string {
  return (evt.jobId || parseJobIdFromToolResult(evt.result) || '').trim();
}

function workflowHasSubAgentFinal(events: WorkflowEvent[], jobId: string): boolean {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type !== 'info') continue;
    const c = typeof e.content === 'object' && e.content ? (e.content as Record<string, unknown>) : null;
    if (!c || c.event !== 'sub_agent_result') continue;
    if (jobId && c.job_id != null && String(c.job_id) !== jobId) continue;
    return true;
  }
  return false;
}

/** True when a block still has an async delegate job without a final result. */
export function hasOpenAsyncDelegate(events: WorkflowEvent[]): boolean {
  for (const e of events) {
    if (!isParentDelegateToolCall(e)) continue;
    const jobId = eventJobId(e);
    if (!jobId) continue;
    if (!isAsyncDelegateAck(e.result)) continue;
    if (!workflowHasSubAgentFinal(events, jobId)) return true;
  }
  return false;
}

/**
 * Find the workflow that owns this sub-agent event (by job_id / open async
 * delegate). Searches completed blocks too — async jobs outlive the parent turn.
 */
function findAsyncDelegateHostIdx(timeline: TimelineEntry[], event: WorkflowEvent): number {
  if (!event.subAgent && !event.jobId) return -1;
  const wantJob = (event.jobId || '').trim();

  if (wantJob) {
    for (let wi = timeline.length - 1; wi >= 0; wi--) {
      if (timeline[wi].kind !== 'workflow') continue;
      const wf = (timeline[wi] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
      for (const evt of wf.events) {
        if (!isParentDelegateToolCall(evt)) continue;
        if (eventJobId(evt) === wantJob) return wi;
      }
      // Also match prior nested children already stamped with this job_id
      for (const evt of wf.events) {
        if (evt.subAgent && evt.jobId === wantJob) return wi;
      }
    }
  }

  // Label / nearest open async fallback (sync delegates or missing job_id)
  if (event.subAgent) {
    const label = (event.subTaskLabel || '').trim();
    for (let wi = timeline.length - 1; wi >= 0; wi--) {
      if (timeline[wi].kind !== 'workflow') continue;
      const wf = (timeline[wi] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
      for (let ei = wf.events.length - 1; ei >= 0; ei--) {
        const evt = wf.events[ei];
        if (!isParentDelegateToolCall(evt)) continue;
        const jobId = eventJobId(evt);
        if (jobId && workflowHasSubAgentFinal(wf.events, jobId)) continue;
        if (label && evt.subTaskLabel) {
          const a = label.slice(0, 40);
          const b = evt.subTaskLabel.trim().slice(0, 40);
          if (a && b && (label.includes(b) || evt.subTaskLabel.includes(a))) return wi;
        }
        if (!evt.result || isAsyncDelegateAck(evt.result)) return wi;
      }
    }
  }

  return -1;
}

function appendEventIntoWorkflowBlock(
  wf: WorkflowBlock,
  event: WorkflowEvent,
): WorkflowEvent[] {
  if (event.type === 'thought' && wf.events.length > 0) {
    const key = thoughtScopeKey(event);
    let mergeIdx = -1;
    for (let i = wf.events.length - 1; i >= 0; i--) {
      const e = wf.events[i];
      const eKey = thoughtScopeKey(e);
      if (e.type === 'thought' && eKey === key) {
        mergeIdx = i;
        break;
      }
      if (eKey === key && e.type !== 'thought') break;
    }
    if (mergeIdx >= 0) {
      const newEvents = [...wf.events];
      const prev = newEvents[mergeIdx];
      newEvents[mergeIdx] = {
        ...prev,
        content: String(prev.content ?? '') + String(event.content ?? ''),
      };
      return newEvents;
    }
    return [...wf.events, event];
  }

  if (event.type === 'tool_result') {
    const resultData = event.content;
    const resultId = typeof resultData === 'object' ? (resultData.id || resultData.tool_use_id) : null;
    const newEvents = [...wf.events];
    for (let i = newEvents.length - 1; i >= 0; i--) {
      const evt = newEvents[i];
      if (evt.type === 'tool_call' && !evt.result && !!evt.subAgent === !!event.subAgent) {
        const callData = typeof evt.content === 'object' ? evt.content : {};
        const callId = callData.id || callData.tool_use_id;
        if (!resultId || !callId || resultId === callId) {
          const ctx = extractDiffContext(resultData);
          newEvents[i] = {
            ...evt,
            result: extractToolResultText(resultData),
            resultStatus:
              (typeof resultData === 'object' && resultData && (resultData as any).error) ||
              isToolResultFailure(extractToolResultText(resultData)) ||
              isToolResultFailure(resultData)
                ? 'error'
                : 'success',
            ...(ctx.diffOld != null ? { diffOld: ctx.diffOld } : {}),
            ...(ctx.diffNew != null ? { diffNew: ctx.diffNew } : {}),
            ...(ctx.diffStartLine != null ? { diffStartLine: ctx.diffStartLine } : {}),
          };
          return newEvents;
        }
      }
    }
    return [...newEvents, event];
  }

  return [...wf.events, event];
}

/** True when nothing in the block is still in-flight (open tools / live summary). */
export function isWorkflowSettled(events: WorkflowEvent[]): boolean {
  for (const e of events) {
    if (e.type === 'tool_call' && !e.result) return false;
    if (e.type === 'summary_stream') {
      const data = typeof e.content === 'object' && e.content ? e.content : {};
      if (!data.done) return false;
    }
    if (e.type === 'compression_progress') {
      const data = typeof e.content === 'object' && e.content ? e.content : {};
      if (!data.is_final) return false;
    }
  }
  // Async delegate_task_submit returns an ack immediately but the sub-agent
  // keeps streaming — treat that as still in-flight for UI settlement.
  if (hasOpenAsyncDelegate(events)) return false;
  return true;
}

/**
 * Seal all incomplete workflow blocks (e.g. user inserts a new message mid-turn).
 * Stamps elapsed_ms so Solo shows "Worked for Xs" instead of stuck "Working".
 */
export function sealIncompleteWorkflows(
  prev: TimelineEntry[],
  opts?: { nowMs?: number; fallbackStartedMs?: number },
): TimelineEntry[] {
  const now = opts?.nowMs ?? Date.now();
  const fallbackStarted = opts?.fallbackStartedMs;
  return prev.map((entry) => {
    if (entry.kind !== 'workflow' || entry.data.completed) return entry;
    const wf = entry.data;
    const started =
      typeof wf.started_ms === 'number'
        ? wf.started_ms
        : typeof fallbackStarted === 'number'
          ? fallbackStarted
          : wf.events[0]?.timestamp;
    const elapsed =
      typeof wf.elapsed_ms === 'number'
        ? wf.elapsed_ms
        : typeof started === 'number'
          ? Math.max(0, now - started)
          : undefined;
    return {
      ...entry,
      data: {
        ...wf,
        completed: true,
        status: null,
        started_ms: typeof started === 'number' ? started : wf.started_ms,
        elapsed_ms: elapsed,
      },
    };
  });
}

/**
 * Whether a trailing / orphan workflow should render as finished even if
 * `completed` was never flipped by a following chat message.
 * Avoids thought-only live blocks (still streaming) being marked done.
 */
export function shouldTreatWorkflowComplete(block: WorkflowBlock): boolean {
  if (block.completed) return true;
  if (!isWorkflowSettled(block.events)) return false;

  let hasDoneSummary = false;
  let hasFinalProgress = false;
  let toolCalls = 0;
  let toolsWithResult = 0;
  for (const e of block.events) {
    if (e.type === 'summary_stream') {
      const data = typeof e.content === 'object' && e.content ? e.content : {};
      if (data.done) hasDoneSummary = true;
    } else if (e.type === 'compression_progress') {
      const data = typeof e.content === 'object' && e.content ? e.content : {};
      if (data.is_final) hasFinalProgress = true;
    } else if (e.type === 'tool_call') {
      toolCalls += 1;
      if (e.result) toolsWithResult += 1;
    }
  }
  if (hasDoneSummary || hasFinalProgress) return true;
  if (toolCalls > 0 && toolsWithResult === toolCalls) return true;
  return false;
}

/** Extract workflow start epoch-ms from a persisted "Workflow started" info event. */
export function workflowStartedMsFromRaw(raw: any): number | undefined {
  if (!raw || raw.type !== 'info') return undefined;
  const data = raw.data || {};
  const text = typeof data === 'string' ? data : (data.text || '');
  if (!/Workflow started/i.test(String(text))) return undefined;
  const ms = typeof data === 'object' && data !== null ? Number((data as any).started_ms) : NaN;
  return Number.isFinite(ms) ? ms : undefined;
}

export type TimelineEntry =
  | { kind: 'message'; data: ChatMessage; _uid: string }
  | { kind: 'workflow'; data: WorkflowBlock; _uid: string }
  | { kind: 'prompt'; data: { system_prompt: string; dynamic_prefix: string; changed: boolean; timestamp: string; diff?: string[] }; _uid: string }
  | { kind: 'status_hint'; data: { hintType: 'sleep' | 'wake' | 'state'; content: string | number; timestamp: number }; _uid: string }
  | { kind: 'archived_section'; data: {
      messageCount: number;
      eventCount: number;
      entries: TimelineEntry[];
      startTs?: string;
      endTs?: string;
    }; _uid: string }
  | { kind: 'task_fold'; data: {
      title?: string;
      messageCount: number;
      eventCount: number;
      entries: TimelineEntry[];
      collapsed: boolean;
    }; _uid: string };

/** Fold agent-side process between the latest user message and an end-task report. */
export function foldAroundEndTaskIndex(
  timeline: TimelineEntry[],
  endIdx: number,
  title?: string,
): TimelineEntry[] {
  if (endIdx <= 0 || endIdx >= timeline.length) return timeline;
  const endEntry = timeline[endIdx];
  if (endEntry.kind !== 'message' || endEntry.data.role !== 'assistant') return timeline;

  let userIdx = -1;
  for (let i = endIdx - 1; i >= 0; i -= 1) {
    const e = timeline[i];
    if (e.kind === 'message' && e.data.role === 'user') {
      userIdx = i;
      break;
    }
  }
  const foldStart = userIdx + 1;
  const foldEnd = endIdx;
  if (foldEnd <= foldStart) return timeline;

  const folded = timeline.slice(foldStart, foldEnd);
  if (folded.length === 0) return timeline;

  let messageCount = 0;
  let eventCount = 0;
  for (const e of folded) {
    if (e.kind === 'message') messageCount += 1;
    else if (e.kind === 'workflow') eventCount += e.data.events.length;
    else if (e.kind === 'task_fold') {
      messageCount += e.data.messageCount;
      eventCount += e.data.eventCount;
    } else eventCount += 1;
  }

  const foldEntry: TimelineEntry = {
    kind: 'task_fold',
    data: {
      title: title || undefined,
      messageCount,
      eventCount,
      entries: folded,
      collapsed: true,
    },
    _uid: genTimelineUID(),
  };

  return [...timeline.slice(0, foldStart), foldEntry, ...timeline.slice(endIdx)];
}

/** After an end-task assistant message is appended, fold the preceding agent process. */
export function foldTaskProcessSinceLastUser(
  timeline: TimelineEntry[],
  opts?: { title?: string },
): TimelineEntry[] {
  for (let i = timeline.length - 1; i >= 0; i -= 1) {
    const e = timeline[i];
    if (e.kind !== 'message') continue;
    if (e.data.role === 'user') return timeline;
    if (e.data.role === 'assistant') {
      return foldAroundEndTaskIndex(timeline, i, opts?.title);
    }
  }
  return timeline;
}

/** Rebuild folds for every persisted end_task assistant message (oldest → newest). */
export function applyEndTaskFolds(timeline: TimelineEntry[]): TimelineEntry[] {
  const endIndexes: number[] = [];
  for (let i = 0; i < timeline.length; i += 1) {
    const e = timeline[i];
    if (e.kind === 'message' && e.data.role === 'assistant' && e.data.end_task) {
      endIndexes.push(i);
    }
  }
  if (endIndexes.length === 0) return timeline;

  // Apply from the end so earlier indices stay stable relative to remaining suffix.
  let result = timeline;
  for (let k = endIndexes.length - 1; k >= 0; k -= 1) {
    // Re-find this end_task message in the current result (uids stable).
    const targetUid = timeline[endIndexes[k]]._uid;
    const idx = result.findIndex((e) => e._uid === targetUid);
    if (idx >= 0) {
      result = foldAroundEndTaskIndex(result, idx);
    }
  }
  return result;
}

export function workflowToolEventKey(evt: WorkflowEvent): string | null {
  if (evt.type !== 'tool_call' && evt.type !== 'tool_result') return null;
  const data = typeof evt.content === 'object' && evt.content ? evt.content : null;
  if (!data) return null;
  const id = data.id || data.tool_use_id;
  if (!id) return null;
  // tool_result merges into tool_call, so both share the call id namespace.
  return `tool:${id}`;
}

export function timelineHasToolEvent(timeline: TimelineEntry[], event: WorkflowEvent): boolean {
  const key = workflowToolEventKey(event);
  if (!key) return false;
  for (const entry of timeline) {
    if (entry.kind === 'archived_section') {
      if (timelineHasToolEvent(entry.data.entries, event)) return true;
      continue;
    }
    if (entry.kind === 'task_fold') {
      if (timelineHasToolEvent(entry.data.entries, event)) return true;
      continue;
    }
    if (entry.kind !== 'workflow') continue;
    for (const evt of entry.data.events) {
      if (workflowToolEventKey(evt) === key) return true;
    }
  }
  return false;
}

function toolCallNameOf(evt: WorkflowEvent): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return String(data.name || data.tool || '').trim();
}

function isPartialToolCall(evt: WorkflowEvent): boolean {
  if (evt.type !== 'tool_call') return false;
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return !!data.partial;
}

function findLastIncompleteWorkflowIdx(timeline: TimelineEntry[]): number {
  for (let i = timeline.length - 1; i >= 0; i--) {
    if (timeline[i].kind === 'prompt' || timeline[i].kind === 'status_hint') continue;
    if (
      timeline[i].kind === 'workflow' &&
      !(timeline[i] as Extract<TimelineEntry, { kind: 'workflow' }>).data.completed
    ) {
      return i;
    }
    break;
  }
  return -1;
}

/**
 * Upsert a streaming partial tool_call (Native FC arguments still arriving).
 * Matches by id / stream index within the active incomplete workflow.
 */
export function upsertPartialToolCall(
  prev: TimelineEntry[],
  event: WorkflowEvent,
  status: string | null,
): TimelineEntry[] {
  const data = typeof event.content === 'object' && event.content ? event.content : {};
  const callId = data.id != null ? String(data.id) : '';
  const streamIndex = data.index != null ? Number(data.index) : NaN;
  const toolName = String(data.name || data.tool || '').trim();

  const updated = [...prev];
  let targetIdx = findLastIncompleteWorkflowIdx(updated);
  if (targetIdx < 0) {
    updated.push({
      kind: 'workflow',
      data: { events: [{ ...event, _uid: genTimelineUID() }], status, completed: false },
      _uid: genTimelineUID(),
    });
    return updated;
  }

  const entry = updated[targetIdx] as Extract<TimelineEntry, { kind: 'workflow' }>;
  const wf = entry.data;
  const events = [...wf.events];
  let matchIdx = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    const evt = events[i];
    if (evt.type !== 'tool_call' || evt.result) continue;
    const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
    if (callId && String(c.id || '') === callId) {
      matchIdx = i;
      break;
    }
    if (
      Number.isFinite(streamIndex) &&
      c.partial &&
      Number(c.index) === streamIndex &&
      (!toolName || String(c.name || '') === toolName)
    ) {
      matchIdx = i;
      break;
    }
  }

  if (matchIdx >= 0) {
    const prevEvt = events[matchIdx];
    events[matchIdx] = {
      ...prevEvt,
      content: { ...(typeof prevEvt.content === 'object' ? prevEvt.content : {}), ...data, partial: true },
      timestamp: event.timestamp || prevEvt.timestamp,
    };
  } else {
    events.push({ ...event, _uid: genTimelineUID() });
  }

  updated[targetIdx] = {
    ...entry,
    data: { ...wf, events, status: status ?? wf.status, completed: false },
  };
  return updated;
}

/**
 * When the final tool_call arrives, replace a matching partial streaming card
 * (same tool name, still open) so we do not show a duplicate Writing row.
 */
export function promotePartialToolCall(
  prev: TimelineEntry[],
  event: WorkflowEvent,
  status: string | null,
): TimelineEntry[] | null {
  if (event.type !== 'tool_call') return null;
  const finalName = toolCallNameOf(event);
  if (!finalName) return null;

  const updated = [...prev];
  const targetIdx = findLastIncompleteWorkflowIdx(updated);
  if (targetIdx < 0) return null;

  const entry = updated[targetIdx] as Extract<TimelineEntry, { kind: 'workflow' }>;
  const wf = entry.data;
  const events = [...wf.events];
  let matchIdx = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    const evt = events[i];
    if (!isPartialToolCall(evt) || evt.result) continue;
    if (toolCallNameOf(evt) === finalName) {
      matchIdx = i;
      break;
    }
  }
  if (matchIdx < 0) return null;

  const prevEvt = events[matchIdx];
  events[matchIdx] = {
    ...prevEvt,
    ...event,
    content: {
      ...(typeof event.content === 'object' ? event.content : {}),
      partial: false,
    },
    _uid: prevEvt._uid || event._uid || genTimelineUID(),
    timestamp: event.timestamp || prevEvt.timestamp,
  };
  updated[targetIdx] = {
    ...entry,
    data: { ...wf, events, status: status ?? wf.status, completed: false },
  };
  return updated;
}

/** Pull replace_in_file UI context fields off a tool_result payload. */
export function extractDiffContext(resultData: unknown): {
  diffOld?: string;
  diffNew?: string;
  diffStartLine?: number;
} {
  if (!resultData || typeof resultData !== 'object') return {};
  const data = resultData as Record<string, unknown>;
  const diffOld = typeof data.diff_old === 'string' ? data.diff_old : undefined;
  const diffNew = typeof data.diff_new === 'string' ? data.diff_new : undefined;
  const rawStart = data.diff_start_line;
  const diffStartLine =
    typeof rawStart === 'number'
      ? rawStart
      : typeof rawStart === 'string' && rawStart.trim()
        ? Number(rawStart)
        : undefined;
  return {
    diffOld,
    diffNew,
    diffStartLine: Number.isFinite(diffStartLine as number) ? (diffStartLine as number) : undefined,
  };
}

/** Extract display text from a tool_result payload (WS or session event). */
export function extractToolResultText(resultData: unknown): string {
  if (resultData == null) return '';
  if (typeof resultData === 'string') {
    // Compact JSON string of {status, content, …} — unwrap for display.
    const trimmed = resultData.trim();
    if (trimmed.startsWith('{')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === 'object') {
          const nested = extractToolResultText(parsed);
          if (nested) return nested;
        }
      } catch {
        /* keep raw string */
      }
    }
    return resultData;
  }
  if (typeof resultData !== 'object') return String(resultData);
  const data = resultData as Record<string, unknown>;
  const candidates = [data.result, data.output, data.content, data.text, data.message];
  for (const c of candidates) {
    if (typeof c === 'string') {
      if (c.length > 0) return c;
      continue;
    }
    if (c != null && typeof c !== 'object') return String(c);
    if (c != null && typeof c === 'object') {
      const nested = c as Record<string, unknown>;
      // filesystem.read_file etc.: {status, content, meta}
      if (typeof nested.content === 'string' && nested.content.length > 0) {
        return nested.content;
      }
      try {
        return JSON.stringify(c, null, 2);
      } catch {
        /* continue */
      }
    }
  }
  return '';
}

/** Heuristic: tool outcome should render as failure in the UI.
 *
 * Prefer structured `status` / `error` / `aborted`. Free-text checks are
 * prefix / short system envelopes only — never scan arbitrary payload bodies
 * (e.g. successful read_file content that mentions "failed" or `"status":"error"`).
 */
export function isToolResultFailure(result: unknown): boolean {
  if (result == null) return false;

  if (typeof result === 'string') {
    const t = result.trim();
    if (!t) return false;
    // Tool often returns a JSON envelope as a string.
    if (t.startsWith('{') || t.startsWith('[')) {
      try {
        const parsed = JSON.parse(t);
        if (parsed && typeof parsed === 'object') {
          return isToolResultFailure(parsed);
        }
      } catch {
        /* fall through to text heuristic */
      }
    }
    return isToolFailureText(t);
  }

  if (typeof result === 'object') {
    const o = result as Record<string, unknown>;
    if (o.error != null && o.error !== false && o.error !== '') return true;
    if (o.aborted === true) return true;

    if ('status' in o) {
      const status = String(o.status ?? '').toLowerCase();
      if (status === 'error' || status === 'failed' || status === 'failure') return true;
      // Explicit success — do not dig into content/text payloads.
      if (
        status === 'ok' ||
        status === 'success' ||
        status === 'done' ||
        status === 'completed' ||
        status === 'running' ||
        status === 'pending'
      ) {
        return false;
      }
    }

    // WS / session tool_result envelope: { id, name, result, ... }
    if ('result' in o) {
      return isToolResultFailure(o.result);
    }
    // Bare error objects: { message: "Error: ..." } without a success payload.
    if (typeof o.message === 'string' && !('content' in o) && !('output' in o) && !('text' in o)) {
      return isToolFailureText(o.message);
    }
    return false;
  }

  return isToolFailureText(String(result));
}

/** Prefix / known system envelopes only — not mid-body keyword scans. */
function isToolFailureText(text: string): boolean {
  const t = text.trim();
  if (!t) return false;
  if (/^(Error|Failed|Failure)\b/i.test(t)) return true;
  if (/^Blocked in Plan mode\b/i.test(t)) return true;
  if (/^(Security Denied|Permission Denied)\b/i.test(t)) return true;
  if (/^Cancelled:/i.test(t)) return true;
  if (/^Command aborted\b/i.test(t)) return true;
  // Short system lines only (avoid matching long file bodies).
  if (t.length < 240 && /\baborted by user\b/i.test(t)) return true;
  return false;
}

/**
 * Merge a tool_result into the matching tool_call (by id, else last unmatched).
 * Returns a new timeline when merged; null when no unmatched tool_call was found.
 */
export function mergeToolResultIntoTimeline(
  prev: TimelineEntry[],
  event: WorkflowEvent,
  status: string | null,
): TimelineEntry[] | null {
  if (event.type !== 'tool_result') return null;
  const resultData = event.content;
  const resultId =
    typeof resultData === 'object' && resultData
      ? (resultData.id || resultData.tool_use_id)
      : null;
  const resStr = extractToolResultText(resultData);
  const resultStatus =
    (typeof resultData === 'object' && resultData && (resultData as any).error) ||
    isToolResultFailure(resStr) ||
    isToolResultFailure(resultData)
      ? ('error' as const)
      : ('success' as const);

  for (let wi = prev.length - 1; wi >= 0; wi--) {
    if (prev[wi].kind !== 'workflow') continue;
    const wf = (prev[wi] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
    for (let ei = wf.events.length - 1; ei >= 0; ei--) {
      const evt = wf.events[ei];
      if (evt.type !== 'tool_call' || evt.result) continue;
      // Prefer same parent/sub scope so a parent result never latches onto a
      // nested sub-agent tool_call (or vice versa) when ids are missing.
      if (!!evt.subAgent !== !!event.subAgent) continue;
      const callData = typeof evt.content === 'object' && evt.content ? evt.content : {};
      const callId = callData.id || callData.tool_use_id;
      if (!resultId || !callId || resultId === callId) {
        const ctx = extractDiffContext(resultData);
        const newEvents = [...wf.events];
        newEvents[ei] = {
          ...evt,
          result: resStr || evt.result,
          resultStatus,
          ...(ctx.diffOld != null ? { diffOld: ctx.diffOld } : {}),
          ...(ctx.diffNew != null ? { diffNew: ctx.diffNew } : {}),
          ...(ctx.diffStartLine != null ? { diffStartLine: ctx.diffStartLine } : {}),
        };
        return prev.map((entry, idx) =>
          idx === wi
            ? {
                kind: 'workflow' as const,
                data: { ...wf, events: newEvents, status: status ?? wf.status },
                _uid: entry._uid,
              }
            : entry,
        );
      }
    }
  }
  return null;
}

export function appendWorkflowEvent(
  prev: TimelineEntry[],
  event: WorkflowEvent,
  status: string | null,
): TimelineEntry[] {
  // Live Native-FC argument stream → upsert partial tool_call card.
  if (
    event.type === 'tool_call' &&
    typeof event.content === 'object' &&
    event.content &&
    event.content.partial
  ) {
    return upsertPartialToolCall(prev, event, status);
  }

  // Final tool_call: promote matching partial card instead of appending a duplicate.
  if (event.type === 'tool_call') {
    const promoted = promotePartialToolCall(prev, event, status);
    if (promoted) return promoted;
  }

  // Dedup tool_call replays (e.g. after compression hydration).
  // IMPORTANT: tool_result shares the same id namespace as tool_call
  // (`tool:${id}`). Treating tool_result as a duplicate here used to DROP
  // the result entirely, leaving the UI stuck on "No result".
  if (event.type === 'tool_call' && timelineHasToolEvent(prev, event)) {
    return prev;
  }
  if (event.type === 'tool_result' && timelineHasToolEvent(prev, event)) {
    // Matching tool_call already on the timeline — merge result into it
    // (or no-op if that call already has a result from a prior merge).
    return mergeToolResultIntoTimeline(prev, event, status) ?? prev;
  }

  const updated = [...prev];

  // Async/sync sub-agent streams can outlive the parent turn. Route them back
  // to the workflow that owns the matching delegate_task(_submit) — including
  // completed blocks — so the open SubAgentPanel keeps receiving live steps.
  if (event.subAgent || event.jobId) {
    const hostIdx = findAsyncDelegateHostIdx(updated, event);
    if (hostIdx >= 0) {
      const entry = updated[hostIdx] as Extract<TimelineEntry, { kind: 'workflow' }>;
      const wf = entry.data;
      const newEvents = appendEventIntoWorkflowBlock(wf, event);
      updated[hostIdx] = {
        ...entry,
        data: {
          ...wf,
          events: newEvents,
          // Keep completed as-is (parent turn may already be sealed). Still
          // refresh status so live UIs that key off it can notice activity.
          status: wf.completed ? wf.status : status,
        },
      };
      return updated;
    }
  }

  // Find the last incomplete workflow block, skipping over any trailing 'prompt' or
  // 'status_hint' entries (these can be interleaved within the same turn).
  let targetIdx = -1;
  for (let i = updated.length - 1; i >= 0; i--) {
    if (updated[i].kind === 'prompt' || updated[i].kind === 'status_hint') continue;
    if (updated[i].kind === 'workflow' && !(updated[i] as Extract<TimelineEntry, { kind: 'workflow' }>).data.completed) {
      targetIdx = i;
    }
    break;
  }

  // CRITICAL FIX for tool_result: when targetIdx is -1 (e.g. user message is the
  // last entry), search ALL workflow blocks — including completed ones and those
  // separated by user messages — for an unmatched tool_call. This prevents the
  // tool_result from being stranded in a new workflow block while the tool_call
  // remains permanently "running" in an older block.
  if (event.type === 'tool_result' && targetIdx < 0) {
    const resultData = event.content;
    const resultId = typeof resultData === 'object' ? (resultData.id || resultData.tool_use_id) : null;

    for (let wi = updated.length - 1; wi >= 0; wi--) {
      if (updated[wi].kind !== 'workflow') continue;
      const wf = (updated[wi] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
      for (let ei = wf.events.length - 1; ei >= 0; ei--) {
        const evt = wf.events[ei];
        if (evt.type === 'tool_call' && !evt.result) {
          const callData = typeof evt.content === 'object' ? evt.content : {};
          const callId = callData.id || callData.tool_use_id;
          if (!resultId || !callId || resultId === callId) {
            // Found match — merge result into this tool_call
            const resStr = extractToolResultText(resultData);
            wf.events[ei] = {
              ...evt,
              result: resStr,
              resultStatus:
                (typeof resultData === 'object' && resultData && resultData.error) ||
                isToolResultFailure(resStr) ||
                isToolResultFailure(resultData)
                  ? 'error'
                  : 'success',
            };
            // Keep the workflow's completed flag unchanged. Setting completed=false
            // here would re-open a legitimately-finished workflow (e.g. system.wait
            // sends tool_result after to_user_reply), leaving hasActiveWorkflow
            // permanently true and the agent working animation stuck on.
            wf.status = status;
            // Return a new reference to trigger re-render
            return updated.map((entry, idx) =>
              idx === wi
                ? { kind: 'workflow' as const, data: { ...wf, events: [...wf.events] }, _uid: entry._uid }
                : entry
            );
          }
        }
      }
    }

    // No matching tool_call found anywhere — create new workflow block as fallback
    return [
      ...updated,
      {
        kind: 'workflow',
        data: { events: [{ ...event, _uid: genTimelineUID() }], status, completed: false },
        _uid: genTimelineUID(),
      },
    ];
  }

  if (targetIdx >= 0) {
    // Existing incomplete workflow block — append or merge event
    const wf = (updated[targetIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
    const newEvents = appendEventIntoWorkflowBlock(wf, event);

    updated[targetIdx] = {
      ...updated[targetIdx],
      data: { events: newEvents, status, completed: false },
    } as TimelineEntry;
  } else {
    // Create new workflow block
    updated.push({
      kind: 'workflow',
      data: { events: [{ ...event, _uid: genTimelineUID() }], status, completed: false },
      _uid: genTimelineUID(),
    });
  }

  return updated;
}

export function toWebMediaUrl(input: any): string {
  if (typeof input !== 'string') return '';
  const raw = input.trim();
  if (!raw) return '';

  // Absolute URL
  if (/^https?:\/\//i.test(raw)) return raw;

  // Already a web upload path
  if (raw.startsWith('/uploads/')) return raw;

  // Windows/Linux absolute file path containing uploads segment
  const m = raw.match(/[\\/]uploads[\\/](.+)$/i);
  if (m && m[1]) {
    const rel = m[1].replace(/\\/g, '/');
    return `/uploads/${rel}`;
  }

  // Any other leading-slash absolute path should not be served directly;
  // fall back to upload basename route.
  if (raw.startsWith('/')) {
    const base = raw.split('/').pop();
    return base ? `/uploads/${base}` : '';
  }

  // Plain filename
  return `/uploads/${raw.split(/[/\\]/).pop()}`;
}

export function mergeOrphanedToolResultsAcrossWorkflows(timeline: TimelineEntry[]): TimelineEntry[] {
  type ToolCallRef = {
    wfIdx: number;
    eventIdx: number;
    callId: string | null;
  };
  type OrphanResultRef = {
    wfIdx: number;
    eventIdx: number;
    resultId: string | null;
  };

  const toolCalls: ToolCallRef[] = [];
  const orphanResults: OrphanResultRef[] = [];

  // Collect all tool_calls (without result) and orphaned tool_results
  timeline.forEach((entry, tlIdx) => {
    if (entry.kind !== 'workflow') return;
    const wf = entry as Extract<TimelineEntry, { kind: 'workflow' }>;
    wf.data.events.forEach((evt, evtIdx) => {
      if (evt.type === 'tool_call' && !evt.result) {
        const callId = (typeof evt.content === 'object' && evt.content)
          ? (evt.content.id || evt.content.tool_use_id || null)
          : null;
        toolCalls.push({ wfIdx: tlIdx, eventIdx: evtIdx, callId });
      } else if (evt.type === 'tool_result') {
        const resultId = (typeof evt.content === 'object' && evt.content)
          ? (evt.content.id || evt.content.tool_use_id || null)
          : null;
        orphanResults.push({ wfIdx: tlIdx, eventIdx: evtIdx, resultId });
      }
    });
  });

  if (orphanResults.length === 0) return timeline;

  // Track which entries have been mutated so we can create new references
  const mutatedWfIndices = new Set<number>();

  const hasUserMessageBetween = (fromWfIdx: number, toWfIdx: number): boolean => {
    if (toWfIdx <= fromWfIdx) return false;
    for (let i = fromWfIdx + 1; i < toWfIdx; i++) {
      if (timeline[i]?.kind === 'message' && (timeline[i] as Extract<TimelineEntry, { kind: 'message' }>).data.role === 'user') {
        return true;
      }
    }
    return false;
  };

  // Merge each orphaned result into the nearest preceding unmatched tool_call
  const usedCallIndices = new Set<number>();
  for (const orphan of orphanResults) {
    // Find the closest preceding tool_call that hasn't been used yet
    let bestMatch: ToolCallRef | null = null;
    let bestCallListIdx = -1;
    for (let i = toolCalls.length - 1; i >= 0; i--) {
      const tc = toolCalls[i];
      const tcGlobalIdx = tc.wfIdx * 1000 + tc.eventIdx;
      const orphanGlobalIdx = orphan.wfIdx * 1000 + orphan.eventIdx;
      if (tcGlobalIdx >= orphanGlobalIdx || usedCallIndices.has(i)) continue;

      const exactId = !!(orphan.resultId && tc.callId && orphan.resultId === tc.callId);
      // After compression/archive, loose matching across a user-message boundary
      // can glue a new tool_result onto an older turn's tool_call and scramble order.
      if (!exactId && hasUserMessageBetween(tc.wfIdx, orphan.wfIdx)) continue;

      if (exactId) {
        bestMatch = tc;
        bestCallListIdx = i;
        break; // Exact match found, use it
      }
      if (!bestMatch) {
        bestMatch = tc;
        bestCallListIdx = i;
      }
    }

    if (bestMatch) {
      usedCallIndices.add(bestCallListIdx);
      // Create new event objects with the merged result
      const orphanEvt = (timeline[orphan.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data.events[orphan.eventIdx];
      const resStr = (typeof orphanEvt.content === 'object' && orphanEvt.content)
        ? (typeof orphanEvt.content.result === 'string'
          ? orphanEvt.content.result
          : (orphanEvt.content.output || JSON.stringify(orphanEvt.content)))
        : String(orphanEvt.content || '');

      const mergedEvent: WorkflowEvent = {
        ...(timeline[bestMatch.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data.events[bestMatch.eventIdx],
        result: resStr,
        resultStatus:
          (typeof orphanEvt.content === 'object' && orphanEvt.content && orphanEvt.content.error) ||
          isToolResultFailure(resStr)
            ? 'error'
            : 'success',
      };

      if (bestMatch.wfIdx !== orphan.wfIdx) {
        // Cross-workflow merge: keep merged event in the LATER position (orphan's workflow)
        // so the tool flow doesn't visually "jump up" to an older block.
        // Replace orphan with merged event, remove original tool_call from its workflow.
        const tcWf = (timeline[bestMatch.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
        const newTcEvents = [...tcWf.events];
        newTcEvents.splice(bestMatch.eventIdx, 1);
        tcWf.events = newTcEvents;
        mutatedWfIndices.add(bestMatch.wfIdx);

        const orphanWf = (timeline[orphan.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
        const newOrphanEvents = [...orphanWf.events];
        newOrphanEvents[orphan.eventIdx] = mergedEvent;
        orphanWf.events = newOrphanEvents;
        mutatedWfIndices.add(orphan.wfIdx);
      } else {
        // Same-workflow merge: standard path
        const tcWf = (timeline[bestMatch.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
        const newTcEvents = [...tcWf.events];
        newTcEvents[bestMatch.eventIdx] = mergedEvent;
        tcWf.events = newTcEvents;
        mutatedWfIndices.add(bestMatch.wfIdx);

        const orphanWf = (timeline[orphan.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
        const newOrphanEvents = [...orphanWf.events];
        newOrphanEvents.splice(orphan.eventIdx, 1);
        orphanWf.events = newOrphanEvents;
        mutatedWfIndices.add(orphan.wfIdx);
      }
    }
  }

  // Create new timeline array with updated workflow references for React
  // Also remove empty workflow blocks
  const result: TimelineEntry[] = [];
  for (let i = 0; i < timeline.length; i++) {
    const entry = timeline[i];
    if (entry.kind === 'workflow') {
      const wf = entry as Extract<TimelineEntry, { kind: 'workflow' }>;
      if (wf.data.events.length === 0) continue; // Remove empty blocks
      if (mutatedWfIndices.has(i)) {
        // Create new reference to trigger re-render
        result.push({ kind: 'workflow', data: { ...wf.data, events: [...wf.data.events] }, _uid: entry._uid });
      } else {
        result.push(entry);
      }
    } else {
      result.push(entry);
    }
  }

  return result;
}

export function buildTimelineFromSession(
  messages: any[],
  events: any[],
  archivedMessages?: any[],
  archivedEvents?: any[],
): TimelineEntry[] {
  // Context compression still stores older turns in archived_* on disk, but the
  // UI no longer folds them into an "已归档" section (that scrambled tool order).
  // Flatten archived + live into one stream so history stays chronological.
  if (
    (archivedMessages && archivedMessages.length > 0) ||
    (archivedEvents && archivedEvents.length > 0)
  ) {
    return buildTimelineFromSession(
      [...(archivedMessages || []), ...(messages || [])],
      [...(archivedEvents || []), ...(events || [])],
    );
  }

  const timeline: TimelineEntry[] = [];
  const getTs = (value: any): number => {
    const ts = value?.timestamp ? new Date(value.timestamp).getTime() : NaN;
    return Number.isNaN(ts) ? Number.MAX_SAFE_INTEGER : ts;
  };

  // Preserve message array order. Sort events by timestamp (then insertion
  // order) so we can walk them once while iterating messages.
  const sortedEvents = events
    .map((evt, index) => ({ item: evt, ts: getTs(evt), order: index }))
    .sort((a, b) => (a.ts !== b.ts ? a.ts - b.ts : a.order - b.order));

  let pendingRaw: any[] = [];
  let eventCursor = 0;

  const flushPendingWorkflow = (opts?: { completed?: boolean; elapsedMs?: number }) => {
    if (pendingRaw.length === 0) return;

    const workflowEvents: any[] = [];
    let startedMs: number | undefined;
    for (const rawEvt of pendingRaw) {
      if (rawEvt.type === 'prompt_update') {
        const p = rawEvt.data || {};
        const systemPrompt = typeof p.system_prompt === 'string' ? p.system_prompt : '';
        if (systemPrompt) {
          timeline.push({
            kind: 'prompt',
            data: {
              system_prompt: systemPrompt,
              dynamic_prefix: typeof p.dynamic_prefix === 'string' ? p.dynamic_prefix : '',
              changed: !!p.changed,
              timestamp: rawEvt.timestamp || new Date().toISOString(),
              diff: Array.isArray(p.diff) ? p.diff : undefined,
            },
            _uid: genTimelineUID(),
          });
        }
      } else {
        const markerMs = workflowStartedMsFromRaw(rawEvt);
        if (typeof markerMs === 'number') startedMs = markerMs;
        workflowEvents.push(rawEvt);
      }
    }

    pendingRaw = [];

    if (workflowEvents.length === 0) return;
    const wfEvents = convertSessionEventsToWorkflow(workflowEvents);
    // In-progress turn may only have a "Workflow started" marker (filtered from
    // display) — still emit an incomplete block so refresh shows Working.
    if (wfEvents.length === 0) {
      if (opts?.completed === false && typeof startedMs === 'number') {
        timeline.push({
          kind: 'workflow',
          data: {
            events: [],
            status: 'working',
            completed: false,
            started_ms: startedMs,
          },
          _uid: genTimelineUID(),
        });
      }
      return;
    }
    if (startedMs == null) {
      const firstTs = wfEvents[0]?.timestamp;
      if (typeof firstTs === 'number') startedMs = firstTs;
    }
    timeline.push({
      kind: 'workflow',
      data: {
        events: wfEvents,
        status: opts?.completed === false ? 'working' : null,
        completed: opts?.completed !== false,
        elapsed_ms: opts?.elapsedMs,
        started_ms: startedMs,
      },
      _uid: genTimelineUID(),
    });
  };

  // Pull events whose timestamp is strictly before `beforeTs` into pendingRaw.
  // Events with missing/invalid timestamps (MAX_SAFE_INTEGER) stay until the end.
  const pullEventsBefore = (beforeTs: number) => {
    while (eventCursor < sortedEvents.length) {
      const ev = sortedEvents[eventCursor];
      if (ev.ts >= beforeTs) break;
      pendingRaw.push(ev.item);
      eventCursor += 1;
    }
  };

  for (let mi = 0; mi < messages.length; mi++) {
    const m = messages[mi];
    const mTs = getTs(m);

    // Do NOT pull/flush workflow events before a user bubble.
    // After compression (and with sub-agent / clock skew), same-turn
    // thought/tool events can have timestamps <= the user message. Pulling
    // them here used to render "Worked" above the user who triggered it.
    // User turns only flush parked context_summary entries (see below).
    if (m.role !== 'user') {
      pullEventsBefore(mTs);
    }

    // System context_summary → must NOT act as a workflow boundary.
    // Convert to summary_stream event and keep its original position in the
    // pending event buffer so ordering stays timestamp-accurate.
    if (m.role === 'system' && m.type === 'context_summary') {
      const summaryText = typeof m.content === 'string' ? m.content : '';
      if (summaryText) {
        pendingRaw.push({
          type: 'summary_stream',
          data: {
            id: 'summary_history',
            text: summaryText,
            done: true,
          },
          timestamp: m.timestamp || new Date().toISOString(),
        });
      }
      continue;
    }

    if (m.type === 'api_sync') {
      // api_sync assistant messages carry the agent's final to_user text
      // synced from the agent disk. Only skip them when they are truly
      // empty placeholders; if they have content, render it so the reply
      // is visible after a restart/history reload.
      const syncContent = typeof m.content === 'string' ? m.content.trim() : '';
      if (!syncContent) {
        continue;
      }
      // Fall through and render as a normal assistant text message.
    }

    // Assistant replies are often persisted (ChatAPI api_sync) BEFORE the
    // matching thought/plan events are written at end-of-turn. Live UI still
    // shows think→reply because WS streams thoughts first; on reload, pure
    // timestamp interleaving would put the reply above the thought.
    // Absorb the rest of this turn's events (until the next user message)
    // so workflow stays above the assistant bubble after refresh.
    if (m.role === 'assistant') {
      let turnEndTs = Number.POSITIVE_INFINITY;
      for (let j = mi + 1; j < messages.length; j++) {
        if (messages[j]?.role === 'user') {
          turnEndTs = getTs(messages[j]);
          break;
        }
      }
      pullEventsBefore(turnEndTs);
    }

    // Flush workflow before assistant (and other non-user) messages only.
    // Before a user message, flush only if pending is parked context_summary
    // content — never same-turn tools.
    if (m.role === 'user') {
      const onlySummary =
        pendingRaw.length > 0 &&
        pendingRaw.every(
          (raw) =>
            raw?.type === 'summary_stream' ||
            raw?.type === 'compression_progress' ||
            raw?.type === 'prompt_update',
        );
      if (onlySummary) {
        flushPendingWorkflow({ completed: true });
      }
    } else {
      flushPendingWorkflow({
        completed: true,
        elapsedMs: typeof m.elapsed_ms === 'number' ? m.elapsed_ms : undefined,
      });
    }

    const extra = (m && typeof m.extra === 'object' && m.extra !== null) ? m.extra : {};
    const rawImagesInput = Array.isArray(m.images)
      ? m.images
      : (Array.isArray(extra.images) ? extra.images : []);
    const rawImages = rawImagesInput
      .map((img: any) => {
        if (typeof img === 'string') return img;
        if (img && typeof img === 'object') return img.url || img.path || img.src || '';
        return '';
      })
      .map((u: any) => toWebMediaUrl(u))
      .filter((u: any) => typeof u === 'string' && u.length > 0);
    let rawAttachments = Array.isArray(m.attachments)
      ? m.attachments
      : (Array.isArray(extra.attachments) ? extra.attachments : []);

    // file_push compatibility: derive image urls from files when needed
    const rawFiles = Array.isArray((m as any).files)
      ? (m as any).files
      : (Array.isArray((extra as any).files) ? (extra as any).files : []);
    // Convert non-image files to FileAttachment objects (survives content cleaning).
    const filesAsAttachments: FileAttachment[] = rawFiles
      .filter((f: any) => f && !f.is_image && !(typeof f.content_type === 'string' && f.content_type.startsWith('image/')))
      .map((f: any) => {
        const sz = (b: number) => {
          if (!b || b < 1024) return `${b || 0} B`;
          if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
          return `${(b / (1024 * 1024)).toFixed(1)} MB`;
        };
        const rawUrl = f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '');
        const name = f.original_name || f.filename || 'file';
        const isVoice =
          f.type === 'voice' || !!f.is_audio || /^voice_/i.test(name)
          || /\.(mp3|wav|ogg|m4a|flac|aac|webm)$/i.test(name);
        return {
          name,
          size: sz(f.size),
          url: toWebMediaUrl(rawUrl) || rawUrl || undefined,
          type: f.is_video && !isVoice
            ? 'video' as const
            : isVoice
              ? (f.type === 'voice' ? 'voice' as const : 'audio' as const)
              : 'file' as const,
        };
      });
    if (filesAsAttachments.length > 0) {
      for (const fa of filesAsAttachments) {
        const faKey = (fa.url || '').replace(/\\/g, '/').split('/').pop() || '';
        const exists = (rawAttachments as any[]).some((a) => {
          if (fa.name && a?.name && String(a.name) === String(fa.name)) return true;
          const aKey = String(a?.url || a?.path || '').replace(/\\/g, '/').split('/').pop() || '';
          return !!faKey && !!aKey && faKey === aKey;
        });
        if (!exists) rawAttachments = [...rawAttachments, fa];
      }
    }

    // Lift [File: name (size) path=... type=...](/uploads/...) into attachments so
    // voice bubbles survive refresh when structured attachments are absent on disk.
    if (typeof m.content === 'string' && m.content.includes('[File:')) {
      const fileMarkerRe =
        /\[File:\s*(.+?)\s*\(([^)]*)\)(?:\s*path=([^\s\]]+))?(?:\s*type=(audio|video|voice|file))?\](?:\(([^)\n]+)\))?/g;
      let fm: RegExpExecArray | null;
      while ((fm = fileMarkerRe.exec(m.content)) !== null) {
        const name = (fm[1] || '').trim();
        if (!name) continue;
        const size = (fm[2] || '').trim();
        const pathRaw = (fm[3] || '').trim();
        const kindRaw = (fm[4] || '').trim();
        const mdUrl = (fm[5] || '').trim();
        const preferred = mdUrl || pathRaw;
        if (!preferred) continue;
        const url = toWebMediaUrl(preferred.split(/\s+/)[0]);
        if (!url) continue;
        const exists = (rawAttachments as any[]).some(
          (a) => (a?.url && a.url === url) || (a?.name && a.name === name),
        );
        if (exists) continue;
        let kind: FileAttachment['type'] =
          (kindRaw as FileAttachment['type']) || undefined;
        if (!kind) {
          if (/^voice_/i.test(name) || /\.(mp3|wav|ogg|m4a|flac|aac|webm)$/i.test(name)) kind = 'voice';
          else if (/\.(mp4|mov|avi|mkv)$/i.test(name)) kind = 'video';
          else kind = 'file';
        }
        rawAttachments = [
          ...rawAttachments,
          { name, size, url, path: pathRaw || undefined, type: kind },
        ];
      }
    }
    const fileImages = rawFiles
      .filter((f: any) => !!f && (f.is_image || (typeof f.content_type === 'string' && f.content_type.startsWith('image/'))))
      .map((f: any) => toWebMediaUrl(f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '')))
      .filter((u: any) => typeof u === 'string' && u.length > 0);

    // Parse legacy markers from content: [File: xxx](url)
    const markerUrls: string[] = [];
    if (typeof m.content === 'string') {
      if (m.content.includes('[File:')) {
        const re = /\[File:\s*.*?\]\((.*?)\)/g;
        let mm: RegExpExecArray | null;
        while ((mm = re.exec(m.content)) !== null) {
          const u = (mm[1] || '').trim();
          if (u) markerUrls.push(u);
        }
      }
      const reImg = /<image>(.*?)<\/image>/gi;
      let im: RegExpExecArray | null;
      while ((im = reImg.exec(m.content)) !== null) {
        const u = (im[1] || '').trim();
        if (u) markerUrls.push(u);
      }
    }

    const mergedImages = Array.from(new Set([
      ...rawImages.filter((u: any) => typeof u === 'string' && u.length > 0),
      ...fileImages,
      ...markerUrls
        .map((u) => toWebMediaUrl(u))
        .filter((u) => typeof u === 'string' && u.length > 0),
    ]));

    const rawOutputImages = Array.isArray((m as any).output_images)
      ? (m as any).output_images
      : (Array.isArray((extra as any).output_images) ? (extra as any).output_images : []);
    const rawOutputAudio = Array.isArray((m as any).output_audio)
      ? (m as any).output_audio
      : (Array.isArray((extra as any).output_audio) ? (extra as any).output_audio : []);

    const cleanedContent = typeof m.content === 'string'
      ? formatUserSkillDisplayContent(
          m.content
            .replace(/\n?\s*<image>.*?<\/image>/gis, '')
            // Strip both markdown-link and path=/type= forms of [File: ...]
            .replace(/\n?\s*\[File:\s*.+?\((?:[^)]*)\)(?:\s*path=[^\s\]]+)?(?:\s*type=(?:audio|video|voice|file))?\](?:\([^)\n]+\))?/g, '')
            .trim(),
        )
      : m.content;

    // CRITICAL: Skip creating a message entry if the cleaned content is empty AND there's no
    // media/attachments. This prevents empty white dialogs from being rendered when a
    // message contains only placeholder markers (e.g. <image> or [File:]) that get cleaned.
    const hasMedia = mergedImages.length > 0 || rawAttachments.length > 0 || rawOutputImages.length > 0 || rawOutputAudio.length > 0;
    const hasContentAfterClean = cleanedContent && cleanedContent.length > 0;
    if (!hasContentAfterClean && !hasMedia) {
      // Skip - don't add empty message to the timeline
      continue;
    }

    // Merge same-content duplicates that are very close in time.
    // The window is widened to 30 seconds because session snapshots and
    // real-time WS events can race, causing the same user/assistant message
    // to appear twice after a page refresh. Intentional repeated messages
    // are usually minutes apart, so 30s is a safe balance.
    // For media-bearing messages we additionally require the prior entry
    // to also have media, to avoid merging a media-rich message into a
    // plain text placeholder.
    let dupIdx = -1;
    const mTsNum = m.timestamp ? new Date(m.timestamp).getTime() : NaN;
    const DEDUP_WINDOW_MS = 30000;
    for (let i = timeline.length - 1; i >= 0; i -= 1) {
      const entry = timeline[i];
      if (entry.kind !== 'message') continue;
      const d = entry.data as ChatMessage;
      if (hasMedia) {
        const dHasMedia = !!(
          (Array.isArray(d.images) && d.images.length > 0) ||
          (Array.isArray(d.attachments) && d.attachments.length > 0) ||
          (Array.isArray(d.output_images) && d.output_images.length > 0) ||
          (Array.isArray(d.output_audio) && d.output_audio.length > 0)
        );
        if (!dHasMedia) continue;
      }
      if (d.role !== m.role || d.content !== cleanedContent) continue;
      const dTs = d.timestamp ? new Date(d.timestamp).getTime() : NaN;
      const withinWindow = Number.isNaN(mTsNum) || Number.isNaN(dTs) || Math.abs(dTs - mTsNum) <= DEDUP_WINDOW_MS;
      if (withinWindow) {
        dupIdx = i;
        break;
      }
    }
    if (dupIdx >= 0) {
      // Merge media from duplicate into the existing entry (prefer richer payload)
      const dupEntry = timeline[dupIdx];
      const dupData = dupEntry.data as ChatMessage;
      const mergedImagesDedup = Array.from(new Set([
        ...(dupData.images || []),
        ...mergedImages,
      ]));
      const mergedAttachmentsDedup = Array.from(new Set([
        ...(dupData.attachments || []),
        ...rawAttachments,
      ]));
      timeline[dupIdx] = {
        ...dupEntry,
        kind: 'message',
        data: {
          ...dupData,
          images: mergedImagesDedup.length > 0 ? mergedImagesDedup : undefined,
          attachments: mergedAttachmentsDedup.length > 0 ? mergedAttachmentsDedup : undefined,
        },
      };
      continue;
    }

    // Only user and assistant messages reach here (system/hidden handled above)
    const stableMsgId = String(
      (m as any).message_id ||
        (m as any).client_id ||
        (m as any).id ||
        ((m as any).extra && ((m as any).extra.message_id || (m as any).extra.id)) ||
        '',
    ).trim();
    timeline.push({
      kind: 'message',
      data: {
        role: m.role,
        content: cleanedContent,
        message_id: stableMsgId || undefined,
        timestamp: m.timestamp,
        type: m.type,
        images: mergedImages.length > 0 ? mergedImages : undefined,
        attachments: rawAttachments.length > 0 ? rawAttachments : undefined,
        output_images: rawOutputImages.length > 0 ? rawOutputImages : undefined,
        output_audio: rawOutputAudio.length > 0 ? rawOutputAudio : undefined,
        end_task: !!(m as any).end_task,
      },
      _uid: stableMsgId || genTimelineUID(),
    });
  }

  // Remaining events (including those with missing timestamps) belong after
  // the last message — often a finished compression/summary with no following
  // chat bubble. Mark settled trailing blocks completed so Classic UI does not
  // stick on "working".
  while (eventCursor < sortedEvents.length) {
    pendingRaw.push(sortedEvents[eventCursor].item);
    eventCursor += 1;
  }
  {
    // Peek-convert to decide completed flag without double-flushing.
    const peek = pendingRaw.filter((r) => r.type !== 'prompt_update');
    const peekWf = peek.length > 0 ? convertSessionEventsToWorkflow(peek) : [];
    const trailingDone =
      peekWf.length > 0 &&
      shouldTreatWorkflowComplete({
        events: peekWf,
        status: null,
        completed: false,
      });
    flushPendingWorkflow({ completed: trailingDone });
  }

  // CRITICAL: After building the timeline, orphaned tool_result events may be
  // stuck in separate workflow blocks because user messages act as boundaries.
  // This post-processing pass merges them back into the nearest unmatched
  // tool_call across workflow block boundaries, so the UI shows a complete
  // tool_call card instead of a permanently "running" one.
  const mergedTimeline = mergeOrphanedToolResultsAcrossWorkflows(timeline);

  return applyEndTaskFolds(mergedTimeline);
}

export function convertSessionEventsToWorkflow(rawEvents: any[]): WorkflowEvent[] {
  const result: WorkflowEvent[] = [];

  for (const raw of rawEvents) {
    const type = raw.type;
    const data = raw.data || {};
    const ts = typeof raw.timestamp === 'number'
      ? raw.timestamp
      : (typeof raw.timestamp === 'string'
        ? new Date(raw.timestamp).getTime()
        : Date.now());
    const eventTimestamp = Number.isNaN(ts) ? Date.now() : ts;

    if (type === 'thought') {
      const text = typeof data === 'string' ? data : (data.text || data.content || '');
      if (!text) continue;
      const isSub = typeof data === 'object' && data !== null && !!data.sub_agent;
      const subLabel = typeof data === 'object' && data !== null ? (data.sub_task_label || '') : '';
      const jobId =
        typeof data === 'object' && data !== null && data.job_id
          ? String(data.job_id)
          : undefined;
      const incoming: WorkflowEvent = {
        _uid: genTimelineUID(),
        type: 'thought',
        content: text,
        timestamp: eventTimestamp,
        subAgent: isSub || undefined,
        subTaskLabel: subLabel || undefined,
        jobId,
      };
      const key = thoughtScopeKey(incoming);
      let mergeIdx = -1;
      for (let i = result.length - 1; i >= 0; i--) {
        const e = result[i];
        const eKey = thoughtScopeKey(e);
        if (e.type === 'thought' && eKey === key) {
          mergeIdx = i;
          break;
        }
        if (eKey === key && e.type !== 'thought') break;
      }
      if (mergeIdx >= 0) {
        result[mergeIdx].content = String(result[mergeIdx].content ?? '') + text;
      } else {
        result.push(incoming);
      }
    } else if (type === 'tool_call' || type === 'tool_call_delta') {
      const isSub = !!data.sub_agent;
      const jobId = data.job_id ? String(data.job_id) : undefined;
      const callId = data.id != null ? String(data.id) : '';
      const streamIndex = data.index != null ? Number(data.index) : NaN;
      const isPartial = type === 'tool_call_delta' || !!data.partial;
      const args = data.args || data.arguments || data.input;
      const toolName = data.name || data.tool || 'Tool';

      // Upsert streaming partials / final tool_call by id or stream index so
      // refresh does not show duplicate Writing rows for the same call.
      let matchIdx = -1;
      for (let i = result.length - 1; i >= 0; i--) {
        const evt = result[i];
        if (evt.type !== 'tool_call' || evt.result) continue;
        const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
        if (callId && String(c.id || '') === callId) {
          matchIdx = i;
          break;
        }
        if (
          Number.isFinite(streamIndex) &&
          c.partial &&
          Number(c.index) === streamIndex &&
          String(c.name || '') === String(toolName)
        ) {
          matchIdx = i;
          break;
        }
      }

      const nextContent: Record<string, unknown> = {
        id: callId || undefined,
        name: toolName,
        args,
        arguments: args,
      };
      if (isPartial) {
        nextContent.partial = true;
        if (Number.isFinite(streamIndex)) nextContent.index = streamIndex;
      }

      if (matchIdx >= 0) {
        const prevEvt = result[matchIdx];
        const prevC = typeof prevEvt.content === 'object' && prevEvt.content ? prevEvt.content : {};
        result[matchIdx] = {
          ...prevEvt,
          content: isPartial
            ? { ...prevC, ...nextContent, partial: true }
            : { ...prevC, ...nextContent },
          timestamp: eventTimestamp,
        };
        if (!isPartial && result[matchIdx].content && typeof result[matchIdx].content === 'object') {
          delete (result[matchIdx].content as Record<string, unknown>).partial;
          delete (result[matchIdx].content as Record<string, unknown>).index;
        }
      } else if (!isPartial) {
        // Final tool_call often uses a runner-generated id different from the
        // streaming Native-FC id — promote the latest open partial with same name.
        let promoteIdx = -1;
        for (let i = result.length - 1; i >= 0; i--) {
          const evt = result[i];
          if (evt.type !== 'tool_call' || evt.result) continue;
          const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
          if (!c.partial) continue;
          if (String(c.name || '') === String(toolName)) {
            promoteIdx = i;
            break;
          }
        }
        if (promoteIdx >= 0) {
          const prevEvt = result[promoteIdx];
          const prevC = typeof prevEvt.content === 'object' && prevEvt.content ? prevEvt.content : {};
          const merged = { ...prevC, ...nextContent };
          delete merged.partial;
          delete merged.index;
          result[promoteIdx] = {
            ...prevEvt,
            content: merged,
            timestamp: eventTimestamp,
            subAgent: isSub || prevEvt.subAgent || undefined,
            subTaskLabel: data.sub_task_label || prevEvt.subTaskLabel,
            jobId: jobId || prevEvt.jobId,
          };
        } else {
          result.push({
            _uid: genTimelineUID(),
            type: 'tool_call',
            content: nextContent,
            timestamp: eventTimestamp,
            subAgent: isSub || undefined,
            subTaskLabel: data.sub_task_label || undefined,
            jobId,
          });
        }
      } else {
        result.push({
          _uid: genTimelineUID(),
          type: 'tool_call',
          content: nextContent,
          timestamp: eventTimestamp,
          subAgent: isSub || undefined,
          subTaskLabel: data.sub_task_label || undefined,
          jobId,
        });
      }
    } else if (type === 'tool_result') {
      // Merge into matching tool_call
      const resultId = data.id || data.tool_use_id;
      const isSub = !!data.sub_agent;
      const jobId = data.job_id ? String(data.job_id) : undefined;
      let merged = false;
      for (let i = result.length - 1; i >= 0; i--) {
        const evt = result[i];
        if (evt.type === 'tool_call' && !evt.result) {
          const callId = evt.content?.id;
          if (!resultId || !callId || resultId === callId) {
            const resStr = extractToolResultText(data);
            const ctx = extractDiffContext(data);
            evt.result = resStr;
            evt.resultStatus =
              data.error || isToolResultFailure(resStr) || isToolResultFailure(data) ? 'error' : 'success';
            if (jobId && !evt.jobId) evt.jobId = jobId;
            if (ctx.diffOld != null) evt.diffOld = ctx.diffOld;
            if (ctx.diffNew != null) evt.diffNew = ctx.diffNew;
            if (ctx.diffStartLine != null) evt.diffStartLine = ctx.diffStartLine;
            merged = true;
            break;
          }
        }
      }
      if (!merged) {
        // Standalone result (fallback)
        const resStr = extractToolResultText(data) || JSON.stringify(data.result || data);
        result.push({
          _uid: genTimelineUID(),
          type: 'tool_result',
          content: { name: data.name || 'Tool', result: resStr },
          timestamp: eventTimestamp,
          subAgent: isSub || undefined,
          subTaskLabel: data.sub_task_label || undefined,
          jobId,
        });
      }
    } else if (type === 'plan') {
      // data is {id, text} from Runner (runner.py:809)
      const planContent = typeof data === 'string' ? data : (data.text || data.content || data);
      const steps = parsePlanContent(planContent);
      if (steps.length > 0) {
        result.push({ _uid: genTimelineUID(), type: 'plan', content: steps, timestamp: eventTimestamp });
      }
    } else if (type === 'summary_stream') {
      const streamData = typeof data === 'object' && data !== null ? data : {};
      result.push({
        _uid: genTimelineUID(),
        type: 'summary_stream',
        content: { id: streamData.id || 'summary_history', text: streamData.text || '', done: !!streamData.done },
        timestamp: eventTimestamp,
      });
    } else if (type === 'info') {
      const detailed =
        typeof data === 'string'
          ? { text: data }
          : (typeof data === 'object' && data !== null ? data : { text: String(data) });
      // Skip system info prompts — these are internal state messages
      // that shouldn't display in the workflow UI (e.g. "Agent entering
      // wait mode", "Context summary generated", "Workflow started").
      if (detailed.text && /entering wait mode|listening for events|Workflow started|New session started|Context summary|Context compressed|compression skipped|injected into prompt/i.test(detailed.text)) {
        continue;
      }
      const isSub = !!detailed.sub_agent;
      const jobId = detailed.job_id ? String(detailed.job_id) : undefined;
      result.push({
        _uid: genTimelineUID(),
        type: 'info',
        content: detailed,
        timestamp: eventTimestamp,
        subAgent: isSub || undefined,
        subTaskLabel: detailed.sub_task_label || undefined,
        jobId,
      });
    }
    // Skip other event types (option, etc.) — or add handling as needed
  }

  return result;
}
