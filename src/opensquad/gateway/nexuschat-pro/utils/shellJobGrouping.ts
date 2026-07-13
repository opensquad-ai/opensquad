/**
 * Group system shell/job tools into CMD-style live panels (like DelegateFold).
 */
import type { WorkflowEvent } from './aiChatTimeline';
import { extractToolResultText, isToolResultFailure } from './aiChatTimeline';

export interface ShellJobBundle {
  id: string;
  parent: WorkflowEvent;
  command: string;
  jobId?: string;
  sessionId?: string;
  shellType?: string;
  running: boolean;
  errored: boolean;
  /** Cumulative stdout from live job_stdout + sealed tool_result */
  output: string;
}

export type DisplayWorkflowItemWithShell =
  | { kind: 'event'; event: WorkflowEvent; key: string }
  | { kind: 'delegation'; bundle: import('./delegateGrouping').DelegateBundle; key: string }
  | { kind: 'shell_job'; bundle: ShellJobBundle; key: string };

const SHELL_JOB_RE = /(?:^|[.__])(start_job|run_session_job)$/i;
const SHELL_POLL_RE = /(?:^|[.__])check_job$/i;

function normalizeToolName(name: unknown): string {
  return typeof name === 'string' ? name.trim() : '';
}

export function isShellJobToolName(name: unknown): boolean {
  const n = normalizeToolName(name);
  if (!n) return false;
  if (!/(?:^|[.__])system(?:$|[.__])/i.test(n) && !SHELL_JOB_RE.test(n)) {
    // Allow short names start_job / run_session_job when already under system namespace forms
    return /(?:^|[.__])(start_job|run_session_job)$/i.test(n);
  }
  return SHELL_JOB_RE.test(n);
}

export function isShellPollToolName(name: unknown): boolean {
  const n = normalizeToolName(name);
  if (!n) return false;
  return SHELL_POLL_RE.test(n);
}

export function toolNameOfEvent(evt: WorkflowEvent): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return String(data.name || data.tool || '');
}

export function isShellJobToolCall(evt: WorkflowEvent): boolean {
  return evt.type === 'tool_call' && !evt.subAgent && isShellJobToolName(toolNameOfEvent(evt));
}

export function isShellPollToolCall(evt: WorkflowEvent): boolean {
  return (
    (evt.type === 'tool_call' || evt.type === 'tool_result') &&
    !evt.subAgent &&
    isShellPollToolName(toolNameOfEvent(evt))
  );
}

function parentCallId(evt: WorkflowEvent): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return String(data.id || data.tool_use_id || evt._uid || '');
}

function parseArgsObject(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === 'string') {
    try {
      const p = JSON.parse(raw);
      if (p && typeof p === 'object' && !Array.isArray(p)) return p as Record<string, unknown>;
    } catch {
      /* ignore */
    }
  }
  return {};
}

export function extractShellCommand(evt: WorkflowEvent): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  const args = parseArgsObject(data.arguments ?? data.args ?? data.input);
  const cmd = args.command ?? args.cmd ?? args.script;
  return typeof cmd === 'string' ? cmd : String(cmd || '').trim();
}

