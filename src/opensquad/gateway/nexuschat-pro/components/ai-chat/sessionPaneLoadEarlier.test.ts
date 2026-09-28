// @vitest-environment jsdom
/**
 * Behavioural lock for "刷新后只能看到上一个任务流，更早的加载不出来".
 *
 * The source scan (`utils/sessionPanePaging.scan.test.ts`) proves the wiring
 * exists. This file proves the *one case the scroll path cannot cover*: a first
 * page that fits inside the viewport. Measured live on 2026-09-28 — the pane's
 * scroller sat at `scrollHeight === clientHeight === 623` for a session whose
 * server page reported `has_more: true, total_messages: 1000` — so no scroll
 * event ever fires, `onNearTop` never runs, and the older history is
 * unreachable. The `data-load-earlier` button is the only way in.
 *
 * L1  a page with `has_more` renders the entry, and clicking it fetches the
 *     NEXT page (offset = messages already painted, anchored on the oldest
 *     message painted) and prepends it;
 * L2  the entry disappears once the server says there is nothing older (a
 *     control that promises more than exists is the same bug in reverse);
 * L3  a complete session (no `has_more`) never renders it at all.
 *
 * Written with `React.createElement`: the vitest `include` glob is
 * `**\/*.test.ts`, so this file must not be `.tsx`.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import '../../i18n';

const getSessionHistoryPaged = vi.fn();

vi.mock('../../services/api', () => ({
  agentSessionAPI: {
    getSessionHistoryPaged: (...args: unknown[]) => getSessionHistoryPaged(...args),
  },
}));

import { SessionChatPane } from './SessionChatPane';
import { invalidateCachedSessionTimeline } from '../../utils/sessionTimelineCache';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const h = React.createElement;

const msg = (id: string, role: 'user' | 'assistant', content: string) => ({
  message_id: id,
  role,
  content,
  timestamp: 1_700_000_000_000,
});

const page = (
  messages: ReturnType<typeof msg>[],
  hasMore: boolean,
  total: number,
) => ({
  session: {
    id: 'sess-1',
    messages,
    events: [],
    archived_messages: [],
    archived_events: [],
    has_more: hasMore,
    total_messages: total,
  },
});

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  (globalThis as any).ResizeObserver = NoopResizeObserver;
  getSessionHistoryPaged.mockReset();
  // The timeline cache is module-level (and deliberately survives a remount) —
  // a cache hit would let one test paint the previous test's entries and skip
  // the fetch entirely.
  invalidateCachedSessionTimeline('agent-1');
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  try {
    localStorage.clear();
  } catch {
    /* jsdom without storage */
  }
});

const mount = async () => {
  await act(async () => {
    root.render(
      h(SessionChatPane, { agentId: 'agent-1', sessionId: 'sess-1' }),
    );
  });
  // Let the first fetch resolve and its state flush.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

const entry = () => container.querySelector('[data-load-earlier="1"]') as HTMLElement | null;

describe('L1 — 装不满视口的一页：按钮取下一页，并把更早的消息拼到前面', () => {
  it('点一下拿到 offset=2 + 锚点，旧消息进时间线，按钮随之消失', async () => {
    getSessionHistoryPaged
      .mockResolvedValueOnce(page([msg('m3', 'user', '第三轮'), msg('m4', 'assistant', '第三轮回答')], true, 4))
      .mockResolvedValueOnce(page([msg('m1', 'user', '第一轮'), msg('m2', 'assistant', '第一轮回答')], false, 4));
    await mount();

    expect(container.textContent).toContain('第三轮');
    expect(entry()).toBeTruthy();
    expect(container.textContent).toContain('加载更早的消息');

    await act(async () => {
      entry()!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(getSessionHistoryPaged).toHaveBeenCalledTimes(2);
    // 第一页 offset=0；第二页必须带 offset=已画条数，且锚在"已画的最旧一条"上
    const second = getSessionHistoryPaged.mock.calls[1];
    expect(second[0]).toBe('agent-1');
    expect(second[1]).toBe('sess-1');
    expect(second[2]).toBe(2);
    expect(second[4]).toBe('m3');

    expect(container.textContent).toContain('第一轮');
    expect(container.textContent).toContain('第三轮');
    // 服务器说没有了 → 入口必须收掉
    expect(entry()).toBeNull();
  });
});

describe('L2/L3 — 没有更早的了就不该给入口', () => {
  it('第一页 has_more=false：不渲染按钮', async () => {
    getSessionHistoryPaged.mockResolvedValueOnce(page([msg('m1', 'user', '唯一一轮')], false, 1));
    await mount();
    expect(container.textContent).toContain('唯一一轮');
    expect(entry()).toBeNull();
    expect(getSessionHistoryPaged).toHaveBeenCalledTimes(1);
  });
});
