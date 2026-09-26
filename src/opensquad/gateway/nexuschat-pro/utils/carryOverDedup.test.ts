/**
 * Behavioural lock for "carry the live workflow into a hydrated timeline".
 *
 * Reported symptom: after a complex task (folded into a 已工作 task_fold on its
 * `to_user_end_task`), sending the next message re-surfaced the *previous*
 * turn's 💡过程输出 rows inside the new turn's fold, sorted above the new
 * 深度思考 row. Disk was clean — each narration was stored once — so the rows
 * were being re-added by the merge.
 *
 * Both carry-over paths filtered carried-over events with
 * `timelineHasToolEvent`, i.e. by tool-call id only. Tool rows were therefore
 * recognised as already-on-disk and dropped, while `process_output` /
 * `thought` / `info` rows (no tool id) were re-pushed unconditionally, and a
 * carried-over event can only attach to the trailing incomplete workflow —
 * the *current* turn's block. `process_output` is the visible half because it
 * is the row that carries the narration text.
 *
 * Covered here:
 *   T1  `timelineHasWorkflowEvent` sees events nested inside a task_fold;
 *   T2  it distinguishes two different narrations;
 *   T3  it still recognises tool events (callers use one check for both);
 *   T4  `mergeCompressionHydration` drops already-persisted rows entirely;
 *   T5  a genuinely new live event still carries over (no over-correction);
 *   T6  the full-replace hydrate path (hook) dedups before re-appending.
 *
 * Mutations verified:
 *   · dropping the `!timelineHasWorkflowEvent` filter in
 *     `mergeCompressionHydration` (`filteredEvents = wf.events`) → T4 + T5
 *     report 2 copies of the narration;
 *   · restoring the tool-only filter (the reported bug) → T5 drops the new
 *     thought, because the block looks "all old" yet its non-tool rows were
 *     kept whenever one tool row happened to be new;
 *   · making `timelineHasWorkflowEvent` non-recursive → T1;
 *   · dropping the check from the hook's carry-over loop → T6.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  timelineHasWorkflowEvent,
  type TimelineEntry,
  type WorkflowEvent,
} from './aiChatTimeline';
import { mergeCompressionHydration } from './agentWebChatHelpers';

const proc = (text: string, ts = 1000): WorkflowEvent => ({
  _uid: `p-${text}`,
  type: 'process_output',
  content: text,
  timestamp: ts,
});
const thought = (text: string, ts = 2000): WorkflowEvent => ({
  _uid: `t-${text}`,
  type: 'thought',
  content: text,
  timestamp: ts,
});
const call = (id: string): WorkflowEvent => ({
  _uid: `c-${id}`,
  type: 'tool_call',
  content: { id, name: 'system__run_session_job' },
  timestamp: 3000,
});

const wf = (events: WorkflowEvent[], completed: boolean): TimelineEntry => ({
  kind: 'workflow',
  data: { events, status: null, completed },
  _uid: `wf-${events.map((e) => e._uid).join(',')}`,
});

const msg = (role: 'user' | 'assistant', content: string): TimelineEntry => ({
  kind: 'message',
  data: { role, content },
  _uid: `m-${role}-${content}`,
});

const fold = (entries: TimelineEntry[]): TimelineEntry => ({
  kind: 'task_fold',
  data: { messageCount: 0, eventCount: 0, entries, collapsed: true },
  _uid: 'fold',
});

const countProcessOutputs = (timeline: TimelineEntry[]): number => {
  let n = 0;
  const walk = (entries: TimelineEntry[]) => {
    for (const e of entries) {
      if (e.kind === 'task_fold') {
        walk(e.data.entries);
        continue;
      }
      if (e.kind !== 'workflow') continue;
      n += e.data.events.filter((v) => v.type === 'process_output').length;
    }
  };
  walk(timeline);
  return n;
};

const NARRATION = '提示区核完：沪电股份、兴森科技是真 maker。';

describe('carry-over dedup', () => {
  const persisted: TimelineEntry[] = [
    msg('user', '找出和 PCB 关联的成长股'),
    fold([
      wf([proc(NARRATION), thought('核对提示区'), call('call-1')], false),
    ]),
    msg('assistant', 'PCB 关联股挖掘完成'),
  ];

  it('T1 — finds an event nested inside a task_fold', () => {
    expect(timelineHasWorkflowEvent(persisted, proc(NARRATION))).toBe(true);
    expect(timelineHasWorkflowEvent(persisted, thought('核对提示区'))).toBe(true);
  });

  it('T2 — two different narrations are not the same event', () => {
    expect(timelineHasWorkflowEvent(persisted, proc('另一条完全不同的过程输出'))).toBe(false);
  });

  it('T3 — tool events are recognised by the same check', () => {
    expect(timelineHasWorkflowEvent(persisted, call('call-1'))).toBe(true);
    expect(timelineHasWorkflowEvent(persisted, call('call-9'))).toBe(false);
  });

  it('T4 — an already-persisted live block contributes nothing', () => {
    // The live block is the one still open in the UI when the snapshot lands:
    // same rows, `completed: false`.
    const live: TimelineEntry[] = [
      ...persisted,
      msg('user', 'Commit & Push'),
      wf([proc(NARRATION), thought('核对提示区'), call('call-1')], false),
    ];

    const merged = mergeCompressionHydration(live, persisted);

    expect(countProcessOutputs(merged)).toBe(1);
  });

  it('T5 — a genuinely new live event still carries over', () => {
    const live: TimelineEntry[] = [
      ...persisted,
      msg('user', 'Commit & Push'),
      wf([proc(NARRATION), thought('关于 Commit & Push 的新思考')], false),
    ];

    const merged = mergeCompressionHydration(live, persisted);
    const carried = merged.filter((e) => e.kind === 'workflow');
    const texts = carried.flatMap((e) =>
      (e as Extract<TimelineEntry, { kind: 'workflow' }>).data.events.map((v) => String(v.content)),
    );

    expect(countProcessOutputs(merged)).toBe(1);
    expect(texts).toContain('关于 Commit & Push 的新思考');
  });

  it('T6 — the full-replace hydrate carries live events over deduped', () => {
    // This loop is the one that produced the reported rows: the snapshot has no
    // incomplete block, so every event of every still-open live block is
    // re-appended — and a re-appended event can only land in the trailing
    // block, i.e. the current turn's fold.
    const HOOK = fs.readFileSync(path.join(__dirname, '..', 'hooks', 'useAgentWebSocket.ts'), 'utf8');
    const at = HOOK.indexOf('const liveWfs = prev.filter(');
    expect(at).toBeGreaterThan(-1);
    const loop = HOOK.slice(at, at + 1600);
    expect(loop).toMatch(/appendWorkflowEvent\(/);
    expect(loop).toMatch(/timelineHasWorkflowEvent\(merged, evt\)/);
  });
});
