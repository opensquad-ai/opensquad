// @vitest-environment jsdom
/**
 * 运行中的输出速率 = **过去这一秒**新产生的量，不是开跑至今的平均。
 *
 * 结算口径的 token 只在回合收尾（`turn_usage`）才有，所以运行中按流出的字数折算；
 * 但折算的口径必须是"这一秒"，否则前面一段慢的会把数字一直拖在低位，看不出此刻
 * 模型到底多快。实现按既有的 1s tick 采样，取相邻两次样本的增量。
 *
 * 只算模型产出的文本：thinking + 过程输出。工具的参数/结果是工具 IO，不是生成，
 * 绝不能计进速率。
 *
 * Rendering the real component is the only way to prove the headline carries the
 * label; the timer-driven case below is the one that actually exercises the
 * sampling (a static render has no second sample yet).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SoloActivityRow, outputCharsOf, rollingCharsPerSec } from './SoloActivityRow';
import type { WorkflowBlock, WorkflowEvent } from '../../utils/aiChatTimeline';
// Side-effect import: initialises i18next so the headline renders translated
// text (the assertions below match the default zh strings).
import '../../i18n';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const RATE_RE = /\d+(\.\d+)? tok\/s/;

/** Model output — an intermediate prose block ("过程输出"). */
function processEvent(text: string): WorkflowEvent {
  return { _uid: 'p1', type: 'process_output', content: text, timestamp: 1_700_000_000_000 };
}

/** Tool I/O — arguments and results are never model output. */
function toolEvents(): WorkflowEvent[] {
  return [
    {
      _uid: 'c1',
      type: 'tool_call',
      content: { name: 'filesystem__read_file', call_id: 'call-1', args: { path: 'a.txt' } },
      timestamp: 1_700_000_000_000,
    },
    {
      _uid: 'r1',
      type: 'tool_result',
      content: { name: 'filesystem__read_file', call_id: 'call-1' },
      result: 'x'.repeat(500),
      resultStatus: 'success',
      timestamp: 1_700_000_000_001,
    },
  ];
}

function liveBlock(events: WorkflowEvent[], ageMs: number): WorkflowBlock {
  return { events, status: null, completed: false, started_ms: Date.now() - ageMs };
}

function render(b: WorkflowBlock): string {
  return renderToStaticMarkup(
    React.createElement(SoloActivityRow, { block: b, turnDelivered: false }),
  );
}

describe('rollingCharsPerSec — 只算过去这一段间隔', () => {
  it('相邻样本的增量 ÷ 间隔秒数', () => {
    expect(rollingCharsPerSec({ t: 1000, chars: 100 }, { t: 2000, chars: 500 })).toBe(400);
  });

  it('间隔不是整 1s 时按真实秒数折算', () => {
    expect(rollingCharsPerSec({ t: 0, chars: 0 }, { t: 2000, chars: 400 })).toBe(200);
  });

  it('第一个样本没有区间可言 → null（而不是 0，0 会被当成"真的没在产出"）', () => {
    expect(rollingCharsPerSec(null, { t: 1000, chars: 999 })).toBeNull();
  });

  it('零间隔 / 时间倒流 → null，绝不吐 Infinity', () => {
    expect(rollingCharsPerSec({ t: 2000, chars: 1 }, { t: 2000, chars: 1 })).toBeNull();
    expect(rollingCharsPerSec({ t: 2000, chars: 1 }, { t: 1000, chars: 9 })).toBeNull();
  });

  it('字数没长或变少（折叠 / 压缩切块）→ 0，不是负数', () => {
    expect(rollingCharsPerSec({ t: 0, chars: 500 }, { t: 1000, chars: 500 })).toBe(0);
    expect(rollingCharsPerSec({ t: 0, chars: 500 }, { t: 1000, chars: 120 })).toBe(0);
  });
});

describe('outputCharsOf — 只算模型产出的文本', () => {
  it('累计思考与过程输出的正文', () => {
    expect(
      outputCharsOf([
        { kind: 'thought', detail: '过'.repeat(10) },
        { kind: 'process', detail: '过'.repeat(5) },
      ]),
    ).toBe(15);
  });

  it('工具的参数 / 结果不算生成', () => {
    expect(outputCharsOf([{ kind: 'tool', detail: 'x'.repeat(999) }])).toBe(0);
    expect(outputCharsOf([{ kind: 'progress', detail: 'x'.repeat(999) }])).toBe(0);
  });

  it('缺 detail 不留 NaN', () => {
    expect(outputCharsOf([{ kind: 'thought' }, { kind: 'process' }])).toBe(0);
  });
});

describe('活动行表头 — 速率的门槛', () => {
  it('刚开跑（还没有第二个采样点）不显示速率', () => {
    const html = render(liveBlock([processEvent('过'.repeat(400))], 5000));
    expect(html).toContain('正在工作');
    expect(html).not.toMatch(RATE_RE);
  });

  it('只有工具 IO 的块不声称速率（工具结果那 500 字不是生成）', () => {
    const html = render(liveBlock(toolEvents(), 5000));
    expect(html).not.toBe('');
    expect(html).not.toMatch(RATE_RE);
  });

  it('已结束的块保留冻结标题，不带运行中速率', () => {
    const b: WorkflowBlock = {
      events: [processEvent('过'.repeat(400))],
      status: null,
      completed: true,
      started_ms: Date.now() - 4000,
      elapsed_ms: 4000,
    };
    expect(render(b)).not.toMatch(RATE_RE);
  });
});

describe('活动行表头 — 这一秒的增量', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('第二个采样点后出现 N tok/s，数字来自这一秒新产出的字', async () => {
    vi.useFakeTimers();
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const view = (block: WorkflowBlock) =>
      React.createElement(SoloActivityRow, { block, turnDelivered: false });

    // 采样点 1：只有工具行，正文还没开始产出。块已经跑了 10s —— 累计口径会
    // 用这个总数当分母，所以下面断言 400.0 才能把"只算这一秒"钉住。
    await act(async () => {
      root.render(view(liveBlock(toolEvents(), 10_000)));
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    // 这一秒里模型产出了 400 字
    await act(async () => {
      root.render(view(liveBlock([...toolEvents(), processEvent('过'.repeat(400))], 11_000)));
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(container.textContent).toMatch(RATE_RE);
    // 增量 400 字 ÷ 间隔 1s —— 不是"400 ÷ 已跑秒数"
    expect(container.textContent).toContain('400.0');

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});
