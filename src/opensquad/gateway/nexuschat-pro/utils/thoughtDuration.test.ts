/** 复现：折叠内多个深度思考行的耗时统计缺失问题。 */
import { describe, expect, it } from 'vitest';
import { genTimelineUID, type WorkflowBlock, type WorkflowEvent } from './aiChatTimeline';
import { buildLines } from '../components/ai-chat/SoloActivityRow';

const t = ((k: string) => k) as never;

function evt(partial: Partial<WorkflowEvent>): WorkflowEvent {
  return { _uid: genTimelineUID(), type: 'thought', content: 'x', ...partial } as WorkflowEvent;
}

describe('deep-think durations inside a completed fold', () => {
  it('shows duration for every thought followed by a later event', () => {
    const base = Date.now();
    const block: WorkflowBlock = {
      events: [
        evt({ type: 'thought', content: 'think 1', timestamp: base }),
        evt({
          type: 'tool_call',
          content: { id: 's1', name: 'web_search', arguments: '{}' },
          result: 'ok',
          timestamp: base + 3000,
        }),
        evt({ type: 'thought', content: 'think 2', timestamp: base + 8000 }),
        evt({
          type: 'tool_call',
          content: { id: 's2', name: 'web_search', arguments: '{}' },
          result: 'ok',
          timestamp: base + 12000,
        }),
        evt({ type: 'thought', content: 'think 3', timestamp: base + 20000 }),
      ],
      status: null,
      completed: true,
      started_ms: base,
      elapsed_ms: 25000,
    };
    const lines = buildLines(block, {}, t);
    const thoughts = lines.filter((l) => l.kind === 'thought');
    expect(thoughts.length).toBe(3);
    // 每个思考行都应有耗时：3s / 4s / 5s（最后一个按块结束时间冻结）。
    expect(thoughts[0].secondary).toBe('3s');
    expect(thoughts[1].secondary).toBe('4s');
    expect(thoughts[2].secondary).toBe('5s');
  });

  it('committed stream-text thought (flush-time) still gets durations', () => {
    const base = Date.now();
    const block: WorkflowBlock = {
      events: [
        evt({ type: 'thought', content: 'think 1', timestamp: base }),
        evt({
          type: 'tool_call',
          content: { id: 's1', name: 'web_search', arguments: '{}' },
          result: 'ok',
          timestamp: base + 3000,
        }),
        // flush 补写的思考：时间戳晚于其后的工具事件（修复前）
        evt({ type: 'thought', content: 'narration', timestamp: base + 9000 }),
        evt({
          type: 'tool_call',
          content: { id: 's2', name: 'web_search', arguments: '{}' },
          result: 'ok',
          timestamp: base + 8000,
        }),
        evt({ type: 'thought', content: 'think 2', timestamp: base + 12000 }),
      ],
      status: null,
      completed: true,
      started_ms: base,
      elapsed_ms: 25000,
    };
    const lines = buildLines(block, {}, t);
    const thoughts = lines.filter((l) => l.kind === 'thought');
    expect(thoughts.length).toBe(3);
    expect(thoughts[0].secondary).toBe('3s');
    // narration(9s) 的下一事件倒挂(8s)：时间戳已不可信 —— 不再向前扫描，
    // 否则会把工具执行、甚至下一轮的耗时整段算进"深度思考 Ns"（虚高）。
    // 推导不出就显示空白。
    expect(thoughts[1].secondary).toBe('');
    // 末尾思考按块结束时间冻结：25s - 12s = 13s。
    expect(thoughts[2].secondary).toBe('13s');
  });
});
