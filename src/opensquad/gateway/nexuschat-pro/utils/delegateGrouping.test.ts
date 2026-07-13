import { describe, it, expect } from 'vitest';
import {
  buildDisplayWorkflowItems,
  extractDelegatePrompt,
  isAsyncSubmitAck,
  isDelegateToolName,
} from './delegateGrouping';
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

  it('keeps async submit running and nests orphan steps by job_id', () => {
    const events: WorkflowEvent[] = [
      {
        type: 'tool_call',
        content: { id: 'd1', name: 'delegate_task_submit', arguments: { task: 'Async' } },
        timestamp: 1,
        result: '{"job_id":"j1","status":"running","result":null}',
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
        jobId: 'j1',
        subTaskLabel: 'Async',
      },
      {
        type: 'thought',
        content: 'looking around',
        timestamp: 4,
        subAgent: true,
        jobId: 'j1',
      },
    ];
    const items = buildDisplayWorkflowItems(events);
    const del = items.find((i) => i.kind === 'delegation');
    expect(del?.kind).toBe('delegation');
    if (del?.kind === 'delegation') {
      expect(del.bundle.running).toBe(true);
      expect(del.bundle.finalResult).toBe('');
      expect(del.bundle.children.some((c) => c.subAgent && c.type === 'tool_call')).toBe(true);
      expect(del.bundle.children.some((c) => c.type === 'thought')).toBe(true);
      expect(del.bundle.jobId).toBe('j1');
    }
  });

  it('routes parallel async jobs to the matching submit fold', () => {
    const events: WorkflowEvent[] = [
      {
        type: 'tool_call',
        content: { id: 'd1', name: 'delegate_task_submit', arguments: { task: 'Explore A' } },
        timestamp: 1,
        result: '{"job_id":"ja","status":"running","result":null}',
        resultStatus: 'success',
      },
      {
        type: 'tool_call',
        content: { id: 'd2', name: 'delegate_task_submit', arguments: { task: 'Explore B' } },
        timestamp: 2,
        result: '{"job_id":"jb","status":"running","result":null}',
        resultStatus: 'success',
      },
      {
        type: 'tool_call',
        content: { id: 'sa', name: 'list_dir' },
        timestamp: 3,
        subAgent: true,
        jobId: 'ja',
        subTaskLabel: 'Explore A',
      },
      {
        type: 'tool_call',
        content: { id: 'sb', name: 'list_dir' },
        timestamp: 4,
        subAgent: true,
        jobId: 'jb',
        subTaskLabel: 'Explore B',
      },
    ];
    const items = buildDisplayWorkflowItems(events);
    const dels = items.filter((i) => i.kind === 'delegation');
    expect(dels).toHaveLength(2);
    if (dels[0].kind === 'delegation' && dels[1].kind === 'delegation') {
      expect(dels[0].bundle.running).toBe(true);
      expect(dels[1].bundle.running).toBe(true);
      expect(dels[0].bundle.children.map((c) => c.jobId)).toEqual(['ja']);
      expect(dels[1].bundle.children.map((c) => c.jobId)).toEqual(['jb']);
    }
  });

  it('does not treat helper tool names as delegate entrypoints', () => {
    expect(isDelegateToolName('delegate_task')).toBe(true);
    expect(isDelegateToolName('delegate_task_submit')).toBe(true);
    expect(isDelegateToolName('delegate_task_result')).toBe(false);
    expect(isDelegateToolName('delegate_task_list')).toBe(false);
    expect(isDelegateToolName('self_learn.start_learn')).toBe(true);
    expect(isDelegateToolName('self_learn__start_learn')).toBe(true);
  });

  it('recognizes namespaced / Native FC delegate tool names', () => {
    expect(isDelegateToolName('delegate_task__delegate_task')).toBe(true);
    expect(isDelegateToolName('delegate_task__delegate_task_submit')).toBe(true);
    expect(isDelegateToolName('delegate_task.delegate_task')).toBe(true);
    expect(isDelegateToolName('delegate_task.delegate_task_submit')).toBe(true);
    expect(isDelegateToolName('delegate_task__delegate_task_result')).toBe(false);
    expect(isDelegateToolName('delegate_task__delegate_task_list')).toBe(false);
  });

  it('nests children under namespaced delegate_task_submit', () => {
    const events: WorkflowEvent[] = [
      {
        type: 'tool_call',
        content: {
          id: 'd1',
          name: 'delegate_task__delegate_task_submit',
          arguments: { task: 'Explore repo' },
        },
        timestamp: 1,
        result: '{"job_id":"j9","status":"running","result":null}',
        resultStatus: 'success',
      },
      {
        type: 'tool_call',
        content: { id: 's1', name: 'list_dir' },
        timestamp: 2,
        subAgent: true,
        jobId: 'j9',
        subTaskLabel: 'Explore repo',
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

  it('detects async submit ack payloads', () => {
    expect(isAsyncSubmitAck('{"job_id":"j1","status":"running","result":null}')).toBe(true);
    expect(isAsyncSubmitAck('{"job_id":"j1"}')).toBe(true);
    expect(isAsyncSubmitAck('final answer text')).toBe(false);
    expect(isAsyncSubmitAck('{"job_id":"j1","status":"done","result":"ok"}')).toBe(false);
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
