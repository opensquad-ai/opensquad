// @vitest-environment jsdom
/**
 * 插话（steer）嵌在工具流里，不切段。
 *
 * 症状（截图）：agent 正在跑工具流时插一句话，那一轮工具流被切成两段，中间夹了
 * 一个用户气泡。agent 侧其实没打断 —— runner 在每个工具执行完后把插话 drain 进
 * 模型上下文，同一个回合继续跑（`_turn_loop.py`）。是前端把它当成"中断"渲染了：
 * `steer_consumed` 走的是和「立即发送」一样的 `sealIncompleteWorkflows` + 顶层
 * 气泡，于是正在跑的 fold 被封口，后面的工具另起一块。
 *
 * 改法：插话变成 fold **内部**的一个事件（`user_steer`），排在它打断的那两次工具
 * 调用之间。实时（appendUserSteerToTimeline）和刷新重建（buildTimelineFromSession
 * 的 steer 分支）汇到同一条渲染路径，两条路的表现因此一致。刷新路径需要历史里的
 * 插话带 `steer` 标记，由 runner 在 drain 时打上。
 *
 * Rules:
 *   R1  a marked mid-turn message lands INSIDE the running fold, between the tools
 *       it interrupted — one block, no top-level bubble (refresh path);
 *   R1b unmarked control: without the marker the same session still splits, which
 *       is what the marker buys;
 *   R2  appendUserSteerToTimeline attaches to the running fold and refuses when
 *       there is none (sealed fold / no fold / blank text), so the caller can fall
 *       back to a bubble; it never mutates the input array;
 *   R3  a `user_steer` event renders as one compact 插话 row, formatted like the
 *       user bubble (a quote tag becomes a quoted line), and is not counted as work
 *       in the collapsed headline;
 *   R4  the row stays visible while the fold is collapsed (展开时它在流内原位，
 *       折叠时补在外面 —— 同一行不能出现两次);
 *   R5  wiring: the consumer nests instead of sealing; the runner stamps the marker.
 *
 * Mutations verified (each killed by the rule named):
 *   MA1 `buildTimelineFromSession` never takes the steer branch      → R1
 *   MA2 `convertSessionEventsToWorkflow` drops `user_steer`          → R1
 *   MA3 `appendUserSteerToTimeline` also attaches to a sealed fold   → R2
 *   MA3b it mutates the array it was handed                          → R2
 *   MA4 the consumer calls it but discards the result (`null && …`)  → R5
 *   MA5 drop the collapsed-state steer rows                          → R4
 *   MA6 `eventToLines` builds no steer line                          → R3
 *   MA7 the thought-only branch swallows a lone steer                → R4
 *   MA8 count the steer as work in the headline                      → R3
 *   MA10 recognise quoted lines only by `> ` (quote blank lines leak a bare `>`) → R3
 *   MA9 (python) runner drops `steer=True` on the drained message    → tests/test_steer_injection.py
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { SoloActivityRow, buildLines } from './SoloActivityRow';
import {
  appendUserSteerToTimeline,
  buildTimelineFromSession,
  type TimelineEntry,
  type WorkflowBlock,
  type WorkflowEvent,
} from '../../utils/aiChatTimeline';
import i18n from '../../i18n';

const t = i18n.t.bind(i18n);

const ROOT = path.resolve(__dirname, '..', '..');
const code = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const PAGE = code(fs.readFileSync(path.join(ROOT, 'components', 'AIChatPage.tsx'), 'utf8'));
const FOLD = code(
  fs.readFileSync(path.join(ROOT, 'components', 'ai-chat', 'SoloActivityRow.tsx'), 'utf8'),
);
const TURN_LOOP = fs.readFileSync(
  path.resolve(ROOT, '..', '..', '_runner', '_turn_loop.py'),
  'utf8',
);

const STEER_TEXT = '在 C:\\Users\\adminuser\\Desktop\\战略\\ai\\skill\\theme-stock-miner 下';

const raw = (type: string, data: unknown, ts: string) => ({ type, data, timestamp: ts });

/**
 * One real turn: two tools, the user interjects, the turn keeps going for another
 * tool, then the reply. `marked` is the runner's `steer` flag on the history
 * message — the only difference between the fixed and the old rendering.
 */
