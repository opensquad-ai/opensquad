// @vitest-environment jsdom
/**
 * 导入 Cookie：面板上那个按钮要真的把导出文件送到 agent 的浏览器，并且顺手镜像一份到本窗口
 * 自己的 partition —— 「我的浏览器」和 agent 的浏览器是两个不同的 Chromium，只做一半就会出现
 * 「一个视图登录了、另一个没登录」。
 *
 * Electron 走文件选择器拿 *路径*（渲染进程读不了文件），网页版让用户选文件后上传文本；这里
 * 固定走 Electron 那条，两条分支共用同一个后端解析。
 *
 * `React.createElement` 而非 JSX：vitest 的 include 是 `**\/*.test.ts`。
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import i18n from '../../i18n';
import { BrowserPanel, agentBrowserSessionId } from './BrowserPanel';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

const mocks = vi.hoisted(() => ({
  open: vi.fn(),
  frame: vi.fn(),
  importCookies: vi.fn(),
  pickCookieFile: vi.fn(),
  importBrowserCookies: vi.fn(),
}));

vi.mock('../../services/api', () => ({
  browserAPI: { open: mocks.open, frame: mocks.frame, importCookies: mocks.importCookies },
}));

let container: HTMLDivElement;
let root: Root;

const EXPORT_PATH = 'C:\\Users\\me\\Desktop\\cookies.txt';
const COOKIES = [{ name: 'sid', value: 'abc', domain: '.example.com', path: '/' }];

const clickImport = async () => {
  const button = container.querySelector<HTMLButtonElement>('[data-testid="browser-import-cookies"]');
  expect(button).toBeTruthy();
  await act(async () => {
    button!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
  });
};

const notice = () => container.querySelector('[data-testid="browser-cookie-notice"]')?.textContent || '';

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.open.mockReset().mockResolvedValue({ ok: true, session_id: 'agent-a1' });
  mocks.frame.mockReset().mockResolvedValue({ ok: true, png: '', url: '', title: '' });
  mocks.pickCookieFile.mockReset().mockResolvedValue(EXPORT_PATH);
  mocks.importBrowserCookies.mockReset().mockResolvedValue({ ok: true, stored: 1, failed: [] });
  mocks.importCookies.mockReset().mockResolvedValue({
    ok: true,
    added: 1,
    skipped_total: 0,
    cookies: COOKIES,
    source: 'netscape',
  });
  await act(async () => {
    await i18n.changeLanguage('zh');
  });
  (window as unknown as { electronEnv?: unknown }).electronEnv = {
    isElectron: true,
    pickCookieFile: mocks.pickCookieFile,
    importBrowserCookies: mocks.importBrowserCookies,
  };
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(h(BrowserPanel, { agentId: 'a1' }));
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete (window as unknown as { electronEnv?: unknown }).electronEnv;
});

describe('导入 Cookie 按钮', () => {
  it('sends the picked path to the agent browser session', async () => {
    await clickImport();

    expect(mocks.pickCookieFile).toHaveBeenCalledTimes(1);
    expect(mocks.importCookies).toHaveBeenCalledWith('a1', agentBrowserSessionId('a1'), { path: EXPORT_PATH });
    expect(notice()).toBe(i18n.t('aiChat.browser.importDone', { count: 1, skipped: 0 }));
  });

  it('mirrors the imported cookies into this window, or only one view would be logged in', async () => {
    await clickImport();

    expect(mocks.importBrowserCookies).toHaveBeenCalledWith(COOKIES);
  });

  it('does nothing when the file dialog is dismissed', async () => {
    mocks.pickCookieFile.mockResolvedValue(null);
    await clickImport();

    expect(mocks.importCookies).not.toHaveBeenCalled();
    expect(notice()).toBe('');
  });

  it('shows the reason the server gave, instead of failing silently', async () => {
    mocks.importCookies.mockResolvedValue({ ok: false, error: 'invalid JSON: line 1' });
    await clickImport();

    expect(notice()).toBe('invalid JSON: line 1');
    expect(mocks.importBrowserCookies).not.toHaveBeenCalled();
  });
});
