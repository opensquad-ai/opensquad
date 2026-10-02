/**
 * The right panel's three top-level tabs (浏览器 / 终端 / 文件) and their contents.
 *
 * What matters here is structural, and a source scan is the honest way to check it: the tab
 * row replaced a title row inside a 2900-line component, the two new panels mount for their
 * own tab only, and the chat drawer that shares this component must not grow tabs. The
 * browser panel is also rendered for real (its URL handling is the risky part); the terminal
 * panel is only scanned, because importing it would open a websocket.
 */
import fs from 'node:fs';
import path from 'node:path';

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { BrowserPanel, isSelfOrigin, normalizeUrl } from './BrowserPanel';

/** Paths are relative to this test file (components/ai-chat/). */
const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

const PANEL = 'ProjectFilesPanel.tsx';
const ELECTRON_MAIN = path.resolve(__dirname, '..', '..', 'electron', 'main.ts');

describe('right panel tabs', () => {
  it('the file area header is now a tab row with three co-equal tabs', () => {
    const src = read(PANEL);

    expect(src).toContain('data-testid="panel-tabs"');
    expect(src).toContain('role="tablist"');
    expect(src).toContain('PANEL_TABS.map');
    expect(src).toContain('data-testid={`panel-tab-${id}`}');
    expect(src).toContain('PANEL_TAB_ICONS');
    // the row sits where the title was: same band, and the title only survives for the
    // chat drawer's treeOnly usage
    expect(src).toMatch(/h-11[^"]*border-b border-border[\s\S]{0,120}treeOnly \?/);
    expect(src).toContain("t('aiChat.workspaceFiles')");
  });

  it('each tab renders its own panel, and the chat drawer stays a file list', () => {
    const src = read(PANEL);

    expect(src).toContain("panelTab === 'terminal'");
    expect(src).toContain('<TerminalPanel agentId={agentId} rootPath={rootPath} />');
    expect(src).toContain("panelTab === 'browser'");
    expect(src).toContain('<BrowserPanel />');
    // both are gated on !treeOnly, so ChatDetailDrawer (treeOnly) never sees them
    expect(src).toMatch(/!treeOnly && panelTab === 'terminal'/);
    expect(src).toMatch(/!treeOnly && panelTab === 'browser'/);
    // the file body itself is untouched — it is the fallback branch
    expect(src).toContain('{listPane}');
    expect(src).toContain('{useSplitPreview ?');
  });

  it('remembers the chosen tab, and the file tab keeps its own actions', () => {
    const src = read(PANEL);

    expect(src).toContain('initialPanelTab(treeOnly)');
    expect(src).toContain('writePanelTab(id)');
    // search / new / preview belong to the file list; the other tabs carry their own
    expect(src).toMatch(/!treeOnly && panelTab !== 'files' \? null : \(/);
    expect(src).toContain("onClick={() => setShowSearch");
  });

  it('the tab row adds no button that the changed-files test would pick up', () => {
    // projectFilesPanelChangedRow.test.ts finds the changed tab by its text; the new row
    // must not introduce a second match before it.
    const src = read(PANEL);
    const row = src.slice(src.indexOf('data-testid="panel-tabs"'), src.indexOf('role="tablist"') + 4000);

    expect(row).not.toMatch(/changed|变动/i);
  });

  it('the terminal drives the agent over the existing stream', () => {
    const src = read('TerminalPanel.tsx');

    expect(src).toContain('getAiWsService(agentId)');
    expect(src).toContain('openTerminal(terminalId');
    expect(src).toContain('writeTerminal(terminalId');
    expect(src).toContain('interruptTerminal(terminalId)');
    expect(src).toContain('closeTerminal(terminalId)');
    // output is filtered to this terminal's own id — no cross-talk with agent jobs
    expect(src).toContain("svc.on('job_stdout'");
    expect(src).toContain("svc.on('job_status'");
    expect(src).toContain("const tunnel = `${ID_PREFIX}${terminalId}`");
    expect(src).toContain("if (String(d.job_id || '') !== tunnel) return;");
    // the panel echoes the typed line itself (a piped shell does not), and says what a
    // no-PTY terminal cannot do
    expect(src).toContain('PROMPT');
    expect(src).toContain("t('aiChat.terminal.noTtyNote')");
    expect(src).toContain('data-testid="terminal-input"');
    expect(src).toContain('data-testid="terminal-output"');
  });

  it('the terminal opens on mount and stops its shell on unmount', () => {
    const src = read('TerminalPanel.tsx');
    const effect = src.slice(src.indexOf('Open on mount'), src.indexOf('// Keep the newest output'));

    expect(effect).toContain('open();');
    expect(effect).toContain('closeTerminal(terminalId)');
  });

  it('the terminal commands exist on the socket service', () => {
    const api = read('../../services/aiWebSocket.ts');

    for (const method of ['openTerminal', 'writeTerminal', 'interruptTerminal', 'closeTerminal']) {
      expect(api, method).toContain(`${method}(`);
    }
    expect(api).toContain("this._sendCommand('terminal_open'");
    expect(api).toContain("this._sendCommand('terminal_write'");
    expect(api).toContain("this._sendCommand('terminal_interrupt'");
    expect(api).toContain("this._sendCommand('terminal_close'");
  });
});

describe('browser panel', () => {
  it('normalises what the user types', () => {
    expect(normalizeUrl('localhost:9555')).toBe('http://localhost:9555');
    expect(normalizeUrl('127.0.0.1:9001/x')).toBe('http://127.0.0.1:9001/x');
    expect(normalizeUrl('192.168.5.4:8080')).toBe('http://192.168.5.4:8080');
    expect(normalizeUrl('example.com')).toBe('https://example.com');
    expect(normalizeUrl('https://example.com/a?b=1')).toBe('https://example.com/a?b=1');
    expect(normalizeUrl('   ')).toBe('');
  });

  it('refuses to frame this app itself (same-origin guests could reach the parent)', () => {
    // the origin is passed in explicitly: this suite has no DOM
    expect(isSelfOrigin('http://localhost:9555/', 'http://localhost:9555')).toBe(true);
    expect(isSelfOrigin('http://localhost:9555/x', 'http://localhost:9555')).toBe(true);
    expect(isSelfOrigin('http://example.com', 'http://localhost:9555')).toBe(false);
  });

  it('renders an address bar and, in a browser, a sandboxed iframe', () => {
    const html = renderToStaticMarkup(React.createElement(BrowserPanel));

    expect(html).toContain('browser-panel');
    expect(html).toContain('data-testid="browser-address"');
    expect(html).toContain('data-testid="browser-go"');
    expect(html).toContain('data-testid="browser-open-external"');
    // no URL yet: nothing is framed, the user is told what to do
    expect(html).not.toContain('<iframe');
  });

  it('uses a real webview under Electron and an iframe elsewhere', () => {
    const src = read('BrowserPanel.tsx');

    expect(src).toContain("window.electronEnv?.isElectron");
    expect(src).toContain("React.createElement('webview'");
    expect(src).toContain('partition: \'persist:opensquad-browser\'');
    expect(src).toContain('data-testid="browser-iframe"');
    // a same-origin URL is never framed: it is offered externally instead
    expect(src).toContain('setRefused(true)');
    expect(src).toContain("t('aiChat.browser.selfOrigin')");
  });

  it('the Electron shell enables the webview and hardens it', () => {
    const main = fs.readFileSync(ELECTRON_MAIN, 'utf8');

    expect(main).toContain('webviewTag:       true');
    expect(main).toContain("on('will-attach-webview'");
    expect(main).toContain('delete (webPreferences as { preloadURL?: string }).preloadURL');
    expect(main).toContain('setWindowOpenHandler');
    // …and external links go to the OS browser, http(s) only
    expect(main).toContain('async function openExternal');
    expect(main).toMatch(/parsed\.protocol !== 'http:' && parsed\.protocol !== 'https:'/);
  });

  it('the tab and panel labels exist in both locales', () => {
    const locales = path.resolve(__dirname, '..', '..', 'locales');

    for (const locale of ['zh', 'en']) {
      const json = JSON.parse(fs.readFileSync(path.join(locales, `${locale}.json`), 'utf8'));
      for (const key of ['panelTabs', 'browser', 'terminal']) {
        expect(json.aiChat[key], `${locale}.${key}`).toBeTruthy();
      }
      expect(json.aiChat.panelTabs.browser, locale).toBeTruthy();
      expect(json.aiChat.panelTabs.terminal, locale).toBeTruthy();
      expect(json.aiChat.panelTabs.files, locale).toBeTruthy();
    }
  });
});
