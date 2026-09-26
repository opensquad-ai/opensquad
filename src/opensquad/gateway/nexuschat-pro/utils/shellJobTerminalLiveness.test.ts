/**
 * Lock: ONE shell call ⇒ ONE terminal, and a terminal must retire itself.
 *
 * Two user-visible failures motivated this file:
 *
 *  1. The composer's "N terminals running" pill grew without bound and listed
 *     jobs with hours of elapsed time. Cause: an unsealed shell call (no
 *     `tool_result` — user re-sent the turn, aborted, process died) was read as
 *     "still running" forever, because liveness was inferred from ABSENCE with
 *     no bound. Proven against a real session: the single unpaired
 *     `system__run_session_job` call there matched the pill's oldest row
 *     (`1h 56m 45s`) exactly.
 *
 *  2. One call rendered as TWO CMD folds — a "Waiting for output…" one plus the
 *     real one. Cause: a `tool_call_delta` argument-streaming preview
 *     (`partial: true`, id `partial_tc_<n>` or the constant `xml_preview_open`)
 *     was classified as a shell job. No `tool_result` can ever carry a
 *     preview's id, so it could never be sealed: it lingered as a phantom fold
 *     AND inflated the pill.
 *
 * Both reduce to one rule, enforced below: liveness is decided exactly ONCE
 * (refreshShellBundle) and "no result" only means "running" while the owning
 * turn is still alive. The one explicit exception is start_job's
 * `completed:false` ack, which by design outlives the turn.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import type { WorkflowEvent, WorkflowBlock, TimelineEntry } from './aiChatTimeline';
import { appendWorkflowEvent, mergeToolResultIntoTimeline } from './aiChatTimeline';
import {
  attachShellJobsToDisplayItems,
  collectRunningShellJobs,
  isShellJobToolCall,
  type ShellJobBundle,
} from './shellJobGrouping';
import { buildDisplayWorkflowItems } from './delegateGrouping';

const RUN = 'system__run_session_job';
const START = 'system__start_job';
const ARGS = (d: string) =>
  JSON.stringify({ command: `cmd-${d}`, description: `desc-${d}`, timeout: 120 });

/** A `tool_call_delta` frame: streaming args preview, never a real call. */
function delta(id: string, index: number, d: string, name = RUN): WorkflowEvent {
  return {
    type: 'tool_call',
    content: { id, index, name, arguments: ARGS(d), args: ARGS(d), partial: true },
    timestamp: 1,
  } as WorkflowEvent;
}
function finalCall(id: string, d: string, name = RUN): WorkflowEvent {
  return { type: 'tool_call', content: { id, name, args: ARGS(d) }, timestamp: 2 } as WorkflowEvent;
}
function res(id: string, text = 'ok', name = RUN): WorkflowEvent {
  return { type: 'tool_result', content: { id, name, result: text }, timestamp: 3 } as WorkflowEvent;
}

/** Every shell fold on the timeline + the contents of the running pill. */
function survey(tl: TimelineEntry[]) {
  const folds: ShellJobBundle[] = [];
  for (const e of tl) {
    if (e.kind !== 'workflow') continue;
    const block = e.data as WorkflowBlock;
    const items = attachShellJobsToDisplayItems(
      buildDisplayWorkflowItems(block.events),
      {},
      !!block.completed,
    );
    for (const it of items) if (it.kind === 'shell_job') folds.push(it.bundle as ShellJobBundle);
  }
  return { folds, running: collectRunningShellJobs(tl as any, {}) };
}

/** The turn ends: the owning workflow block is sealed. */
function sealAll(tl: TimelineEntry[]): TimelineEntry[] {
  return tl.map((e) =>
    e.kind === 'workflow' ? { ...e, data: { ...(e.data as WorkflowBlock), completed: true } } : e,
  );
}

