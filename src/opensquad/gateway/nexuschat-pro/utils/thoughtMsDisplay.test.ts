/**
 * The deep-think duration comes from the AGENT, not from a guess.
 *
 * See ``tests/test_thought_clock.py`` for the recorder.  Watched here is the
 * display half: a row with a recorded duration shows that number and never
 * ticks, a phase-ending frame freezes the row with the exact total, and the
 * inference stays only as the fallback for old sessions.
 *
 * Mutations verified (applied, run, reverted):
 *   MU1 the reader never finds the field            -> the shape test fails
 *   MU2 the row ignores the recorded value          -> the "does not tick" test fails
 *   MU3 the chunk merge drops the newest value      -> the merge test fails
 *   MU4 the stamp hits the first thought, not the last -> the freeze test fails
 *   MU5 the stamp touches settled blocks too        -> the settled-block test fails
 */
import { describe, expect, it } from 'vitest';
import {
  appendWorkflowEvent,
  genTimelineUID,
  readThoughtMs,
  stampThoughtDuration,
  type TimelineEntry,
  type WorkflowBlock,
  type WorkflowEvent,
} from './aiChatTimeline';
import { buildLines } from '../components/ai-chat/SoloActivityRow';

const t = ((k: string) => k) as never;

function evt(partial: Partial<WorkflowEvent>): WorkflowEvent {
  return { _uid: genTimelineUID(), type: 'thought', content: 'x', timestamp: 1000, ...partial } as WorkflowEvent;
}

function block(events: WorkflowEvent[], completed = false): WorkflowBlock {
  return { events, status: null, completed, started_ms: 1000 };
}

function timelineWith(b: WorkflowBlock): TimelineEntry[] {
  return [{ kind: 'workflow', data: b, _uid: 'w1' }];
}

describe('readThoughtMs', () => {
  it('accepts every shape the field travels in', () => {
    expect(readThoughtMs({ thought_ms: 1200 })).toBe(1200);
    expect(readThoughtMs({ thoughtMs: 1200 })).toBe(1200);
    expect(readThoughtMs({ data: { thought_ms: 1200 } })).toBe(1200);
    expect(readThoughtMs({ content: { thought_ms: 1200 } })).toBe(1200);
  });

  it('ignores absent / nonsensical values instead of rendering NaN', () => {
    expect(readThoughtMs(undefined)).toBeUndefined();
    expect(readThoughtMs('thought')).toBeUndefined();
    expect(readThoughtMs({})).toBeUndefined();
    expect(readThoughtMs({ thought_ms: -5 })).toBeUndefined();
    expect(readThoughtMs({ thought_ms: 'abc' })).toBeUndefined();
  });
});

describe('stampThoughtDuration', () => {
  it('freezes the trailing thought of the live block', () => {
    const tl = timelineWith(block([evt({ content: 'a' }), evt({ content: 'b', timestamp: 2000 })]));
    const out = stampThoughtDuration(tl, 7000);
    const events = (out[0] as Extract<TimelineEntry, { kind: 'workflow' }>).data.events;
    expect(events[0].thoughtMs).toBeUndefined();
    expect(events[1].thoughtMs).toBe(7000);
  });

  it('leaves a settled block alone', () => {
    const tl = timelineWith(block([evt({ content: 'a' })], true));
    expect(stampThoughtDuration(tl, 7000)).toBe(tl);
  });

  it('ignores garbage instead of corrupting the timeline', () => {
    const tl = timelineWith(block([evt({ content: 'a' })]));
    expect(stampThoughtDuration(tl, Number.NaN)).toBe(tl);
    expect(stampThoughtDuration(tl, -1)).toBe(tl);
  });
});

describe('chunk merge keeps the newest duration', () => {
  it('later chunks overwrite, earliest wins only until then', () => {
    const tl = timelineWith(block([]));
    const first = appendWorkflowEvent(tl, evt({ content: 'a', thoughtMs: 100 }), 'Thinking...');
    const second = appendWorkflowEvent(first, evt({ content: 'b', thoughtMs: 900 }), 'Thinking...');
    const events = (second[0] as Extract<TimelineEntry, { kind: 'workflow' }>).data.events;
    expect(events).toHaveLength(1);
    expect(events[0].content).toBe('ab');
    expect(events[0].thoughtMs).toBe(900);
  });
});

describe('the recorded duration is displayed, never inferred', () => {
  it('shows the agent number and does NOT set up a ticking row', () => {
    const b = block([
      evt({ content: 'reasoning', timestamp: 1000, thoughtMs: 12_000 }),
      evt({ type: 'tool_call', content: { id: 'c1', name: 'websearch__search' }, timestamp: 2000 }),
    ]);
    const thought = buildLines(b, {}, t).find((l) => l.kind === 'thought');
    expect(thought?.secondary).toBe('12s');
    // The tick is what "深度思考 4s" growing past the phase came from.
    expect(thought?.elapsedStartMs).toBeUndefined();
    expect(thought?.running).toBeFalsy();
  });

  it('a trailing live thought with a recorded value does not tick either', () => {
    const b = block([evt({ content: 'reasoning', timestamp: 1000, thoughtMs: 3000 })]);
    const thought = buildLines(b, {}, t).find((l) => l.kind === 'thought');
    expect(thought?.secondary).toBe('3s');
    expect(thought?.elapsedStartMs).toBeUndefined();
  });

  it('still infers for sessions recorded before the agent sent the field', () => {
    const b = block([
      evt({ content: 'reasoning', timestamp: 1000 }),
      evt({ type: 'tool_call', content: { id: 'c1', name: 'websearch__search' }, timestamp: 5000 }),
    ]);
    const thought = buildLines(b, {}, t).find((l) => l.kind === 'thought');
    expect(thought?.secondary).toBe('4s');
  });
});
