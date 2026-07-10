import { describe, it, expect } from 'vitest';
import { buildDisplayWorkflowItems, extractDelegatePrompt } from './delegateGrouping';
import type { WorkflowEvent } from './aiChatTimeline';

describe('delegateGrouping', () => {
  it('nests sub_agent events under parent delegate_task', () => {
    const events: WorkflowEvent[] = [
      {
        type: 'tool_call',
        content: {
          id: 'd1',
          name: 'delegate_task',
          arguments: { task: 'Explore the UI', context: 'Look at Solo' },
        },
        timestamp: 1,
      },
      {
        type: 'tool_call',
        content: { id: 's1', name: 'read_file', args: '{}' },
        timestamp: 2,
        subAgent: true,
        subTaskLabel: 'Explore the UI',
      },
      {
        type: 'tool_call',
        content: { id: 's1', name: 'read_file' },
        timestamp: 3,
        result: 'ok',
        resultStatus: 'success',
        subAgent: true,
      },
      {
        type: 'tool_result',
        content: { id: 'd1', name: 'delegate_task', result: 'done exploring' },
        timestamp: 4,
      },
      {
        type: 'thought',
        content: 'parent thought after',
        timestamp: 5,
      },
    ];

    const items = buildDisplayWorkflowItems(events);
    expect(items).toHaveLength(2);
    expect(items[0].kind).toBe('delegation');
    if (items[0].kind === 'delegation') {
      expect(items[0].bundle.running).toBe(false);
      expect(items[0].bundle.children).toHaveLength(2);
      expect(items[0].bundle.prompt).toContain('Explore the UI');
      expect(items[0].bundle.parent.result).toContain('done exploring');
      expect(items[0].bundle.finalResult).toContain('done exploring');
    }
    expect(items[1].kind).toBe('event');
  });

  it('absorbs untagged thoughts so nested tools are not orphaned', () => {
    const events: WorkflowEvent[] = [
      {
        type: 'tool_call',
        content: { id: 'd1', name: 'delegate_task', arguments: { task: 'Go' } },
        timestamp: 1,
      },
      {
        type: 'thought',
        content: 'I will list files first',
        timestamp: 2,
      },
      {
        type: 'tool_call',
        content: { id: 's1', name: 'read_file' },
        timestamp: 3,
        subAgent: true,
      },
      {
        type: 'tool_result',
        content: { id: 'd1', name: 'delegate_task', result: 'report here' },
        timestamp: 4,
      },
    ];
    const items = buildDisplayWorkflowItems(events);
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('delegation');
    if (items[0].kind === 'delegation') {
      expect(items[0].bundle.children).toHaveLength(2);
      expect(items[0].bundle.children[0].type).toBe('thought');
      expect(items[0].bundle.children[1].subAgent).toBe(true);
      expect(items[0].bundle.finalResult).toBe('report here');
    }
  });

  it('attaches orphan subAgent events to preceding delegation', () => {
    const events: WorkflowEvent[] = [
      {
        type: 'tool_call',
        content: { id: 'd1', name: 'delegate_task_submit', arguments: { task: 'Async' } },
        timestamp: 1,
        result: '{"job_id":"j1"}',
        resultStatus: 'success',
      },
      {
        type: 'thought',
        content: 'parent continues',
        timestamp: 2,
      },
      {
        type: 'tool_call',
        content: { id: 's1', name: 'read_file' },
        timestamp: 3,
        subAgent: true,
      },
    ];
    const items = buildDisplayWorkflowItems(events);
    const del = items.find((i) => i.kind === 'delegation');
    expect(del?.kind).toBe('delegation');
    if (del?.kind === 'delegation') {
      expect(del.bundle.children.some((c) => c.subAgent && c.type === 'tool_call')).toBe(true);
    }
  });

  it('keeps delegate running until parent result arrives', () => {
    const events: WorkflowEvent[] = [
      {
        type: 'tool_call',
        content: { id: 'd1', name: 'delegate_task', arguments: { task: 'Go' } },
        timestamp: 1,
      },
      {
        type: 'info',
        content: { message: '[Sub-Agent] Starting: Go', sub_agent: true },
        timestamp: 2,
        subAgent: true,
      },
    ];
    const items = buildDisplayWorkflowItems(events);
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('delegation');
    if (items[0].kind === 'delegation') {
      expect(items[0].bundle.running).toBe(true);
      expect(items[0].bundle.children).toHaveLength(1);
    }
  });

  it('extractDelegatePrompt prefers task + context', () => {
    const evt: WorkflowEvent = {
      type: 'tool_call',
      content: { name: 'delegate_task', arguments: { task: 'A', context: 'B' } },
      timestamp: 1,
    };
    expect(extractDelegatePrompt(evt)).toContain('A');
    expect(extractDelegatePrompt(evt)).toContain('B');
  });
});