function tryParseJson(text: string): Record<string, unknown> | null {
  const t = text.trim();
  if (!t.startsWith('{') && !t.startsWith('[')) return null;
  try {
    const p = JSON.parse(t);
    return p && typeof p === 'object' && !Array.isArray(p) ? (p as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** start_job returned early with completed:false — keep panel running. */
export function isShellJobStillRunningAck(result: unknown): boolean {
  const raw = typeof result === 'string' ? result : result != null ? String(result) : '';
  if (!raw) return false;
  const o = tryParseJson(raw);
  if (!o) return false;
  if (o.completed === false) return true;
  if (o.aborted === true) return false;
  const status = String(o.status || '').toLowerCase();
  if (status === 'error' && o.aborted) return false;
  return false;
}

export function parseJobIdFromShellResult(result: unknown): string {
  const raw = typeof result === 'string' ? result : '';
  const o = tryParseJson(raw);
  if (o && typeof o.job_id === 'string') return o.job_id;
  return '';
}

export function extractShellOutputFromResult(result: unknown): string {
  if (result == null) return '';
  const raw = typeof result === 'string' ? result : String(result);
  const o = tryParseJson(raw);
  if (!o) return raw;
  if (typeof o.output === 'string') return o.output;
  if (typeof o.data === 'string') return o.data;
  if (typeof o.partial_data === 'string') return o.partial_data;
  return raw;
}

export interface ShellStreamState {
  callId: string;
  output: string;
  state: 'running' | 'done' | 'error' | 'aborted';
  command?: string;
  jobId?: string;
  sessionId?: string;
  shellType?: string;
  returnCode?: number | null;
}

export function applyJobStdout(
  prev: Record<string, ShellStreamState>,
  payload: Record<string, unknown>,
): Record<string, ShellStreamState> {
  let callId = String(payload.call_id || '');
  const jobId = typeof payload.job_id === 'string' ? payload.job_id : '';
  // Fallback: match an existing running stream by job_id when call_id is missing
  if (!callId && jobId) {
    const match = Object.values(prev).find((s) => s.jobId === jobId);
    if (match) callId = match.callId;
  }
  if (!callId) return prev;
  const chunk = typeof payload.chunk === 'string' ? payload.chunk : '';
  const existing = prev[callId];
  return {
    ...prev,
    [callId]: {
      callId,
      output: (existing?.output || '') + chunk,
      state: existing?.state || 'running',
      command: typeof payload.command === 'string' ? payload.command : existing?.command,
      jobId: jobId || existing?.jobId,
      sessionId: typeof payload.session_id === 'string' ? payload.session_id : existing?.sessionId,
      shellType: typeof payload.shell_type === 'string' ? payload.shell_type : existing?.shellType,
      returnCode: existing?.returnCode ?? null,
    },
  };
}

export function applyJobStatus(
  prev: Record<string, ShellStreamState>,
  payload: Record<string, unknown>,
): Record<string, ShellStreamState> {
  let callId = String(payload.call_id || '');
  const jobId = typeof payload.job_id === 'string' ? payload.job_id : '';
  if (!callId && jobId) {
    const match = Object.values(prev).find((s) => s.jobId === jobId);
    if (match) callId = match.callId;
  }
  if (!callId) return prev;
  const stateRaw = String(payload.state || 'running').toLowerCase();
  const state: ShellStreamState['state'] =
    stateRaw === 'done' || stateRaw === 'error' || stateRaw === 'aborted' || stateRaw === 'running'
      ? stateRaw
      : 'running';
  const existing = prev[callId];
  return {
    ...prev,
    [callId]: {
      callId,
      output: existing?.output || '',
      state,
      command: typeof payload.command === 'string' ? payload.command : existing?.command,
      jobId: jobId || existing?.jobId,
      sessionId: typeof payload.session_id === 'string' ? payload.session_id : existing?.sessionId,
      shellType: typeof payload.shell_type === 'string' ? payload.shell_type : existing?.shellType,
      returnCode:
        typeof payload.return_code === 'number'
          ? payload.return_code
          : existing?.returnCode ?? null,
    },
  };
}

function refreshShellBundle(
  bundle: ShellJobBundle,
  stream?: ShellStreamState | null,
): ShellJobBundle {
  const result = bundle.parent.result;
  const stillAck = !!(result && isShellJobStillRunningAck(result));
  const streamRunning = stream?.state === 'running' || (!stream?.state && !result);
  const streamDone =
    stream?.state === 'done' || stream?.state === 'error' || stream?.state === 'aborted';
  const sealedByResult = !!(result && !stillAck);
  const running = !sealedByResult && !streamDone && (stillAck || streamRunning || !result);

  const fromResult = sealedByResult ? extractShellOutputFromResult(result) : '';
  const output =
    (stream?.output && stream.output.length > 0 ? stream.output : '') ||
    fromResult ||
    bundle.output ||
    '';

  const errored =
    stream?.state === 'error' ||
    stream?.state === 'aborted' ||
    bundle.parent.resultStatus === 'error' ||
    isToolResultFailure(result);

  return {
    ...bundle,
    jobId: stream?.jobId || bundle.jobId || parseJobIdFromShellResult(result) || undefined,
    sessionId: stream?.sessionId || bundle.sessionId,
    shellType: stream?.shellType || bundle.shellType,
    command: stream?.command || bundle.command || extractShellCommand(bundle.parent),
    output,
    running,
    errored: !!errored && !running,
  };
}

/**
 * Merge shell_job items into an existing display list from buildDisplayWorkflowItems.
 * Hides check_job tool cards; wraps start_job / run_session_job as shell_job.
 */
export function attachShellJobsToDisplayItems(
  items: Array<{ kind: string; event?: WorkflowEvent; bundle?: any; key: string }>,
  streams: Record<string, ShellStreamState> = {},
): DisplayWorkflowItemWithShell[] {
  const out: DisplayWorkflowItemWithShell[] = [];

  for (const item of items) {
    if (item.kind === 'delegation' && item.bundle) {
      out.push({ kind: 'delegation', bundle: item.bundle, key: item.key });
      continue;
    }
    if (item.kind !== 'event' || !item.event) {
      continue;
    }
    const evt = item.event;

    if (isShellPollToolCall(evt)) {
      continue; // hide check_job from main stream
    }

    if (isShellJobToolCall(evt)) {
      const callId = parentCallId(evt);
      const stream = streams[callId];
      const command = extractShellCommand(evt) || stream?.command || 'command';
      const bundle = refreshShellBundle(
        {
          id: callId || item.key,
          parent: evt,
          command,
          jobId: stream?.jobId || parseJobIdFromShellResult(evt.result) || undefined,
          sessionId: stream?.sessionId,
          shellType: stream?.shellType,
          running: !evt.result,
          errored: false,
          output: '',
        },
        stream,
      );
      out.push({ kind: 'shell_job', key: `shell-${bundle.id}`, bundle });
      continue;
    }

    // Orphan tool_result for shell tools already merged onto tool_call — skip stray
    if (evt.type === 'tool_result' && isShellJobToolName(toolNameOfEvent(evt))) {
      continue;
    }
    if (evt.type === 'tool_result' && isShellPollToolName(toolNameOfEvent(evt))) {
      continue;
    }

    out.push({ kind: 'event', event: evt, key: item.key });
  }

  return out;
}

/** Seed stream state when a shell tool_call arrives (before first job_stdout). */
export function seedShellStreamFromToolCall(
  prev: Record<string, ShellStreamState>,
  evt: WorkflowEvent,
): Record<string, ShellStreamState> {
  if (!isShellJobToolCall(evt)) return prev;
  const callId = parentCallId(evt);
  if (!callId || prev[callId]) return prev;
  return {
    ...prev,
    [callId]: {
      callId,
      output: '',
      state: 'running',
      command: extractShellCommand(evt),
    },
  };
}

/** When tool_result seals a shell job, sync stream state from the result. */
export function sealShellStreamFromResult(
  prev: Record<string, ShellStreamState>,
  callId: string,
  result: unknown,
): Record<string, ShellStreamState> {
  if (!callId) return prev;
  const still = isShellJobStillRunningAck(result);
  const existing = prev[callId];
  const fromResult = extractShellOutputFromResult(result);
  const failed = isToolResultFailure(result);
  const jobId = existing?.jobId || parseJobIdFromShellResult(result) || undefined;
  return {
    ...prev,
    [callId]: {
      callId,
      output: (existing?.output && existing.output.length > 0 ? existing.output : fromResult) || '',
      state: still ? 'running' : failed ? 'error' : 'done',
      command: existing?.command,
      jobId,
      sessionId: existing?.sessionId,
      shellType: existing?.shellType,
      returnCode: existing?.returnCode ?? null,
    },
  };
}

/** Rebuild CMD panel state from persisted workflow events after refresh/restart. */
export function rebuildShellStreamsFromEvents(
  events: WorkflowEvent[],
): Record<string, ShellStreamState> {
  let streams: Record<string, ShellStreamState> = {};
  for (const evt of events) {
    if (evt.type === 'tool_call' && isShellJobToolCall(evt)) {
      streams = seedShellStreamFromToolCall(streams, evt);
      if (evt.result != null && evt.result !== '') {
        const callId = parentCallId(evt);
        if (callId) streams = sealShellStreamFromResult(streams, callId, evt.result);
      }
      continue;
    }
    if (evt.type === 'tool_result' && !evt.subAgent && isShellJobToolName(toolNameOfEvent(evt))) {
      const callId = parentCallId(evt);
      if (!callId) continue;
      if (!streams[callId]) {
        streams = {
          ...streams,
          [callId]: {
            callId,
            output: '',
            state: 'running',
            command: extractShellCommand({ ...evt, type: 'tool_call' } as WorkflowEvent),
          },
        };
      }
      const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
      const result = (data as any).result ?? evt.content;
      streams = sealShellStreamFromResult(streams, callId, result);
    }
  }
  return streams;
}

/** Merge shell streams from every workflow block on a timeline. */
export function rebuildShellStreamsFromTimeline(
  timeline: Array<{ kind: string; data?: { events?: WorkflowEvent[] } }>,
): Record<string, ShellStreamState> {
  let streams: Record<string, ShellStreamState> = {};
  for (const entry of timeline) {
    if (entry.kind !== 'workflow') continue;
    const events = entry.data?.events || [];
    const part = rebuildShellStreamsFromEvents(events);
    if (Object.keys(part).length === 0) continue;
    streams = { ...streams, ...part };
  }
  return streams;
}

/** After start_job returns job_id, attach it onto the running stream for job_id-based matching. */
export function attachJobIdToStream(
  prev: Record<string, ShellStreamState>,
  callId: string,
  jobId: string,
): Record<string, ShellStreamState> {
  if (!callId || !jobId) return prev;
  const existing = prev[callId];
  if (!existing) {
    return {
      ...prev,
      [callId]: { callId, output: '', state: 'running', jobId },
    };
  }
  return { ...prev, [callId]: { ...existing, jobId } };
}

// re-export for convenience when only shell helpers are needed alongside extractToolResultText
export { extractToolResultText };
