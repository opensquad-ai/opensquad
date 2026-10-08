// @vitest-environment jsdom
/**
 * 邀请卡点「参与」之后的应答态 —— 真渲染、真点击。
 *
 * 卡片读的是看板（5 秒轮询），但**观看者本人不是看板参与人**，所以轮询永远刷不出
 * 他自己的应答：点完「参与」按钮曾一直可点（同目录 CollabTaskCard.tsx 的注释也记了
 * 这条）。这一份把卡片真的挂到 DOM 上，回答两个问题：
 *
 *   1. 点下去 → onRespond('accept') 被调用一次，按钮变成「已参与」且不可再点；
 *   2. onRespond 抛错 → 按钮回到可点，能重试（一次失败不能把卡片锁死）。
 *
 * `React.createElement` 而非 JSX：vitest 的 include 是 `**\/*.test.ts`。
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import type { CollabTaskCardPayload } from '../services/api';
import { CollabTaskCard } from './CollabTaskCard';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

// The card polls the board on mount; keep it off the network.
const { taskSummary } = vi.hoisted(() => ({
  taskSummary: vi.fn(async () => ({ participants: [], status: 'active' })),
}));
vi.mock('../services/api', () => ({ collabBoardAPI: { taskSummary } }));

const payload: CollabTaskCardPayload = {
  v: 1,
  id: 'ctask_abc123',
  kind: 'invite',
  collab_id: 'AB12CD',
  title: '发布流程',
  summary: '做一个审核流程',
  card: 'software_dev_team',
  participants: [{ agent_id: 'qa', name: 'QA', state: 'invited' }],
};

let container: HTMLDivElement;
let root: Root;

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  taskSummary.mockClear();
  await act(async () => {
    await i18n.changeLanguage('zh');
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const button = () => container.querySelector<HTMLButtonElement>('[data-testid="collab-task-respond"]');

async function render(onRespond: (action: 'accept' | 'decline') => void | Promise<void>) {
  await act(async () => {
    root.render(h(CollabTaskCard, { payload, onOpen: vi.fn(), onRespond }));
    // let the mount-time board read land inside this act window
    await Promise.resolve();
  });
}

const click = (el: Element) =>
  act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

describe('an invite is answered from the card, once', () => {
  it('shows the answer button, then disables it once the answer lands', async () => {
    const onRespond = vi.fn(async () => undefined);
    await render(onRespond);

    const before = button();
    expect(before, '邀请卡没有应答按钮').toBeTruthy();
    expect(before!.disabled).toBe(false);
    expect(before!.textContent).toBe('收到');

    await click(before!);

    expect(onRespond).toHaveBeenCalledTimes(1);
    expect(onRespond).toHaveBeenCalledWith('accept');
    const after = button()!;
    expect(after.disabled, '应答后按钮仍可点').toBe(true);
    expect(after.textContent).toBe('已参与');
  });

  it('stays clickable when the answer fails, so it can be retried', async () => {
    const onRespond = vi.fn(async () => {
      throw new Error('boom');
    });
    await render(onRespond);

    await click(button()!);

    expect(onRespond).toHaveBeenCalledTimes(1);
    const after = button()!;
    expect(after.disabled, '一次失败把卡片锁死了').toBe(false);
    expect(after.textContent).toBe('收到');
  });
});
