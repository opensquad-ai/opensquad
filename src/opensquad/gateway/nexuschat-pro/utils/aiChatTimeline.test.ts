import { describe, expect, it } from 'vitest';
import {
  appendWorkflowEvent,
  buildTimelineFromSession,
  mergeOrphanedToolResultsAcrossWorkflows,
  shouldTreatWorkflowComplete,
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

  it('merges tool_result into existing tool_call with the same id (does not drop as duplicate)', () => {
    let timeline: TimelineEntry[] = [];
    timeline = appendWorkflowEvent(timeline, toolCall('t1', 'system__run_session_job'), 'Calling...');
    timeline = appendWorkflowEvent(
      timeline,
      toolResult('t1', 'C:\\ai_work\\pro0\\opensquad_deploy_test'),
      'Done',
    );
    const wf = timeline.find((e) => e.kind === 'workflow');
    expect(wf?.kind).toBe('workflow');
    if (wf?.kind !== 'workflow') return;
    expect(wf.data.events).toHaveLength(1);
    expect(wf.data.events[0].type).toBe('tool_call');
    expect(wf.data.events[0].result).toBe('C:\\ai_work\\pro0\\opensquad_deploy_test');
    expect(wf.data.events[0].resultStatus).toBe('success');
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

  it('does not loosely merge tool_result across a user message when ids differ', () => {
    const timeline: TimelineEntry[] = [
      {
        kind: 'workflow',
        data: {
          events: [toolCall('old-call')],
          status: null,
          completed: true,
        },
        _uid: 'w1',
      },
      {
        kind: 'message',
        data: { role: 'user', content: 'new turn' },
        _uid: 'm1',
      },
      {
        kind: 'workflow',
        data: {
          events: [toolResult('new-call', 'fresh-result')],
          status: null,
          completed: true,
        },
        _uid: 'w2',
      },
    ];
    const merged = mergeOrphanedToolResultsAcrossWorkflows(timeline);
    const workflows = merged.filter((e) => e.kind === 'workflow');
    expect(workflows).toHaveLength(2);
    const firstEvents = workflows[0].kind === 'workflow' ? workflows[0].data.events : [];
    const secondEvents = workflows[1].kind === 'workflow' ? workflows[1].data.events : [];
    expect(firstEvents[0].result).toBeUndefined();
    expect(secondEvents[0].type).toBe('tool_result');
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

  it('keeps thought workflow above assistant reply even when thought was saved later', () => {
    // Mirrors production: ChatAPI api_sync saves the assistant message first,
    // then parse_and_persist_tags writes the thought event with a later ts.
    const messages = [
      { role: 'user', content: '帮我看看', timestamp: '2026-01-01T00:00:00.000Z' },
      {
        role: 'assistant',
        content: '好的，这是回复',
        type: 'api_sync',
        timestamp: '2026-01-01T00:00:02.000Z',
      },
    ];
    const events = [
      {
        type: 'thought',
        data: { text: '先分析需求…' },
        timestamp: '2026-01-01T00:00:03.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    const kinds = tl.map((e) => e.kind);
    expect(kinds).toEqual(['message', 'workflow', 'message']);
    expect(tl[0].kind === 'message' && tl[0].data.role).toBe('user');
    expect(tl[1].kind).toBe('workflow');
    expect(tl[1].kind === 'workflow' && tl[1].data.events.some((ev) => ev.type === 'thought')).toBe(true);
    expect(tl[2].kind === 'message' && tl[2].data.role).toBe('assistant');
  });

  it('keeps same-turn tools AFTER the user even when event timestamps are earlier', () => {
    // Compression / sub-agent races often persist tool events with ts <= user ts.
    const messages = [
      { role: 'user', content: '委托下delegate', timestamp: '2026-07-11T01:34:51.000Z' },
      {
        role: 'assistant',
        content: '这是报告',
        timestamp: '2026-07-11T01:36:00.000Z',
      },
    ];
    const events = [
      {
        type: 'tool_call',
        data: { id: 't1', name: 'filesystem__list_directory', args: '{}' },
        timestamp: '2026-07-11T01:34:50.500Z',
      },
      {
        type: 'tool_result',
        data: { id: 't1', name: 'filesystem__list_directory', result: 'ok' },
        timestamp: '2026-07-11T01:34:50.800Z',
      },
      {
        type: 'thought',
        data: { text: '整理报告…' },
        timestamp: '2026-07-11T01:34:50.900Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    const kinds = tl.map((e) => e.kind);
    expect(kinds).toEqual(['message', 'workflow', 'message']);
    expect(tl[0].kind === 'message' && tl[0].data.role).toBe('user');
    expect(tl[0].kind === 'message' && tl[0].data.content).toBe('委托下delegate');
    expect(tl[1].kind).toBe('workflow');
    expect(tl[2].kind === 'message' && tl[2].data.role).toBe('assistant');
  });

  it('places context_summary fold above the next user, tools still after user', () => {
    const messages = [
      {
        role: 'system',
        type: 'context_summary',
        content: '# Context Summary\n…',
        timestamp: '2026-07-11T01:34:40.000Z',
      },
      { role: 'user', content: '继续', timestamp: '2026-07-11T01:34:51.000Z' },
      { role: 'assistant', content: '好', timestamp: '2026-07-11T01:35:00.000Z' },
    ];
    const events = [
      {
        type: 'tool_call',
        data: { id: 't1', name: 'read_file', args: '{}' },
        timestamp: '2026-07-11T01:34:50.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    expect(tl.map((e) => e.kind)).toEqual(['workflow', 'message', 'workflow', 'message']);
    expect(tl[0].kind === 'workflow' && tl[0].data.events.some((e) => e.type === 'summary_stream')).toBe(true);
    expect(tl[1].kind === 'message' && tl[1].data.role).toBe('user');
    expect(tl[2].kind).toBe('workflow');
    expect(tl[3].kind === 'message' && tl[3].data.role).toBe('assistant');
  });

  it('marks trailing completed summary as completed (no following chat message)', () => {
    const messages = [
      { role: 'user', content: 'compress please', timestamp: '2026-07-11T01:00:00.000Z' },
    ];
    const events = [
      {
        type: 'summary_stream',
        data: { id: 'summary_history', text: '# Context Summary', done: true },
        timestamp: '2026-07-11T01:00:05.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    expect(tl.map((e) => e.kind)).toEqual(['message', 'workflow']);
    expect(tl[1].kind === 'workflow' && tl[1].data.completed).toBe(true);
    expect(tl[1].kind === 'workflow' && tl[1].data.status).toBeNull();
  });
});

describe('shouldTreatWorkflowComplete', () => {
  it('treats done summary as complete even when completed flag is false', () => {
    expect(
      shouldTreatWorkflowComplete({
        completed: false,
        status: 'working',
        events: [
          {
            type: 'summary_stream',
            content: { id: 's', text: 'done', done: true },
            timestamp: 1,
          },
        ],
      }),
    ).toBe(true);
  });

  it('does not treat thought-only live block as complete', () => {
    expect(
      shouldTreatWorkflowComplete({
        completed: false,
        status: 'Thinking...',
        events: [{ type: 'thought', content: 'hmm', timestamp: 1 }],
      }),
    ).toBe(false);
  });
});