function midTurnSteerSession(marked: boolean) {
  return {
    messages: [
      { role: 'user', content: '把项目跑起来', timestamp: '2026-09-24T02:00:00.000Z' },
      { role: 'assistant', content: '', timestamp: '2026-09-24T02:00:05.000Z' },
      {
        role: 'user',
        content: STEER_TEXT,
        timestamp: '2026-09-24T02:00:20.000Z',
        ...(marked ? { steer: true } : {}),
      },
      { role: 'assistant', content: '已经跑起来了', timestamp: '2026-09-24T02:01:00.000Z' },
    ],
    events: [
      raw('thought', { text: '先看看目录' }, '2026-09-24T02:00:02.000Z'),
      raw('tool_call', { id: 'c1', name: 'filesystem__read_file', args: {} }, '2026-09-24T02:00:03.000Z'),
      raw('tool_result', { id: 'c1', name: 'filesystem__read_file', result: 'ok' }, '2026-09-24T02:00:04.000Z'),
      // 插话注入后（下一个工具边界）才继续的工具
      raw('tool_call', { id: 'c2', name: 'filesystem__read_file', args: {} }, '2026-09-24T02:00:21.000Z'),
      raw('tool_result', { id: 'c2', name: 'filesystem__read_file', result: 'ok' }, '2026-09-24T02:00:22.000Z'),
      raw('tool_call', { id: 'c3', name: 'filesystem__read_file', args: {} }, '2026-09-24T02:00:23.000Z'),
      raw('tool_result', { id: 'c3', name: 'filesystem__read_file', result: 'ok' }, '2026-09-24T02:00:24.000Z'),
    ],
  };
}

const blocksOf = (tl: TimelineEntry[]) =>
  tl.filter((e): e is Extract<TimelineEntry, { kind: 'workflow' }> => e.kind === 'workflow');
const bubblesOf = (tl: TimelineEntry[]) =>
  tl.filter(
    (e): e is Extract<TimelineEntry, { kind: 'message' }> =>
      e.kind === 'message' && (e.data as any).role === 'user',
  );

describe('R1 — 带标记的插话留在 fold 里（刷新重建）', () => {
  it('lands between the tools it interrupted, in one block', () => {
    const { messages, events } = midTurnSteerSession(true);
    const tl = buildTimelineFromSession(messages, events);

    expect(blocksOf(tl)).toHaveLength(1);
    const evs = blocksOf(tl)[0].data.events;
    expect(evs.map((e) => e.type)).toEqual([
      'thought',
      'tool_call',
      'user_steer',
      'tool_call',
      'tool_call',
    ]);
    // The row sits after c1 and before c2 — exactly where it was injected.
    expect((evs[1].content as any).id).toBe('c1');
    expect((evs[3].content as any).id).toBe('c2');
  });

  it('and no longer shows up as a bubble of its own', () => {
    const { messages, events } = midTurnSteerSession(true);
    const tl = buildTimelineFromSession(messages, events);
    expect(bubblesOf(tl).map((b) => (b.data as any).content)).toEqual(['把项目跑起来']);
    expect(JSON.stringify(bubblesOf(tl))).not.toContain('theme-stock-miner');
  });

  it('the interjection text survives the round trip', () => {
    const { messages, events } = midTurnSteerSession(true);
    const tl = buildTimelineFromSession(messages, events);
    const steer = blocksOf(tl)[0].data.events.find((e) => e.type === 'user_steer');
    expect(String((steer?.content as any).text)).toContain('theme-stock-miner');
  });
});

describe('R1b — 没标记的同一条消息仍会切段（标记就是这件事的开关）', () => {
  it('control: the unmarked steer stays a top-level bubble', () => {
    const { messages, events } = midTurnSteerSession(false);
    const tl = buildTimelineFromSession(messages, events);
    expect(bubblesOf(tl).map((b) => (b.data as any).content)).toEqual([
      '把项目跑起来',
      STEER_TEXT,
    ]);
    expect(
      blocksOf(tl)[0].data.events.some((e) => e.type === 'user_steer'),
    ).toBe(false);
  });
});

