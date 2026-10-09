// @vitest-environment jsdom
/**
 * 浏览器面板的第二个视图：agent 那个内置浏览器的**只读**预览。
 *
 * 这一份真的把面板挂到 DOM 上，回答三件事：
 *
 *   1. 切到「Agent 浏览器」时用的是 agent 自己的会话 id（`agent-<dir_name>`，见
 *      `opensquad/tools/browser.py`），并以 headless 启动——看着它不该在桌面上多出一个窗口；
 *   2. 它轮询的是 `frame`（缓存好的那张图），拿到的帧会渲染成一张 `<img>`；
 *   3. 它是只读的：点那张图不会回传任何输入。
 *
 * 第 3 条是这份测试存在的理由。早先的版本会把点击转发给那个会话，于是点到了本机的另一个
 * 窗口、还追不上 700ms 的轮询，整个面板视图被回退（commit b7df0c0）。看着是功能，操作留给
 * agent——别再把这条退回去。
 *
 * `React.createElement` 而非 JSX：vitest 的 include 是 `**\/*.test.ts`。
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../../i18n';
import { BrowserPanel } from './BrowserPanel';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

// The panel talks to the launcher over HTTP; keep it off the network.
const { open, frame, click, type, press } = vi.hoisted(() => ({
  open: vi.fn(),
  frame: vi.fn(),
  click: vi.fn(),
  type: vi.fn(),
  press: vi.fn(),
}));
vi.mock('../../services/api', () => ({ browserAPI: { open, frame, click, type, press } }));

let container: HTMLDivElement;
let root: Root;

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  open.mockReset().mockResolvedValue({ ok: true, session_id: 'agent-ai002' });
  frame.mockReset().mockResolvedValue({
    ok: true,
    png: 'UE5H',
    url: 'https://example.com/',
    title: 'Example',
    headed: false,
    window_note: '',
  });
  click.mockReset();
  type.mockReset();
  press.mockReset();
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

describe('the agent browser preview', () => {
  it('mirrors the agent session, and forwards nothing back', async () => {
    await act(async () => {
      root.render(h(BrowserPanel, { agentId: 'ai002' }));
    });

    const agentTab = container.querySelector<HTMLButtonElement>('[data-testid="browser-mode-agent"]');
    expect(agentTab).toBeTruthy();

    await act(async () => {
      agentTab?.click();
    });

    // the agent's own session, headless: watching must not put a window on this desktop
    expect(open).toHaveBeenCalledWith('ai002', 'agent-ai002', true);
    expect(frame).toHaveBeenCalledWith('ai002', 'agent-ai002');

    const img = container.querySelector<HTMLImageElement>('[data-testid="browser-agent-frame"]');
    expect(img).toBeTruthy();
    expect(img?.getAttribute('src')).toBe('data:image/png;base64,UE5H');

    // read-only: a click on the picture never reaches the session
    await act(async () => {
      img?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(click).not.toHaveBeenCalled();
    expect(type).not.toHaveBeenCalled();
    expect(press).not.toHaveBeenCalled();
  });

  it('offers no preview tab when there is no agent to mirror', async () => {
    await act(async () => {
      root.render(h(BrowserPanel, {}));
    });

    expect(container.querySelector('[data-testid="browser-mode-agent"]')).toBeNull();
    // …and the user's own view is what is left
    expect(container.querySelector('[data-testid="browser-address"]')).toBeTruthy();
    expect(frame).not.toHaveBeenCalled();
  });
});
