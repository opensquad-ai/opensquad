import { describe, expect, it } from 'vitest';
import {
  appendWorkflowEvent,
  buildTimelineFromSession,
  mergeOrphanedToolResultsAcrossWorkflows,
  timelineHasToolEvent,
  type TimelineEntry,
  type WorkflowEvent,
} from './aiChatTimeline';

function toolCall(id: string, name = 'read_file'): WorkflowEvent {
  return {
    type: 'tool_call',
    content: { id, name, args: {} },
    timestamp: Date.now(),
  };
}

function toolResult(id: string, result = 'ok'): WorkflowEvent {
  return {
    type: 'tool_result',
    content: { id, result },
    timestamp: Date.now(),
  };
}

describe('timelineHasToolEvent / appendWorkflowEvent dedup', () => {
  it('detects an existing tool id in the timeline', () => {
    const timeline: TimelineEntry[] = [
      {
        kind: 'workflow',
        data: {
          events: [{ ...toolCall('t1'), result: 'done', resultStatus: 'success' }],
          status: null,
          completed: true,
        },
        _uid: 'w1',
      },
    ];
    expect(timelineHasToolEvent(timeline, toolCall('t1'))).toBe(true);
    expect(timelineHasToolEvent(timeline, toolResult('t1'))).toBe(true);
    expect(timelineHasToolEvent(timeline, toolCall('t2'))).toBe(false);
  });

  it('skips duplicate tool_call after hydration', () => {
    let timeline: TimelineEntry[] = [];
    timeline = appendWorkflowEvent(timeline, toolCall('t1'), 'Calling...');
    const again = appendWorkflowEvent(timeline, toolCall('t1'), 'Calling...');
    expect(again).toBe(timeline);
    const wf = again.find((e) => e.kind === 'workflow');
    expect(wf?.kind === 'workflow' && wf.data.events).toHaveLength(1);
  });
});

describe('mergeOrphanedToolResultsAcrossWorkflows', () => {
  it('merges orphan tool_result into preceding unmatched tool_call across message boundary', () => {
    const timeline: TimelineEntry[] = [
      {
        kind: 'workflow',
        data: {
          events: [toolCall('t1')],
          status: null,
          completed: true,
        },
        _uid: 'w1',
      },
      {
        kind: 'message',
        data: { role: 'user', content: 'interrupt' },
        _uid: 'm1',
      },
      {
        kind: 'workflow',
        data: {
          events: [toolResult('t1', 'result-text')],
          status: null,
          completed: true,
        },
        _uid: 'w2',
      },
    ];
    const merged = mergeOrphanedToolResultsAcrossWorkflows(timeline);
    const workflows = merged.filter((e) => e.kind === 'workflow');
    expect(workflows).toHaveLength(1);
    const events = workflows[0].kind === 'workflow' ? workflows[0].data.events : [];
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe('tool_call');
    expect(events[0].result).toContain('result-text');
  });
});

describe('buildTimelineFromSession', () => {
  it('returns empty timeline for empty session', () => {
    expect(buildTimelineFromSession([], [])).toEqual([]);
  });

  it('preserves message order and prepends archived section', () => {
    const messages = [
      { role: 'user', content: 'hello', timestamp: '2026-01-01T00:00:00.000Z' },
      { role: 'assistant', content: 'hi', timestamp: '2026-01-01T00:00:01.000Z' },
    ];
    const archived = [
      { role: 'user', content: 'old', timestamp: '2025-12-31T00:00:00.000Z' },
    ];
    const tl = buildTimelineFromSession(messages, [], archived, []);
    expect(tl[0].kind).toBe('archived_section');
    expect(tl.filter((e) => e.kind === 'message')).toHaveLength(2);
    expect(tl.filter((e) => e.kind === 'message').map((e) => (e.kind === 'message' ? e.data.content : ''))).toEqual([
      'hello',
      'hi',
    ]);
  });
});