describe('R2 — 实时挂载：贴到正在跑的那一块上', () => {
  const running = (events: WorkflowEvent[] = []): TimelineEntry[] => [
    { kind: 'message', data: { role: 'user', content: '干活' } as any, _uid: 'u1' },
    { kind: 'workflow', data: { events, status: 'working', completed: false }, _uid: 'w1' },
  ];

  it('appends the steer to the open fold', () => {
    const before = running([
      { type: 'tool_call', content: { id: 'c1', name: 'filesystem__read_file' }, timestamp: 1 },
    ]);
    const after = appendUserSteerToTimeline(before, STEER_TEXT);
    expect(after).not.toBeNull();
    expect(blocksOf(after as TimelineEntry[])[0].data.events.map((e) => e.type)).toEqual([
      'tool_call',
      'user_steer',
    ]);
    // 不封口：fold 还是活的，后面的工具继续落在同一块里
    expect(blocksOf(after as TimelineEntry[])[0].data.completed).toBe(false);
    expect(blocksOf(after as TimelineEntry[])[0].data.status).toBe('working');
  });

  it('does not touch the array it was handed', () => {
    const before = running();
    const settled = appendUserSteerToTimeline(before, STEER_TEXT) as TimelineEntry[];
    expect(blocksOf(before)[0].data.events).toHaveLength(0);
    expect(blocksOf(settled)[0].data.events).toHaveLength(1);
    expect(before).not.toBe(settled);
  });

  it('refuses a sealed fold, so the caller can fall back to a bubble', () => {
    const sealed: TimelineEntry[] = [
      { kind: 'workflow', data: { events: [], status: null, completed: true }, _uid: 'w1' },
    ];
    expect(appendUserSteerToTimeline(sealed, STEER_TEXT)).toBeNull();
  });

  it('refuses when there is no fold at all', () => {
    expect(
      appendUserSteerToTimeline(
        [{ kind: 'message', data: { role: 'user', content: 'hi' } as any, _uid: 'u1' }],
        STEER_TEXT,
      ),
    ).toBeNull();
    expect(appendUserSteerToTimeline([], STEER_TEXT)).toBeNull();
  });

  it('refuses an empty insertion (nothing to show, no empty row)', () => {
    expect(appendUserSteerToTimeline(running(), '   \n\t ')).toBeNull();
  });
});

describe('R3 — 插话渲染成一行，且不算"做过的事"', () => {
  const withSteer = (): WorkflowBlock => ({
    events: [
      {
        _uid: 'c1',
        type: 'tool_call',
        content: { id: 'c1', name: 'filesystem__read_file', args: {} },
        result: 'ok',
        resultStatus: 'success',
        timestamp: 1,
      },
      { _uid: 's1', type: 'user_steer', content: { text: STEER_TEXT }, timestamp: 2 },
      {
        _uid: 'c2',
        type: 'tool_call',
        content: { id: 'c2', name: 'filesystem__read_file', args: {} },
        result: 'ok',
        resultStatus: 'success',
        timestamp: 3,
      },
    ],
    status: null,
    completed: true,
    started_ms: 1,
  });

  it('produces exactly one steer line, in place', () => {
    const lines = buildLines(withSteer(), {}, t);
    expect(lines.map((l) => l.kind)).toEqual(['tool', 'steer', 'tool']);
    expect(lines[1].detail).toBe(STEER_TEXT);
  });

  it('formats a quote the same way the user bubble does', () => {
    const b = withSteer();
    b.events[1] = {
      ...b.events[1],
      content: { text: '<user_quote>\n文件.py:12\nuse strict;\n</user_quote>\n\n看看这里' },
    };
    const line = buildLines(b, {}, t).find((l) => l.kind === 'steer')!;
    expect(line.detail).toContain('> 文件.py:12');
    expect(line.detail).toContain('> use strict;');
    expect(line.detail).toContain('看看这里');
    expect(line.detail).not.toContain('<user_quote>');
  });

  it('引用里的空行不会漏成裸 ">"', () => {
    // formatUserSkillDisplayContent 把引用中的空行写成裸 `>`；渲染时若只认
    // `> ` 前缀，这个 '>' 会当正文原样显示出来。（外层折叠箭头本身也是 '>'，
    // 所以只看这一行自己的 DOM。）
    const b = withSteer();
    b.events[1] = {
      ...b.events[1],
      content: { text: '<user_quote>\n第一段\n\n第二段\n</user_quote>' },
    };
    const host = document.createElement('div');
    host.innerHTML = renderToStaticMarkup(
      React.createElement(SoloActivityRow, {
        block: b,
        uiMode: 'classic',
        embedVisualizations: false,
        turnDelivered: true,
      }),
    );
    const body = host.querySelector('.max-h-24') as HTMLElement | null;
    expect(body).not.toBeNull();
    // 三行都在引用块里（空行也算一行），正文里没有那个裸 '>'
    expect(body!.querySelectorAll('.border-l-2')).toHaveLength(3);
    expect(body!.textContent).toBe('第一段第二段');
  });

  it('an empty steer event renders no row', () => {
    const b = withSteer();
    b.events[1] = { ...b.events[1], content: { text: '   ' } };
    expect(buildLines(b, {}, t).filter((l) => l.kind === 'steer')).toHaveLength(0);
  });

  it('is not counted in the collapsed headline', () => {
    const html = renderToStaticMarkup(
      React.createElement(SoloActivityRow, {
        block: withSteer(),
        uiMode: 'classic',
        embedVisualizations: false,
        turnDelivered: true,
      }),
    );
    // 两个文件读 → 统计只认工具
    expect(html).toContain('读取 2 个文件');
    expect(html).not.toContain('插话 1');
  });
});

