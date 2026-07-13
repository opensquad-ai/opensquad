import { describe, expect, it } from 'vitest';
import type { WorkflowEvent } from './aiChatTimeline';
import { buildDisplayWorkflowItems } from './delegateGrouping';
import {
  applyJobStdout,
  applyJobStatus,
  attachShellJobsToDisplayItems,
  isShellJobStillRunningAck,
  isShellJobToolName,
  isShellPollToolName,
  rebuildShellStreamsFromEvents,
  sealShellStreamFromResult,
  type ShellStreamState,
} from './shellJobGrouping';

function toolCall(id: string, name: string, args: string): WorkflowEvent {
  return {
    type: 'tool_call',
    content: { id, name, args },
    timestamp: 1,
  };
}

describe('shellJobGrouping', () => {
  it('recognizes system shell job tool names', () => {
    expect(isShellJobToolName('system__start_job')).toBe(true);
    expect(isShellJobToolName('system.start_job')).toBe(true);
    expect(isShellJobToolName('system__run_session_job')).toBe(true);
    expect(isShellJobToolName('system__check_job')).toBe(false);
    expect(isShellPollToolName('system__check_job')).toBe(true);
  });

  it('detects still-running start_job ack', () => {
    expect(
      isShellJobStillRunningAck(
        JSON.stringify({ status: 'success', completed: false, job_id: 'abc' }),
      ),
    ).toBe(true);
    expect(
      isShellJobStillRunningAck(
        JSON.stringify({ status: 'success', completed: true, output: 'ok' }),
      ),
    ).toBe(false);
  });

  it('hides check_job and wraps start_job as shell_job', () => {
    const events: WorkflowEvent[] = [
      toolCall('c1', 'system__start_job', JSON.stringify({ command: 'echo hi' })),
      toolCall('c2', 'system__check_job', JSON.stringify({ job_id: 'x' })),
    ];
    const base = buildDisplayWorkflowItems(events);
    const items = attachShellJobsToDisplayItems(base, {});
    expect(items.some((i) => i.kind === 'shell_job')).toBe(true);
    expect(
      items.some(
        (i) =>
          i.kind === 'event' &&
          i.event.type === 'tool_call' &&
          String((i.event.content as any)?.name || '').includes('check_job'),
      ),
    ).toBe(false);
  });

  it('accumulates stdout and seals on status', () => {
    let streams = applyJobStdout(
      {},
      { call_id: 'c1', chunk: 'hello\n', command: 'echo' },
    );
    streams = applyJobStdout(streams, { call_id: 'c1', chunk: 'world\n' });
    expect(streams.c1.output).toBe('hello\nworld\n');
    streams = applyJobStatus(streams, { call_id: 'c1', state: 'done', return_code: 0 });
    expect(streams.c1.state).toBe('done');
    streams = sealShellStreamFromResult(
      streams,
      'c1',
      JSON.stringify({ completed: true, output: 'final' }),
    );
    expect(streams.c1.state).toBe('done');
    expect(streams.c1.output).toContain('hello');
  });

  it('matches job_stdout by job_id when call_id is missing', () => {
    let streams: Record<string, ShellStreamState> = {
      c1: { callId: 'c1', output: '', state: 'running', jobId: 'job9' },
    };
    streams = applyJobStdout(streams, { job_id: 'job9', chunk: 'hi\n' });
    expect(streams.c1.output).toBe('hi\n');
  });

  it('rebuilds shell streams from persisted tool_call+result after refresh', () => {
    const events: WorkflowEvent[] = [
      {
        type: 'tool_call',
        content: {
          id: 'c1',
          name: 'system__start_job',
          args: JSON.stringify({ command: 'echo hi' }),
        },
        result: JSON.stringify({ status: 'success', completed: true, output: 'hi\n', job_id: 'j1' }),
        timestamp: 1,
      },
    ];
    const streams = rebuildShellStreamsFromEvents(events);
    expect(streams.c1?.state).toBe('done');
    expect(streams.c1?.output).toContain('hi');
    expect(streams.c1?.command).toContain('echo');
  });
});
