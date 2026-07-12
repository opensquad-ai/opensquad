import { describe, expect, it } from 'vitest';
import {
  appendWorkflowEvent,
  buildTimelineFromSession,
  formatUserSkillDisplayContent,
  isToolResultFailure,
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

describe('isToolResultFailure', () => {
  it('detects Plan-mode blocks and Error prefixes', () => {
    expect(isToolResultFailure('Blocked in Plan mode: filesystem__write_file')).toBe(true);
    expect(isToolResultFailure('Error: boom')).toBe(true);
    expect(isToolResultFailure('Cancelled: stopped by user')).toBe(true);
    expect(isToolResultFailure({ status: 'error', message: 'x' })).toBe(true);
    expect(isToolResultFailure('ok wrote file')).toBe(false);
  });
});

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

describe('formatUserSkillDisplayContent', () => {
  it('collapses user_send_skill tags to /name form', () => {
    expect(
      formatUserSkillDisplayContent('<user_send_skill>babysit</user_send_skill>\n\nfix the PR'),
    ).toBe('/babysit fix the PR');
  });

  it('hides expanded SKILL.md bodies leaked into history', () => {
    const expanded = [
      '[User-selected skill: Babysit (`babysit`)]',
      'Follow the skill instructions below to complete the user\'s request.',
      '',
      '----- BEGIN SKILL -----',
      '# Babysit',
      'Do lots of secret skill stuff...',
      '----- END SKILL -----',
      '',
      '[User request]',
      'fix the open PR',
    ].join('\n');
    expect(formatUserSkillDisplayContent(expanded)).toBe('/babysit fix the open PR');
  });
});

describe('buildTimelineFromSession', () => {
  it('returns empty timeline for empty session', () => {
    expect(buildTimelineFromSession([], [])).toEqual([]);
  });

  it('flattens archived messages into the normal timeline (no fold)', () => {
    const messages = [
      { role: 'user', content: 'hello', timestamp: '2026-01-01T00:00:00.000Z' },
      { role: 'assistant', content: 'hi', timestamp: '2026-01-01T00:00:01.000Z' },
    ];
    const archived = [
      { role: 'user', content: 'old', timestamp: '2025-12-31T00:00:00.000Z' },
    ];
    const tl = buildTimelineFromSession(messages, [], archived, []);
    expect(tl.every((e) => e.kind !== 'archived_section')).toBe(true);
    expect(tl.filter((e) => e.kind === 'message')).toHaveLength(3);
    expect(tl.filter((e) => e.kind === 'message').map((e) => (e.kind === 'message' ? e.data.content : ''))).toEqual([
      'old',
      'hello',
      'hi',
    ]);
  });

  it('does not expose expanded skill body after session rebuild', () => {
    const messages = [
      {
        role: 'user',
        content: [
          '[User-selected skill: Babysit (`babysit`)]',
          'Follow the skill instructions below to complete the user\'s request.',
          '',
          '----- BEGIN SKILL -----',
          '# secret skill body',
          '----- END SKILL -----',
          '',
          '[User request]',
          'please help',
        ].join('\n'),
        timestamp: '2026-01-01T00:00:00.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, []);
    expect(tl).toHaveLength(1);
    expect(tl[0].kind).toBe('message');
    if (tl[0].kind === 'message') {
      expect(tl[0].data.content).toBe('/babysit please help');
      expect(tl[0].data.content).not.toContain('BEGIN SKILL');
      expect(tl[0].data.content).not.toContain('secret skill body');
    }
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

describe('appendWorkflowEvent thought coalesce across interleaved scopes', () => {
  it('merges parent thought fragments interrupted by sub-agent tools', () => {
    let timeline: TimelineEntry[] = [];
    timeline = appendWorkflowEvent(
      timeline,
      { type: 'thought', content: 'deer_flow ', timestamp: 1 },
      'Thinking...',
    );
    timeline = appendWorkflowEvent(
      timeline,
      {
        type: 'tool_call',
        content: { id: 's1', name: 'list_dir' },
        timestamp: 2,
        subAgent: true,
        jobId: 'j1',
        subTaskLabel: 'Explore',
      },
      'Calling...',
    );
    timeline = appendWorkflowEvent(
      timeline,
      { type: 'thought', content: '还在探索中', timestamp: 3 },
      'Thinking...',
    );
    const wf = timeline.find((e) => e.kind === 'workflow');
    expect(wf?.kind).toBe('workflow');
    if (wf?.kind !== 'workflow') return;
    const parentThoughts = wf.data.events.filter((e) => e.type === 'thought' && !e.subAgent);
    expect(parentThoughts).toHaveLength(1);
    expect(parentThoughts[0].content).toBe('deer_flow 还在探索中');
  });
});

describe('appendWorkflowEvent routes async sub-agent events across sealed workflows', () => {
  it('appends job_id sub-agent steps into the completed host block after parent to_user_reply', () => {
    const submitAck = JSON.stringify({ job_id: 'job-42', status: 'running', result: null });
    let timeline: TimelineEntry[] = [
      {
        kind: 'workflow',
        data: {
          events: [
            {
              type: 'tool_call',
              content: { id: 'd1', name: 'delegate_task_submit', arguments: { task: 'Explore dir' } },
              timestamp: 1,
              result: submitAck,
              resultStatus: 'success',
              jobId: 'job-42',
            },
            {
              type: 'thought',
              content: 'early step',
              timestamp: 2,
              subAgent: true,
              jobId: 'job-42',
            },
          ],
          status: null,
          completed: true,
        },
        _uid: 'w-host',
      },
      {
        kind: 'message',
        data: { role: 'assistant', content: 'Started exploring in background.' },
        _uid: 'm1',
      },
    ];

    timeline = appendWorkflowEvent(
      timeline,
      {
        type: 'tool_call',
        content: { id: 's2', name: 'filesystem__list_directory', arguments: { path: 'C:\\x' } },
        timestamp: 3,
        subAgent: true,
        jobId: 'job-42',
      },
      'Calling...',
    );
    timeline = appendWorkflowEvent(
      timeline,
      {
        type: 'tool_result',
        content: { id: 's2', result: '{"status":"error","message":"Security Denied"}' },
        timestamp: 4,
        subAgent: true,
        jobId: 'job-42',
      },
      'Done',
    );
    timeline = appendWorkflowEvent(
      timeline,
      {
        type: 'thought',
        content: 'will try another path',
        timestamp: 5,
        subAgent: true,
        jobId: 'job-42',
      },
      'Thinking...',
    );

    const workflows = timeline.filter((e) => e.kind === 'workflow');
    expect(workflows).toHaveLength(1);
    const host = workflows[0];
    expect(host.kind).toBe('workflow');
    if (host.kind !== 'workflow') return;
    expect(host.data.completed).toBe(true);
    const subTools = host.data.events.filter(
      (e) => e.subAgent && (e.type === 'tool_call' || e.type === 'tool_result'),
    );
    // tool_result merges into tool_call → one tool_call with result + one thought after early
    expect(host.data.events.filter((e) => e.subAgent && e.type === 'tool_call')).toHaveLength(1);
    expect(host.data.events.filter((e) => e.subAgent && e.type === 'tool_call')[0].result).toContain(
      'Security Denied',
    );
    expect(host.data.events.filter((e) => e.subAgent && e.type === 'thought')).toHaveLength(2);
    expect(subTools.length).toBeGreaterThanOrEqual(1);
  });

  it('does not treat async submit ack alone as workflow settled', () => {
    const submitAck = JSON.stringify({ job_id: 'job-9', status: 'running' });
    expect(
      shouldTreatWorkflowComplete({
        completed: false,
        status: 'Thinking...',
        events: [
          {
            type: 'tool_call',
            content: { id: 'd1', name: 'delegate_task_submit' },
            timestamp: 1,
            result: submitAck,
            resultStatus: 'success',
            jobId: 'job-9',
          },
        ],
      }),
    ).toBe(false);
  });
});