describe('R4 — 折叠着也看得见那句插话', () => {
  const block = (): WorkflowBlock => ({
    events: [
      { _uid: 'c1', type: 'tool_call', content: { id: 'c1', name: 'filesystem__read_file', args: {} }, result: 'ok', resultStatus: 'success', timestamp: 1 },
      { _uid: 's1', type: 'user_steer', content: { text: STEER_TEXT }, timestamp: 2 },
      { _uid: 'c2', type: 'tool_call', content: { id: 'c2', name: 'filesystem__read_file', args: {} }, result: 'ok', resultStatus: 'success', timestamp: 3 },
    ],
    status: null,
    completed: true,
    started_ms: 1,
  });

  const render = () =>
    renderToStaticMarkup(
      React.createElement(SoloActivityRow, {
        block: block(),
        uiMode: 'classic',
        embedVisualizations: false,
        turnDelivered: true,
      }),
    );

  it('the collapsed fold still shows the interjection', () => {
    const html = render();
    expect(html).toContain('theme-stock-miner');
    expect(html).toContain('插话');
  });

  it('and shows it exactly once (the open-state copy is inside the fold)', () => {
    const html = render();
    expect(html.split('theme-stock-miner').length - 1).toBe(1);
  });

  it('a steer in a thought-only block is not swallowed by the thought branch', () => {
    // 只有思考 + 插话的块会撞上 isThoughtOnly 分支（那条分支只渲染思考正文），
    // 插话必须让它走普通分支，否则这句话直接消失。
    const html = renderToStaticMarkup(
      React.createElement(SoloActivityRow, {
        block: {
          events: [
            { _uid: 't1', type: 'thought', content: '先想想路径', timestamp: 1 },
            { _uid: 's1', type: 'user_steer', content: { text: STEER_TEXT }, timestamp: 2 },
          ],
          status: null,
          completed: true,
          started_ms: 1,
        } as WorkflowBlock,
        uiMode: 'classic',
        embedVisualizations: false,
        turnDelivered: true,
      }),
    );
    expect(html).toContain('theme-stock-miner');
  });

  it('the live path composes: 挂载 → 渲染，回合还在跑也看得见', () => {
    // R2 与 R4 各自只看一半，这里把两半接起来跑一遍（没有这一步，"挂上去了但
    // 渲染不出来"这类断链两条单测都发现不了）。
    const live: TimelineEntry[] = [
      { kind: 'message', data: { role: 'user', content: '干活' } as any, _uid: 'u1' },
      {
        kind: 'workflow',
        data: {
          events: [
            { _uid: 'c1', type: 'tool_call', content: { id: 'c1', name: 'filesystem__read_file', args: {} }, result: 'ok', resultStatus: 'success', timestamp: 1 },
          ],
          status: 'working',
          completed: false,
        },
        _uid: 'w1',
      },
    ];
    const after = appendUserSteerToTimeline(live, STEER_TEXT) as TimelineEntry[];
    const html = renderToStaticMarkup(
      React.createElement(SoloActivityRow, {
        block: blocksOf(after)[0].data,
        uiMode: 'classic',
        embedVisualizations: false,
      }),
    );
    expect(html).toContain('theme-stock-miner');
  });
});

describe('R5 — 接线', () => {
  it('the consumer nests into the fold instead of sealing', () => {
    const svc = PAGE.slice(PAGE.indexOf("svc.on('steer_consumed'"));
    // 必须是真的调用结果；`null && append…` 这种「看起来调用了」不算接线。
    expect(svc).toMatch(/const steered = appendUserSteerToTimeline\(prevBucket, text\);/);
  });

  it('and keeps the bubble only as the fallback', () => {
    // 挂不上 fold 时才封口 + 气泡
    const svc = PAGE.slice(PAGE.indexOf("svc.on('steer_consumed'"));
    expect(svc).toMatch(/steered \?\? \[/);
    expect(svc).toContain('sealIncompleteWorkflows(');
  });

  it('the runner marks the drained mid-turn message as a steer', () => {
    const block = TURN_LOOP.slice(TURN_LOOP.indexOf('for evt in _raw_events'));
    expect(block).toContain('steer=True');
    expect(block).toContain('client_id=_steer_cid or None');
  });

  it('the closed-state copy is gated so the row cannot render twice', () => {
    expect(FOLD).toMatch(/\{!outerOpen && steerLines\.length > 0 \?/);
  });

  it('a steer keeps a thought-heavy block out of the thought-only branch', () => {
    // 否则那句插话会随 isThoughtOnly 分支被丢掉
    const pred = FOLD.slice(FOLD.indexOf('const isThoughtOnly'));
    expect(pred).toContain("l.kind === 'steer'");
  });
});
