import { describe, expect, it } from 'vitest';
import {
  appendWorkflowEvent,
  appendWorkflowEvents,
  absorbAssistantFinalText,
  appendLiveWorkflowBatch,
  buildTimelineFromSession,
  CANCELLED_OPEN_TOOL_RESULT,
  composeAssistantDisplayContent,
  detectCancelledTurn,
  extractLiveToolCallFromMarkup,
  foldTaskProcessSinceLastUser,
  formatUserSkillDisplayContent,
  genTimelineUID,
  LIVE_XML_TOOL_ID,
  isToolResultFailure,
  mergeOrphanedToolResultsAcrossWorkflows,
  rebaseTimelineUids,
  sealIncompleteWorkflows,
  shouldTreatWorkflowComplete,
  stripToolCallMarkup,
  timelineHasToolEvent,
  timelineHasVisibleChatContent,
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

  it('does not treat successful payload bodies as failures', () => {
    // Successful read_file content that happens to mention failure keywords.
    const fileBody = [
      'function handle() {',
      '  if (failed) return;',
      '  return { "status": "error", "message": "demo" };',
      '}',
    ].join('\n');
    expect(isToolResultFailure(fileBody)).toBe(false);
    expect(
      isToolResultFailure({
        id: 'c1',
        name: 'filesystem__read_file',
        result: fileBody,
      }),
    ).toBe(false);
    expect(
      isToolResultFailure({
        status: 'ok',
        content: fileBody,
      }),
    ).toBe(false);
    expect(
      isToolResultFailure(
        JSON.stringify({ status: 'ok', content: 'has failed and "status": "error" inside' }),
      ),
    ).toBe(false);
  });

  it('still detects structured / prefixed failure envelopes', () => {
    expect(isToolResultFailure('Failed to open path')).toBe(true);
    expect(isToolResultFailure('Security Denied: Path outside project.')).toBe(true);
    expect(isToolResultFailure('{"status":"error","message":"Security Denied"}')).toBe(true);
    expect(isToolResultFailure({ status: 'failed', message: 'nope' })).toBe(true);
    expect(isToolResultFailure({ error: 'boom' })).toBe(true);
    expect(isToolResultFailure({ aborted: true })).toBe(true);
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

describe('composeAssistantDisplayContent', () => {
  it('keeps report body outside to_user plus the coda', () => {
    const body = '# Weekly report\n\n' + 'market data line.\n'.repeat(10);
    const raw = `${body}\n\n<to_user>\nAbove is the full report.\n</to_user>`;
    const out = composeAssistantDisplayContent(raw);
    expect(out).toContain('Weekly report');
    expect(out).toContain('Above is the full report');
    expect(out).not.toContain('<to_user>');
  });

  it('strips DSML tool-call markup leaked into the assistant body', () => {
    const raw =
      '<\uFF5C\uFF5Ctool_calls>\n' +
      '<\uFF5C\uFF5Cinvoke name="anysearch">\n' +
      '<\uFF5C\uFF5Cparameter name="query">\u798F\u5DDE\u4ECA\u5929\u5929\u6C14</\uFF5C\uFF5Cparameter>\n' +
      '</\uFF5C\uFF5Cinvoke>\n' +
      '</\uFF5C\uFF5Ctool_calls>\n\n' +
      '<to_user>\u62B1\u6B49\uFF0C\u67E5\u4E0D\u5230\u5929\u6C14\u3002</to_user>';
    const out = composeAssistantDisplayContent(raw);
    expect(out).not.toContain('tool_calls');
    expect(out).not.toContain('invoke');
    expect(out).not.toContain('parameter');
    expect(out).not.toContain('anysearch');
    expect(out).toContain('\u62B1\u6B49');
  });

  it('strips unclosed <tool_call> and leaked websearch skeletons', () => {
    const unclosed =
      'Let me search.\n<tool_call>\n<func>websearch</func>\n<query>\u798F\u5DDE\u5929\u6C14</query>';
    expect(composeAssistantDisplayContent(unclosed)).toBe('Let me search.');
    expect(
      composeAssistantDisplayContent('websearch\nquery\n\u798F\u5DDE\u5929\u6C14'),
    ).toBe('');
  });

  it('strips orphan DSML close tags that markdown would render as a table', () => {
    expect(stripToolCallMarkup('</||DSML||calls>').trim()).toBe('');
    expect(stripToolCallMarkup('hello\n</||DSML||calls>\n').trim()).toBe('hello');
    const fw = `</\uFF5C\uFF5CDSML\uFF5C\uFF5C calls>`;
    const out = composeAssistantDisplayContent(fw);
    expect(out).not.toMatch(/DSML/i);
    expect(out).not.toMatch(/\bcalls\b/i);
  });

  it('strips <plan> checklist so refresh does not dump it as chat', () => {
    const raw = [
      '<plan>',
      '- [x] 摸清项目结构与技术栈',
      '- [>] 后台跑前端类型检查',
      '- [ ] 审查 Web 对话渲染',
      '</plan>',
      '<to_user>开始执行第二项。</to_user>',
    ].join('\n');
    const out = composeAssistantDisplayContent(raw);
    expect(out).toContain('开始执行第二项');
    expect(out).not.toContain('摸清项目结构');
    expect(out).not.toContain('<plan>');
  });

  it('hides untagged plan-only assistant dumps', () => {
    const raw = [
      '- 摸清项目结构与技术栈 [x]',
      '- 后台跑前端类型检查 + 单测 (tsc / vitest) [>]',
      '- 审查 Web 对话渲染相关代码 [ ]',
    ].join('\n');
    expect(composeAssistantDisplayContent(raw)).toBe('');
  });

  it('strips protocol tags including inner text so timeout does not become "60"', () => {
    expect(composeAssistantDisplayContent('<timeout>60</timeout>')).toBe('');
    expect(composeAssistantDisplayContent('<timeout>60</timeout>\n')).toBe('');
    expect(composeAssistantDisplayContent('<Timeout>60</Timeout>')).toBe('');
    expect(composeAssistantDisplayContent('hello\n<timeout>60</timeout>')).toBe('hello');
    expect(composeAssistantDisplayContent('<to_user>开始执行。</to_user>\n<timeout>60</timeout>')).toBe(
      '开始执行。',
    );
    expect(composeAssistantDisplayContent('<sleep>5</sleep>')).toBe('');
    expect(composeAssistantDisplayContent('<to_system>task_complete</to_system>')).toBe('');
    expect(composeAssistantDisplayContent('<wake>now</wake>')).toBe('');
    expect(composeAssistantDisplayContent('<state>idle</state>')).toBe('');
    expect(composeAssistantDisplayContent('<timeout>60')).toBe('');
  });

  it('strips namespaced tool-as-tag XML so refresh does not dump shell commands', () => {
    const leaked = [
      '<system.run_session_job>',
      'copy /Y src\\\\purify.min.js .tmpscratch\\\\purify.min.js',
      '</system.run_session_job>',
      'findstr /s /i /m "dangerouslySetInnerHTML" src\\\\components\\\\*.tsx',
      '</system.run_session_job>',
    ].join('\n');
    expect(composeAssistantDisplayContent(leaked)).toBe('');
    expect(
      composeAssistantDisplayContent(
        '<to_user>已完成检查。</to_user>\n<system.run_session_job>git status</system.run_session_job>',
      ),
    ).toBe('已完成检查。');
    expect(
      composeAssistantDisplayContent('<filesystem.read_file>\npath: foo.ts\n</filesystem.read_file>'),
    ).toBe('');
  });

  it('strips dots_function_call blocks so refresh does not dump tool XML', () => {
    const raw = [
      '<dots_function_call>',
      '<invoke name="mcp__filesystem__directory_tree">',
      '<parameter name="path">.</parameter>',
      '</invoke>',
      '</dots_function_call>',
    ].join('\n');
    expect(composeAssistantDisplayContent(raw)).toBe('');
    expect(
      composeAssistantDisplayContent(`<to_user>开始检查。</to_user>\n${raw}`),
    ).toBe('开始检查。');
  });
});

describe('buildTimelineFromSession', () => {
  it('returns empty timeline for empty session', () => {
    expect(buildTimelineFromSession([], [])).toEqual([]);
  });

  it('moves persisted <plan> from assistant text into the workflow fold', () => {
    const messages = [
      { role: 'user', content: '检查项目', timestamp: '2026-01-01T00:00:00.000Z' },
      {
        role: 'assistant',
        content:
          '<plan>\n- [x] 摸清项目结构与技术栈\n- [>] 跑单测\n- [ ] 汇总\n</plan>\n<to_user>已列出计划。</to_user>',
        timestamp: '2026-01-01T00:00:02.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, []);
    const assistant = tl.filter(
      (e): e is Extract<TimelineEntry, { kind: 'message' }> =>
        e.kind === 'message' && e.data.role === 'assistant',
    );
    expect(assistant).toHaveLength(1);
    expect(String(assistant[0].data.content)).toContain('已列出计划');
    expect(String(assistant[0].data.content)).not.toContain('摸清项目结构');
    const planEvents = tl
      .filter((e) => e.kind === 'workflow')
      .flatMap((e) => e.data.events.filter((ev) => ev.type === 'plan'));
    expect(planEvents.length).toBeGreaterThan(0);
  });

  it('does not render timeout-only assistant messages as chat', () => {
    const messages = [
      { role: 'user', content: '继续', timestamp: '2026-09-10T11:09:00.000Z' },
      {
        role: 'assistant',
        content: '<timeout>60</timeout>',
        timestamp: '2026-09-10T11:10:00.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, []);
    const assistant = tl.filter(
      (e): e is Extract<TimelineEntry, { kind: 'message' }> =>
        e.kind === 'message' && e.data.role === 'assistant',
    );
    expect(assistant).toHaveLength(0);
  });

  it('does not render namespaced tool XML as a chat bubble after refresh', () => {
    const messages = [
      { role: 'user', content: '检查', timestamp: '2026-09-11T10:00:00.000Z' },
      {
        role: 'assistant',
        content:
          '<system.run_session_job>\ncopy /Y purify.min.js .tmp\n</system.run_session_job>\nfindstr foo\n</system.run_session_job>',
        timestamp: '2026-09-11T10:00:02.000Z',
      },
    ];
    const events = [
      {
        type: 'tool_call',
        data: { id: 'c1', name: 'system.run_session_job', arguments: 'copy /Y purify.min.js .tmp' },
        timestamp: '2026-09-11T10:00:01.000Z',
      },
      {
        type: 'tool_result',
        data: { id: 'c1', name: 'system.run_session_job', result: 'ok' },
        timestamp: '2026-09-11T10:00:01.500Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    const assistant = tl.filter(
      (e): e is Extract<TimelineEntry, { kind: 'message' }> =>
        e.kind === 'message' && e.data.role === 'assistant',
    );
    expect(assistant).toHaveLength(0);
    expect(JSON.stringify(assistant)).not.toContain('copy /Y purify');
    const tools = tl
      .filter((e) => e.kind === 'workflow')
      .flatMap((e) => e.data.events.filter((ev) => ev.type === 'tool_call'));
    expect(tools.length).toBeGreaterThan(0);
  });

  it('does not duplicate plan when session events already have one', () => {
    const messages = [
      { role: 'user', content: '检查项目', timestamp: '2026-01-01T00:00:00.000Z' },
      {
        role: 'assistant',
        content: '- 摸清项目结构 [x]\n- 跑单测 [>]\n- 汇总 [ ]',
        timestamp: '2026-01-01T00:00:02.000Z',
      },
    ];
    const events = [
      {
        type: 'plan',
        data: { text: '- [x] 摸清项目结构\n- [>] 跑单测\n- [ ] 汇总' },
        timestamp: '2026-01-01T00:00:01.500Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    const planEvents = tl
      .filter((e) => e.kind === 'workflow')
      .flatMap((e) => e.data.events.filter((ev) => ev.type === 'plan'));
    expect(planEvents.length).toBe(1);
    const assistant = tl.filter(
      (e): e is Extract<TimelineEntry, { kind: 'message' }> =>
        e.kind === 'message' && e.data.role === 'assistant',
    );
    expect(assistant.length).toBe(0);
  });

  it('skips role=tool messages (tool IO is LLM context, rendered via workflow events)', () => {
    const messages = [
      { role: 'user', content: 'hi', timestamp: '2026-01-01T00:00:00.000Z' },
      { role: 'assistant', content: 'working...', timestamp: '2026-01-01T00:00:01.000Z' },
      { role: 'tool', content: "{'status': 'success', 'content': '1: file line'}", timestamp: '2026-01-01T00:00:02.000Z' },
      { role: 'assistant', content: 'done', timestamp: '2026-01-01T00:00:03.000Z' },
    ];
    const tl = buildTimelineFromSession(messages, []);
    const messageEntries = tl.filter((e: any) => e.kind === 'message');
    expect(messageEntries.length).toBe(3); // user + 2 assistant, no tool bubble
    const leaked = messageEntries.some(
      (e: any) => e.data && String(e.data.content || '').includes('file line'),
    );
    expect(leaked).toBe(false);
  });

  it('drops a second message with the same message_id (no duplicate React key)', () => {
    // Same message_id arriving twice (optimistic echo + disk snapshot, or a
    // compression overlap) must render once — _uid is derived from the id and
    // duplicates would produce two children with the same React key.
    const messages = [
      { role: 'user', content: '创建项目', timestamp: '2026-01-01T00:00:00.000Z', client_id: 'dup-uuid', message_id: 'dup-uuid' },
      { role: 'assistant', content: '好的', timestamp: '2026-01-01T00:00:02.000Z' },
      { role: 'user', content: '创建项目', timestamp: '2026-01-01T00:00:04.000Z', client_id: 'dup-uuid', message_id: 'dup-uuid' },
    ];
    const tl = buildTimelineFromSession(messages, []);
    const userEntries = tl.filter(
      (e) => e.kind === 'message' && e.data.role === 'user',
    );
    expect(userEntries).toHaveLength(1);
    const uids = tl.map((e) => e._uid);
    expect(new Set(uids).size).toBe(uids.length);
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

  it('keeps in-flight tools after last progress (long Agent Web turn mid-run)', () => {
    // Disk refresh while websearch is still open must not seal as "Worked"
    // above / inside the last to_user progress bubble.
    const messages = [
      { role: 'user', content: '报告今日行情', timestamp: '2026-07-31T04:00:00.000Z' },
      {
        role: 'assistant',
        content: '先看昨日复盘，再取今日早盘',
        timestamp: '2026-07-31T04:05:00.000Z',
      },
    ];
    const events = [
      {
        type: 'thought',
        data: { text: 'planning…' },
        timestamp: '2026-07-31T04:00:10.000Z',
      },
      {
        type: 'tool_call',
        data: { id: 'w1', name: 'websearch__search', args: '{}' },
        timestamp: '2026-07-31T04:05:30.000Z',
      },
      {
        type: 'thought',
        data: { text: 'late thought after api_sync' },
        timestamp: '2026-07-31T04:05:05.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    const kinds = tl.map((e) => e.kind);
    expect(kinds[0]).toBe('message');
    expect(tl.some((e) => e.kind === 'workflow' && !e.data.completed)).toBe(true);
    const progressIdx = tl.findIndex(
      (e) => e.kind === 'message' && String(e.data.content).includes('昨日复盘'),
    );
    const openWfIdx = tl.findIndex(
      (e) =>
        e.kind === 'workflow'
        && !e.data.completed
        && e.data.events.some(
          (ev) =>
            ev.type === 'tool_call'
            && !ev.result
            && String((ev.content as any)?.name || '').includes('websearch'),
        ),
    );
    expect(progressIdx).toBeGreaterThanOrEqual(0);
    expect(openWfIdx).toBeGreaterThan(progressIdx);
    // Late thought still sits above the progress reply (api_sync race).
    const thoughtBefore = tl
      .slice(0, progressIdx)
      .some(
        (e) =>
          e.kind === 'workflow'
          && e.data.events.some((ev) => String(ev.content || '').includes('late thought')),
      );
    expect(thoughtBefore).toBe(true);
  });

  it('seals stopped turns after refresh even when tools never returned', () => {
    const messages = [
      { role: 'user', content: '搜一下', timestamp: '2026-09-10T08:00:00.000Z' },
    ];
    const events = [
      {
        type: 'tool_call',
        data: { id: 't1', name: 'filesystem__search_files', args: '{}' },
        timestamp: '2026-09-10T08:00:10.000Z',
      },
      {
        type: 'turn_summary',
        data: { reason: 'user_stop', elapsed_ms: 351000 },
        timestamp: '2026-09-10T08:05:51.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    const wfs = tl.filter((e) => e.kind === 'workflow');
    expect(wfs.length).toBeGreaterThan(0);
    expect(wfs.every((e) => e.kind === 'workflow' && e.data.completed)).toBe(true);
    expect(tl.some((e) => e.kind === 'workflow' && !e.data.completed)).toBe(false);
    const open = wfs.flatMap((e) =>
      e.kind === 'workflow'
        ? e.data.events.filter((ev) => ev.type === 'tool_call' && !ev.result)
        : [],
    );
    expect(open).toHaveLength(0);
    const cancelled = wfs.flatMap((e) =>
      e.kind === 'workflow'
        ? e.data.events.filter(
          (ev) =>
            ev.type === 'tool_call'
            && String(ev.result || '').includes('Cancelled'),
        )
        : [],
    );
    expect(cancelled.length).toBeGreaterThan(0);
    expect(cancelled[0].result).toBe(CANCELLED_OPEN_TOOL_RESULT);
    const elapsed = wfs[0].kind === 'workflow' ? wfs[0].data.elapsed_ms : undefined;
    expect(elapsed).toBe(351000);
  });

  it('seals from [Stopped] assistant text when turn_summary is missing', () => {
    const messages = [
      { role: 'user', content: '继续', timestamp: '2026-09-10T08:00:00.000Z' },
      { role: 'assistant', content: '[Stopped]', timestamp: '2026-09-10T08:01:00.000Z' },
    ];
    const events = [
      {
        type: 'tool_call',
        data: { id: 't1', name: 'websearch__search', args: '{}' },
        timestamp: '2026-09-10T08:00:20.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    expect(tl.some((e) => e.kind === 'workflow' && !e.data.completed)).toBe(false);
    const tools = tl
      .filter((e) => e.kind === 'workflow')
      .flatMap((e) => e.data.events.filter((ev) => ev.type === 'tool_call'));
    expect(tools.every((ev) => !!ev.result)).toBe(true);
  });

  it('does not seal a new turn from an older user_stop summary', () => {
    const messages = [
      { role: 'user', content: '上一轮', timestamp: '2026-09-10T08:00:00.000Z' },
      { role: 'user', content: '新一轮还在跑', timestamp: '2026-09-10T09:00:00.000Z' },
    ];
    const events = [
      {
        type: 'turn_summary',
        data: { reason: 'user_stop', elapsed_ms: 1000 },
        timestamp: '2026-09-10T08:01:00.000Z',
      },
      {
        type: 'tool_call',
        data: { id: 't2', name: 'websearch__search', args: '{}' },
        timestamp: '2026-09-10T09:00:10.000Z',
      },
    ];
    expect(detectCancelledTurn(messages, events).cancelled).toBe(false);
    const tl = buildTimelineFromSession(messages, events);
    expect(tl.some((e) => e.kind === 'workflow' && !e.data.completed)).toBe(true);
  });

  it('interleaves tools between intermediate to_user progress lines (scheduled-task)', () => {
    // Progress to_user messages must not pull later tools before themselves.
    const messages = [
      { role: 'user', content: '[Scheduled Task]', timestamp: '2026-07-31T03:54:33.000Z' },
      {
        role: 'assistant',
        content: '已获取早盘信息',
        timestamp: '2026-07-31T03:54:52.000Z',
      },
      {
        role: 'assistant',
        content: '已获取午盘实时行情',
        timestamp: '2026-07-31T03:55:17.000Z',
      },
      {
        role: 'assistant',
        content: '最终报告正文',
        end_task: true,
        timestamp: '2026-07-31T03:55:30.000Z',
      },
    ];
    const events = [
      {
        type: 'tool_call',
        data: { id: 'c1', name: 'websearch__search', args: '{}' },
        timestamp: '2026-07-31T03:54:40.000Z',
      },
      {
        type: 'tool_result',
        data: { id: 'c1', content: 'ok' },
        timestamp: '2026-07-31T03:54:50.000Z',
      },
      {
        type: 'tool_call',
        data: { id: 'c2', name: 'task_watch__complete', args: '{}' },
        timestamp: '2026-07-31T03:55:20.000Z',
      },
      {
        type: 'tool_result',
        data: { id: 'c2', content: 'done' },
        timestamp: '2026-07-31T03:55:25.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    // end_task folds process into task_fold; inspect nested order.
    expect(tl[0].kind).toBe('message');
    expect(tl[0].kind === 'message' && tl[0].data.role).toBe('user');
    expect(tl.some((e) => e.kind === 'task_fold')).toBe(true);
    const fold = tl.find((e) => e.kind === 'task_fold');
    const nested = fold && fold.kind === 'task_fold' ? fold.data.entries : [];
    const nestedKinds = nested.map((e) =>
      e.kind === 'message' ? `msg:${e.data.role}:${String(e.data.content).slice(0, 8)}` : e.kind,
    );
    const firstProgress = nestedKinds.findIndex((x) => x.includes('已获取早盘'));
    const noonProgress = nestedKinds.findIndex((x) => x.includes('已获取午盘'));
    expect(firstProgress).toBeGreaterThanOrEqual(0);
    expect(noonProgress).toBeGreaterThan(firstProgress);
    const firstWf = nestedKinds.indexOf('workflow');
    expect(firstWf).toBeGreaterThanOrEqual(0);
    expect(firstWf).toBeLessThan(firstProgress);
    const wfIndexes = nestedKinds
      .map((x, i) => (x === 'workflow' ? i : -1))
      .filter((i) => i >= 0);
    expect(wfIndexes.some((i) => i > noonProgress)).toBe(true);
    expect(
      tl.some((e) => e.kind === 'message' && String(e.data.content).includes('最终报告')),
    ).toBe(true);
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

describe('task_fold / to_user_end_task', () => {
  it('folds agent process between last user message and end_task report', () => {
    const messages = [
      { role: 'user', content: 'do the big task', timestamp: '2026-01-01T00:00:00.000Z' },
      {
        role: 'assistant',
        content: 'mid notice',
        timestamp: '2026-01-01T00:00:02.000Z',
      },
      {
        role: 'assistant',
        content: 'final summary',
        end_task: true,
        timestamp: '2026-01-01T00:00:05.000Z',
      },
    ];
    const events = [
      {
        type: 'thought',
        data: { text: 'planning…' },
        timestamp: '2026-01-01T00:00:01.000Z',
      },
      {
        type: 'tool_call',
        data: { id: 'c1', name: 'system.run_session_job', args: '{}' },
        timestamp: '2026-01-01T00:00:01.500Z',
      },
      {
        type: 'tool_result',
        data: { id: 'c1', name: 'system.run_session_job', result: 'ok' },
        timestamp: '2026-01-01T00:00:01.800Z',
      },
      {
        type: 'thought',
        data: { text: 'sub thinking', sub_agent: true, sub_task_label: 'worker' },
        timestamp: '2026-01-01T00:00:03.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    expect(tl.map((e) => e.kind)).toEqual(['message', 'task_fold', 'message']);
    expect(tl[0].kind === 'message' && tl[0].data.role).toBe('user');
    expect(tl[2].kind === 'message' && tl[2].data.content).toBe('final summary');
    expect(tl[2].kind === 'message' && tl[2].data.end_task).toBe(true);
    if (tl[1].kind === 'task_fold') {
      expect(tl[1].data.collapsed).toBe(true);
      expect(tl[1].data.entries.some((e) => e.kind === 'workflow')).toBe(true);
      expect(tl[1].data.entries.some((e) => e.kind === 'message' && e.data.content === 'mid notice')).toBe(
        true,
      );
      // Sub-agent thoughts after mid progress stay in the fold (later workflow).
      expect(
        tl[1].data.entries.some(
          (e) =>
            e.kind === 'workflow'
            && e.data.events.some((ev) => ev.subAgent && String(ev.content).includes('sub thinking')),
        ),
      ).toBe(true);
    }
  });

  it('foldTaskProcessSinceLastUser leaves user message outside the fold', () => {
    const uid = () => genTimelineUID();
    const timeline: TimelineEntry[] = [
      { kind: 'message', data: { role: 'user', content: 'go' }, _uid: uid() },
      {
        kind: 'workflow',
        data: {
          events: [{ type: 'thought', content: 'x', timestamp: 1 }],
          status: null,
          completed: true,
        },
        _uid: uid(),
      },
      {
        kind: 'message',
        data: { role: 'assistant', content: 'done', end_task: true },
        _uid: uid(),
      },
    ];
    const folded = foldTaskProcessSinceLastUser(timeline);
    expect(folded.map((e) => e.kind)).toEqual(['message', 'task_fold', 'message']);
    expect(folded[0].kind === 'message' && folded[0].data.role).toBe('user');
  });
});

describe('sealIncompleteWorkflows (mid-send)', () => {
  it('marks incomplete workflows completed and stamps elapsed_ms', () => {
    const uid = () => genTimelineUID();
    const started = 1_700_000_000_000;
    const now = started + 5500;
    const prev: TimelineEntry[] = [
      {
        kind: 'workflow',
        data: {
          events: [{ type: 'thought', content: 'thinking', timestamp: started }],
          status: 'working',
          completed: false,
          started_ms: started,
        },
        _uid: uid(),
      },
    ];
    const sealed = sealIncompleteWorkflows(prev, { nowMs: now });
    expect(sealed[0].kind).toBe('workflow');
    if (sealed[0].kind !== 'workflow') return;
    expect(sealed[0].data.completed).toBe(true);
    expect(sealed[0].data.status).toBeNull();
    expect(sealed[0].data.elapsed_ms).toBe(5500);
    expect(sealed[0].data.started_ms).toBe(started);
  });

  it('cancels every open tool_call across all incomplete workflows', () => {
    const uid = () => genTimelineUID();
    const started = 1_700_000_000_000;
    const prev: TimelineEntry[] = [
      {
        kind: 'workflow',
        data: {
          events: [
            { type: 'tool_call', content: { id: 'call_a', name: 'shell' }, timestamp: started },
            { type: 'thought', content: 'still going', timestamp: started + 10 },
          ],
          status: 'working',
          completed: false,
          started_ms: started,
        },
        _uid: uid(),
      },
      {
        kind: 'message',
        data: { role: 'user', content: 'again', timestamp: new Date().toISOString() },
        _uid: uid(),
      },
      {
        kind: 'workflow',
        data: {
          events: [
            { type: 'tool_call', content: { id: 'call_b', name: 'write' }, timestamp: started + 100, result: 'ok' },
            { type: 'tool_call', content: { id: 'call_c', name: 'edit' }, timestamp: started + 200 },
          ],
          status: 'working',
          completed: false,
          started_ms: started + 100,
        },
        _uid: uid(),
      },
    ];
    const sealed = sealIncompleteWorkflows(prev, {
      nowMs: started + 900,
      cancelOpenTools: 'Cancelled: stopped by user',
    });
    expect(sealed.filter((e) => e.kind === 'workflow').every((e) => e.kind === 'workflow' && e.data.completed)).toBe(true);
    const tools = sealed
      .filter((e): e is Extract<TimelineEntry, { kind: 'workflow' }> => e.kind === 'workflow')
      .flatMap((e) => e.data.events.filter((ev) => ev.type === 'tool_call'));
    expect(tools[0].result).toBe('Cancelled: stopped by user');
    expect(tools[0].resultStatus).toBe('error');
    expect(tools[1].result).toBe('ok');
    expect(tools[2].result).toBe('Cancelled: stopped by user');
  });

  it('drops streaming xml-live preview rows instead of marking them failed', () => {
    const uid = () => genTimelineUID();
    const started = 1_700_000_000_000;
    const prev: TimelineEntry[] = [
      {
        kind: 'workflow',
        data: {
          events: [
            {
              type: 'tool_call',
              content: { id: LIVE_XML_TOOL_ID, name: 'files', partial: true },
              timestamp: started,
            },
            { type: 'tool_call', content: { id: 'call_real', name: 'shell' }, timestamp: started + 1 },
          ],
          status: 'working',
          completed: false,
          started_ms: started,
        },
        _uid: uid(),
      },
    ];
    const sealed = sealIncompleteWorkflows(prev, {
      nowMs: started + 900,
      cancelOpenTools: 'Cancelled: still running when the turn stopped',
    });
    const tools = sealed
      .filter((e): e is Extract<TimelineEntry, { kind: 'workflow' }> => e.kind === 'workflow')
      .flatMap((e) => e.data.events.filter((ev) => ev.type === 'tool_call'));
    expect(tools).toHaveLength(1);
    expect(tools[0].content.id).toBe('call_real');
    expect(tools[0].result).toBe('Cancelled: still running when the turn stopped');
  });

  it('drops backend xml_preview_* prefix rows on cancel', () => {
    const uid = () => genTimelineUID();
    const started = 1_700_000_000_000;
    const prev: TimelineEntry[] = [
      {
        kind: 'workflow',
        data: {
          events: [
            {
              type: 'tool_call',
              content: { id: 'xml_preview_files', name: 'files', partial: true },
              timestamp: started,
            },
            {
              type: 'tool_call',
              content: { id: 'xml_preview_get s', name: 'get s', partial: true },
              timestamp: started + 1,
            },
          ],
          status: 'working',
          completed: false,
          started_ms: started,
        },
        _uid: uid(),
      },
    ];
    const sealed = sealIncompleteWorkflows(prev, {
      nowMs: started + 900,
      cancelOpenTools: 'Cancelled: still running when the turn stopped',
    });
    const tools = sealed
      .filter((e): e is Extract<TimelineEntry, { kind: 'workflow' }> => e.kind === 'workflow')
      .flatMap((e) => e.data.events.filter((ev) => ev.type === 'tool_call'));
    expect(tools).toHaveLength(0);
  });
});

describe('buildTimelineFromSession in-progress refresh', () => {
  it('keeps trailing incomplete workflow with started_ms and partial tool_call_delta', () => {
    const started = 1_700_000_000_000;
    const messages = [
      {
        role: 'user',
        content: 'write a file',
        timestamp: new Date(started - 1000).toISOString(),
      },
    ];
    const events = [
      {
        type: 'info',
        data: { text: 'Workflow started', started_ms: started },
        timestamp: new Date(started).toISOString(),
      },
      {
        type: 'thought',
        data: { text: 'planning edit' },
        timestamp: new Date(started + 200).toISOString(),
      },
      {
        type: 'tool_call_delta',
        data: {
          id: 'partial_tc_0',
          index: 0,
          name: 'filesystem__write_file',
          arguments: '{"path":"a.ts","content":"hello',
          partial: true,
        },
        timestamp: new Date(started + 400).toISOString(),
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    expect(tl.map((e) => e.kind)).toEqual(['message', 'workflow']);
    expect(tl[1].kind === 'workflow' && tl[1].data.completed).toBe(false);
    expect(tl[1].kind === 'workflow' && tl[1].data.started_ms).toBe(started);
    if (tl[1].kind !== 'workflow') return;
    const tools = tl[1].data.events.filter((e) => e.type === 'tool_call');
    expect(tools).toHaveLength(1);
    expect(tools[0].content?.partial).toBe(true);
    expect(String(tools[0].content?.args || tools[0].content?.arguments || '')).toContain('hello');
  });

  it('promotes partial tool_call_delta into final tool_call with different id', () => {
    const started = 1_700_000_000_000;
    const messages = [
      {
        role: 'user',
        content: 'write',
        timestamp: new Date(started - 1000).toISOString(),
      },
    ];
    const events = [
      {
        type: 'info',
        data: { text: 'Workflow started', started_ms: started },
        timestamp: new Date(started).toISOString(),
      },
      {
        type: 'tool_call_delta',
        data: {
          id: 'stream-id',
          index: 0,
          name: 'filesystem__write_file',
          arguments: '{"path":"a.ts","content":"x"}',
          partial: true,
        },
        timestamp: new Date(started + 100).toISOString(),
      },
      {
        type: 'tool_call',
        data: {
          id: 'call_runner_write_0',
          name: 'filesystem__write_file',
          args: '{\n  "path": "a.ts",\n  "content": "x"\n}',
        },
        timestamp: new Date(started + 200).toISOString(),
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    expect(tl[1].kind).toBe('workflow');
    if (tl[1].kind !== 'workflow') return;
    const tools = tl[1].data.events.filter((e) => e.type === 'tool_call');
    expect(tools).toHaveLength(1);
    expect(tools[0].content?.id).toBe('call_runner_write_0');
    expect(tools[0].content?.partial).toBeUndefined();
  });

  it('keeps same-turn tools in one fold when assistant sync is only tool markup', () => {
    const messages = [
      { role: 'user', content: '检查仓库', timestamp: '2026-01-01T00:00:00.000Z' },
      {
        role: 'assistant',
        content: '<tool_call>\n<func>filesystem.list_directory</func>\n</tool_call>',
        timestamp: '2026-01-01T00:00:04.000Z',
      },
      {
        role: 'assistant',
        content: '<tool_call>\n<func>filesystem.read_file</func>\n</tool_call>',
        timestamp: '2026-01-01T00:00:07.000Z',
      },
    ];
    const events = [
      {
        type: 'tool_call',
        data: { id: 'a', name: 'filesystem.list_directory', args: '{}' },
        timestamp: '2026-01-01T00:00:02.000Z',
      },
      {
        type: 'tool_result',
        data: { id: 'a', result: 'ok' },
        timestamp: '2026-01-01T00:00:03.000Z',
      },
      {
        type: 'tool_call',
        data: { id: 'b', name: 'filesystem.read_file', args: '{}' },
        timestamp: '2026-01-01T00:00:05.000Z',
      },
      {
        type: 'tool_result',
        data: { id: 'b', result: 'ok' },
        timestamp: '2026-01-01T00:00:06.000Z',
      },
      {
        type: 'tool_call',
        data: { id: 'c', name: 'filesystem.search_files', args: '{}' },
        timestamp: '2026-01-01T00:00:08.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    const wfs = tl.filter((e) => e.kind === 'workflow');
    expect(wfs).toHaveLength(1);
    if (wfs[0].kind !== 'workflow') return;
    const tools = wfs[0].data.events.filter((e) => e.type === 'tool_call');
    expect(tools).toHaveLength(3);
    expect(wfs[0].data.completed).toBe(false);
    expect(tl.some((e) => e.kind === 'message' && e.data.role === 'assistant')).toBe(false);
  });
});

describe('timelineHasVisibleChatContent', () => {
  it('ignores empty / lifecycle-only workflow shells (landing must stay centered)', () => {
    const emptyShell: TimelineEntry[] = [
      {
        kind: 'workflow',
        data: { events: [], status: 'working', completed: false, started_ms: Date.now() },
        _uid: 'w1',
      },
    ];
    expect(timelineHasVisibleChatContent(emptyShell)).toBe(false);

    const lifecycleOnly: TimelineEntry[] = [
      {
        kind: 'workflow',
        data: {
          events: [
            { type: 'info', content: { text: 'New session started' }, timestamp: Date.now() },
            { type: 'info', content: 'Workflow started', timestamp: Date.now() },
          ],
          status: null,
          completed: true,
        },
        _uid: 'w2',
      },
    ];
    expect(timelineHasVisibleChatContent(lifecycleOnly)).toBe(false);

    const hintOnly: TimelineEntry[] = [
      {
        kind: 'status_hint',
        data: { hintType: 'sleep', content: 30, timestamp: Date.now() },
        _uid: 'h1',
      },
    ];
    expect(timelineHasVisibleChatContent(hintOnly)).toBe(false);
  });

  it('detects real messages and tool activity', () => {
    expect(
      timelineHasVisibleChatContent([
        {
          kind: 'message',
          data: { role: 'user', content: 'hello', timestamp: new Date().toISOString() },
          _uid: 'm1',
        },
      ]),
    ).toBe(true);

    expect(
      timelineHasVisibleChatContent([
        {
          kind: 'workflow',
          data: {
            events: [toolCall('c1')],
            status: 'working',
            completed: false,
          },
          _uid: 'w3',
        },
      ]),
    ).toBe(true);
  });
});

describe('appendWorkflowEvents', () => {
  it('applies events in order and dedups tool_call replays', () => {
    const call = toolCall('c1', 'read_file');
    const result = toolResult('c1', 'ok');
    const next = appendWorkflowEvents([], [
      { event: call, status: 'Calling...' },
      { event: call, status: 'Calling...' },
      { event: result, status: 'done' },
    ]);
    const wf = next.find((e) => e.kind === 'workflow');
    expect(wf?.kind).toBe('workflow');
    if (wf && wf.kind === 'workflow') {
      const calls = wf.data.events.filter((e) => e.type === 'tool_call');
      expect(calls).toHaveLength(1);
      expect(calls[0].result).toBe('ok');
    }
  });
});

describe('appendLiveWorkflowBatch / absorbAssistantFinalText', () => {
  it('appends a parent tool_call into the live thought stream instead of sealing it', () => {
    let tl: TimelineEntry[] = [];
    tl = appendWorkflowEvent(
      tl,
      { type: 'thought', content: 'thinking', timestamp: Date.now() },
      'Thinking...',
    );
    tl = appendLiveWorkflowBatch(
      tl,
      [{ event: toolCall('c1', 'write_file'), status: 'Calling...' }],
      { commitAssistantText: 'Here is the plan for the next edit.' },
    );
    expect(tl.map((e) => e.kind)).toEqual(['workflow']);
    expect(tl[0].kind === 'workflow' && tl[0].data.completed).toBe(false);
    const types = tl[0].kind === 'workflow' ? tl[0].data.events.map((e) => e.type) : [];
    expect(types).toEqual(['thought', 'tool_call']);
  });

  it('folds leftover stream text into the thought step when a parent tool_call arrives', () => {
    const tl = appendLiveWorkflowBatch(
      [],
      [{ event: toolCall('c1', 'write_file'), status: 'Calling...' }],
      { commitAssistantText: 'Here is the plan for the next edit.' },
    );
    expect(tl.map((e) => e.kind)).toEqual(['workflow']);
    expect(tl.some((e) => e.kind === 'message')).toBe(false);
    const events = tl[0].kind === 'workflow' ? tl[0].data.events : [];
    expect(events.map((e) => e.type)).toEqual(['thought', 'tool_call']);
    expect(events[0]?.content).toBe('Here is the plan for the next edit.');
  });

  it('does not commit stream for a sub-agent tool_call', () => {
    const next = appendLiveWorkflowBatch(
      [],
      [{
        event: { ...toolCall('sub1', 'read_file'), subAgent: true },
        status: 'Calling...',
      }],
      { commitAssistantText: 'Parent reply still streaming.' },
    );
    expect(next.some((e) => e.kind === 'message')).toBe(false);
    expect(next[0]?.kind).toBe('workflow');
  });

  it('upgrades a committed assistant bubble instead of appending after later tools', () => {
    const tl: TimelineEntry[] = [
      {
        kind: 'message',
        data: { role: 'assistant', content: 'Hello there, working on it.' },
        _uid: 'm1',
      },
      {
        kind: 'workflow',
        data: { events: [toolCall('c1', 'write_file')], status: 'Calling...', completed: false },
        _uid: 'w1',
      },
    ];
    const absorbed = absorbAssistantFinalText(tl, 'Hello there, working on it. Done.');
    expect(absorbed).not.toBeNull();
    const messages = absorbed!.filter((e) => e.kind === 'message');
    expect(messages).toHaveLength(1);
    expect(messages[0].kind === 'message' && messages[0].data.content).toBe(
      'Hello there, working on it. Done.',
    );
    const msgIdx = absorbed!.findIndex((e) => e.kind === 'message');
    const toolIdx = absorbed!.findIndex((e, i) => i > msgIdx && e.kind === 'workflow');
    expect(toolIdx).toBeGreaterThan(msgIdx);
  });

  it('does not absorb a short prior reply into a later unrelated final', () => {
    const tl: TimelineEntry[] = [
      {
        kind: 'message',
        data: { role: 'assistant', content: 'OK' },
        _uid: 'm1',
      },
      {
        kind: 'workflow',
        data: { events: [toolCall('c1')], status: null, completed: false },
        _uid: 'w1',
      },
    ];
    expect(absorbAssistantFinalText(tl, 'OK, I will now rewrite the module.')).toBeNull();
  });
});

describe('extractLiveToolCallFromMarkup', () => {
  it('reads an unclosed <tool_call><func> websearch block', () => {
    const got = extractLiveToolCallFromMarkup(
      '<tool_call>\n<func>websearch.search</func>\n<query>福州天气',
    );
    expect(got).not.toBeNull();
    expect(got!.name).toMatch(/websearch/i);
    expect(JSON.stringify(got!.arguments)).toContain('福州');
    expect(got!.partial).toBe(true);
  });

  it('reads attribute-style name before </tool_call>', () => {
    const got = extractLiveToolCallFromMarkup(
      '<tool_call name="websearch.search">\n<arguments>{"query": "福州天气"}',
    );
    expect(got?.name).toBe('websearch.search');
  });

  it('reads leaked skeleton without XML tags', () => {
    const got = extractLiveToolCallFromMarkup('websearch\nquery\n福州天气');
    expect(got?.name).toMatch(/websearch/i);
    expect((got!.arguments as Record<string, unknown>).query).toBe('福州天气');
  });

  it('does not treat prose about calling a tool as a tool row', () => {
    expect(extractLiveToolCallFromMarkup('Let me use the websearch tool to check Fuzhou weather.')).toBeNull();
  });

  it('does not emit a row for a streaming func name prefix', () => {
    expect(extractLiveToolCallFromMarkup('<func>files')).toBeNull();
    expect(extractLiveToolCallFromMarkup('<func>filesystem')).toBeNull();
    expect(extractLiveToolCallFromMarkup('<tool_call>\n<func>start j')).toBeNull();
    expect(extractLiveToolCallFromMarkup('<tool_call>\n<func>get s')).toBeNull();
  });

  it('emits one stable live id once the dotted tool name is ready', () => {
    const got = extractLiveToolCallFromMarkup('<tool_call>\n<func>filesystem.read_file');
    expect(got).not.toBeNull();
    expect(got!.id).toBe(LIVE_XML_TOOL_ID);
    expect(got!.name).toBe('filesystem.read_file');
  });

  it('reads Ling/Qwen first-line name + arg_key/arg_value', () => {
    const got = extractLiveToolCallFromMarkup(
      '<tool_call>websearch\n<arg_key>query</arg_key>\n<arg_value>福州天气</arg_value>',
    );
    expect(got).not.toBeNull();
    expect(got!.name).toBe('websearch');
    expect((got!.arguments as Record<string, unknown>).query).toBe('福州天气');
  });

  it('appends the extracted call into the live thought workflow', () => {
    const markup = extractLiveToolCallFromMarkup(
      '<tool_call>\n<func>websearch.search</func>\n<query>福州天气',
    );
    expect(markup).not.toBeNull();
    let tl = appendWorkflowEvent(
      [],
      { type: 'thought', content: 'checking weather', timestamp: Date.now() },
      'Thinking...',
    );
    tl = appendLiveWorkflowBatch(tl, [{
      event: {
        type: 'tool_call',
        content: {
          id: markup!.id,
          name: markup!.name,
          arguments: markup!.arguments,
          args: markup!.arguments,
          partial: true,
        },
        timestamp: Date.now(),
      },
      status: 'Calling websearch...',
    }]);
    expect(tl).toHaveLength(1);
    expect(tl[0].kind).toBe('workflow');
    if (tl[0].kind === 'workflow') {
      expect(tl[0].data.completed).toBe(false);
      const types = tl[0].data.events.map((e) => e.type);
      expect(types).toEqual(['thought', 'tool_call']);
      const call = tl[0].data.events[1];
      expect(call.content.name).toMatch(/websearch/i);
    }
  });

  it('merges backend xml_preview_* prefix ids into a single row', () => {
    let tl = appendWorkflowEvent(
      [],
      {
        type: 'tool_call',
        content: { id: 'xml_preview_files', name: 'files', partial: true, index: 0 },
        timestamp: 1,
      },
      'Calling files...',
    );
    tl = appendWorkflowEvent(
      tl,
      {
        type: 'tool_call',
        content: { id: 'xml_preview_filesystem', name: 'filesystem.list_directory', partial: true, index: 0 },
        timestamp: 2,
      },
      'Calling filesystem.list_directory...',
    );
    expect(tl).toHaveLength(1);
    if (tl[0].kind !== 'workflow') return;
    const tools = tl[0].data.events.filter((e) => e.type === 'tool_call');
    expect(tools).toHaveLength(1);
    expect(tools[0].content.name).toBe('filesystem.list_directory');
  });
});

describe('rebaseTimelineUids', () => {
  it('keeps matching workflow and tool-call uids across a disk rebuild', () => {
    const prev: TimelineEntry[] = [{
      kind: 'workflow',
      _uid: 'wf-live',
      data: {
        events: [{
          type: 'tool_call',
          content: { id: 't1', name: 'read_file', args: {} },
          timestamp: 100,
          _uid: 'evt-live',
        }],
        status: 'Calling...',
        completed: false,
        started_ms: 50,
      },
    }];
    const next: TimelineEntry[] = [{
      kind: 'workflow',
      _uid: 'wf-disk',
      data: {
        events: [{
          type: 'tool_call',
          content: { id: 't1', name: 'read_file', args: {} },
          timestamp: 999,
          _uid: 'evt-disk',
        }],
        status: null,
        completed: true,
        started_ms: 50,
      },
    }];
    const rebased = rebaseTimelineUids(prev, next);
    expect(rebased[0]._uid).toBe('wf-live');
    expect(rebased[0].kind).toBe('workflow');
    if (rebased[0].kind === 'workflow') {
      expect(rebased[0].data.events[0]._uid).toBe('evt-live');
    }
  });

  it('matches workflow uid by first-event identity when started_ms differs', () => {
    const prev: TimelineEntry[] = [{
      kind: 'workflow',
      _uid: 'wf-live',
      data: {
        events: [{
          type: 'tool_call',
          content: { id: 'call-9', name: 'websearch' },
          timestamp: 1,
          _uid: 'evt-a',
        }],
        status: 'Calling...',
        completed: false,
        started_ms: 10,
      },
    }];
    const next: TimelineEntry[] = [{
      kind: 'workflow',
      _uid: 'wf-disk',
      data: {
        events: [{
          type: 'tool_call',
          content: { id: 'call-9', name: 'websearch' },
          timestamp: 88,
          _uid: 'evt-b',
        }],
        status: null,
        completed: true,
        started_ms: 99,
      },
    }];
    const rebased = rebaseTimelineUids(prev, next);
    expect(rebased[0]._uid).toBe('wf-live');
    if (rebased[0].kind === 'workflow') {
      expect(rebased[0].data.events[0]._uid).toBe('evt-a');
    }
  });
});
