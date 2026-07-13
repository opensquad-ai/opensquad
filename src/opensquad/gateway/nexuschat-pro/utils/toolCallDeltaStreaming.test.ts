/** Unit tests for partial Native-FC tool arg streaming helpers. */
import { describe, expect, it } from 'vitest';
import {
  appendWorkflowEvent,
  genTimelineUID,
  type TimelineEntry,
  type WorkflowEvent,
} from './aiChatTimeline';
import { parsePartialFileToolArgs } from '../components/ai-chat/FileDiffBlock';

describe('parsePartialFileToolArgs', () => {
  it('parses complete JSON', () => {
    const args = parsePartialFileToolArgs('{"path":"a.py","content":"print(1)\\n"}');
    expect(args?.path).toBe('a.py');
    expect(args?.content).toBe('print(1)\n');
  });

  it('extracts path+content from incomplete streaming JSON', () => {
    const raw = '{"path": "src/app.ts", "content": "export const x = 1;\\nconsol';
    const args = parsePartialFileToolArgs(raw);
    expect(args?.path).toBe('src/app.ts');
    expect(String(args?.content)).toContain('export const x = 1;');
    expect(String(args?.content)).toContain('consol');
  });
});

describe('appendWorkflowEvent partial tool_call streaming', () => {
  it('upserts partial tool_call then promotes on final tool_call', () => {
    let timeline: TimelineEntry[] = [
      {
        kind: 'workflow',
        data: { events: [], status: null, completed: false },
        _uid: genTimelineUID(),
      },
    ];

    const partial1: WorkflowEvent = {
      type: 'tool_call',
      content: {
        id: 'partial_tc_0',
        index: 0,
        name: 'filesystem__write_file',
        arguments: '{"path":"a.py","content":"hel',
        partial: true,
      },
      timestamp: 1,
    };
    timeline = appendWorkflowEvent(timeline, partial1, 'Writing…');
    expect(timeline[0].kind).toBe('workflow');
    const wf0 = (timeline[0] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
    expect(wf0.events).toHaveLength(1);
    expect(wf0.events[0].content.partial).toBe(true);
    expect(String(wf0.events[0].content.arguments)).toContain('hel');

    const partial2: WorkflowEvent = {
      type: 'tool_call',
      content: {
        id: 'partial_tc_0',
        index: 0,
        name: 'filesystem__write_file',
        arguments: '{"path":"a.py","content":"hello\\nworld"}',
        partial: true,
      },
      timestamp: 2,
    };
    timeline = appendWorkflowEvent(timeline, partial2, 'Writing…');
    const wf1 = (timeline[0] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
    expect(wf1.events).toHaveLength(1);
    expect(String(wf1.events[0].content.arguments)).toContain('world');

    const finalCall: WorkflowEvent = {
      type: 'tool_call',
      content: {
        id: 'call_final_write',
        name: 'filesystem__write_file',
        args: JSON.stringify({ path: 'a.py', content: 'hello\nworld' }, null, 2),
      },
      timestamp: 3,
    };
    timeline = appendWorkflowEvent(timeline, finalCall, 'Calling…');
    const wf2 = (timeline[0] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
    expect(wf2.events).toHaveLength(1);
    expect(wf2.events[0].content.partial).toBe(false);
    expect(wf2.events[0].content.id).toBe('call_final_write');
  });
});