describe('shell terminal liveness', () => {
  it('never classifies a streaming args preview as a shell job', () => {
    expect(isShellJobToolCall(delta('partial_tc_0', 0, 'A'))).toBe(false);
    expect(isShellJobToolCall(delta('xml_preview_open', 0, 'A', 'system__start_job'))).toBe(false);
    expect(isShellJobToolCall(finalCall('call_A_0', 'A'))).toBe(true);
  });

  it('two parallel calls ⇒ two folds, none running', () => {
    let tl: TimelineEntry[] = [];
    tl = appendWorkflowEvent(tl, delta('partial_tc_0', 0, 'A'), 'w');
    tl = appendWorkflowEvent(tl, delta('partial_tc_1', 1, 'B'), 'w');
    tl = appendWorkflowEvent(tl, finalCall('call_A_0', 'A'), 'c');
    tl = appendWorkflowEvent(tl, finalCall('call_B_1', 'B'), 'c');
    tl = mergeToolResultIntoTimeline(tl, res('call_A_0'), null) ?? tl;
    tl = mergeToolResultIntoTimeline(tl, res('call_B_1'), null) ?? tl;
    const s = survey(sealAll(tl));
    expect(s.folds).toHaveLength(2);
    expect(s.running).toHaveLength(0);
  });

  it('a preview carrying the real call id still yields one fold', () => {
    let tl: TimelineEntry[] = [];
    tl = appendWorkflowEvent(tl, delta('call_A_0', 0, 'A'), 'w');
    tl = appendWorkflowEvent(tl, finalCall('call_A_0', 'A'), 'c');
    tl = mergeToolResultIntoTimeline(tl, res('call_A_0'), null) ?? tl;
    const s = survey(sealAll(tl));
    expect(s.folds).toHaveLength(1);
    expect(s.running).toHaveLength(0);
  });

  it('a late preview after the final call does not add a second fold', () => {
    let tl: TimelineEntry[] = [];
    tl = appendWorkflowEvent(tl, delta('partial_tc_0', 0, 'A'), 'w');
    tl = appendWorkflowEvent(tl, finalCall('call_A_0', 'A'), 'c');
    tl = appendWorkflowEvent(tl, delta('partial_tc_0', 0, 'A'), 'w');
    tl = mergeToolResultIntoTimeline(tl, res('call_A_0'), null) ?? tl;
    const s = survey(sealAll(tl));
    expect(s.folds).toHaveLength(1);
    expect(s.running).toHaveLength(0);
  });

  it('a block sealed between preview and final call still yields one fold', () => {
    let tl: TimelineEntry[] = [];
    tl = appendWorkflowEvent(tl, delta('partial_tc_0', 0, 'A'), 'w');
    tl = sealAll(tl);
    tl = appendWorkflowEvent(tl, finalCall('call_A_0', 'A'), 'c');
    tl = mergeToolResultIntoTimeline(tl, res('call_A_0'), null) ?? tl;
    const s = survey(sealAll(tl));
    expect(s.folds).toHaveLength(1);
    expect(s.running).toHaveLength(0);
  });

  it('a call with no result runs while live, then retires as interrupted', () => {
    let tl: TimelineEntry[] = [];
    tl = appendWorkflowEvent(tl, finalCall('call_1941_system__run_session_job_0', 'A'), 'c');

    const live = survey(tl);
    expect(live.folds).toHaveLength(1);
    expect(live.running).toHaveLength(1); // genuinely executing — must stay tracked
    expect(live.folds[0].interrupted).toBe(false);

    const ended = survey(sealAll(tl));
    expect(ended.folds).toHaveLength(1);
    expect(ended.running).toHaveLength(0); // the phantom is gone
    expect(ended.folds[0].interrupted).toBe(true);
    expect(ended.folds[0].running).toBe(false);
  });

  it('a genuinely background job stays tracked after the turn ends', () => {
    const ack = JSON.stringify({ status: 'success', completed: false, job_id: 'job9' });
    let tl: TimelineEntry[] = [];
    tl = appendWorkflowEvent(tl, finalCall('call_bg_0', 'bg', START), 'c');
    tl = mergeToolResultIntoTimeline(tl, res('call_bg_0', ack, START), null) ?? tl;
    const s = survey(sealAll(tl));
    expect(s.running).toHaveLength(1);
    expect(s.folds[0].interrupted).toBe(false);
  });

  it('sealing retires the orphan but keeps the still-acked background job', () => {
    const ack = JSON.stringify({ status: 'success', completed: false, job_id: 'job9' });
    let tl: TimelineEntry[] = [];
    tl = appendWorkflowEvent(tl, finalCall('call_bg_0', 'bg', START), 'c');
    tl = mergeToolResultIntoTimeline(tl, res('call_bg_0', ack, START), null) ?? tl;
    tl = appendWorkflowEvent(tl, finalCall('call_orphan_0', 'orphan'), 'c');
    const s = survey(sealAll(tl));
    expect(s.running.map((j) => j.id)).toEqual(['call_bg_0']);
  });
});

describe('shell terminal liveness: source fences', () => {
  const ROOT = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'));
  const read = (rel: string) =>
    fs
      .readFileSync(path.join(ROOT, rel), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');

  it('the running-terminals collector has no second liveness formula', () => {
    const src = read('shellJobGrouping.ts');
    const start = src.indexOf('function collectRunningShellJobsFromEntries');
    expect(start).toBeGreaterThan(-1);
    // Anchor on the NEXT declaration, not on a comment: `read` strips comments.
    const end = src.indexOf('export function shellJobDoneLabel', start);
    expect(end).toBeGreaterThan(start);
    const body = src.slice(start, end);
    // Liveness comes from the bundle (single source of truth)…
    expect(body).toContain('if (!b.running) continue;');
    // …and is never re-derived from the raw stream here. The stream map is
    // never pruned, so re-reading it resurrects retired phantoms.
    expect(body).not.toContain('stream?.state === ');
    expect(body).not.toMatch(/const running\s*=/);
  });

  it('a streaming preview is filtered in exactly one place', () => {
    const src = read('shellJobGrouping.ts');
    expect(src).toMatch(/export function isPartialToolCallEvent/);
    // The shell-job classifier must consult it, otherwise previews become folds.
    const fn = src.slice(src.indexOf('export function isShellJobToolCall'));
    const body = fn.slice(0, fn.indexOf('}'));
    expect(body).toContain('isPartialToolCallEvent(evt)');
  });

  it('the "no result" running inference is gated on the turn being live', () => {
    const src = read('shellJobGrouping.ts');
    const fn = src.slice(src.indexOf('function refreshShellBundle'));
    const body = fn.slice(0, fn.indexOf('\n}'));
    expect(body).toContain('!turnCompleted && (streamRunning || !result)');
    expect(body).toContain('const interrupted = turnCompleted && !result && !streamDone');
  });

  it('the fold never resurrects running from a stale stream', () => {
    const src = read('../components/ai-chat/ShellJobFold.tsx');
    const fn = src.slice(src.indexOf('function mergeBundle'));
    const body = fn.slice(0, fn.indexOf('\n}'));
    expect(body).toContain('bundle.interrupted');
    expect(body).toMatch(/streamDone \|\| interrupted \? false/);
  });
});
