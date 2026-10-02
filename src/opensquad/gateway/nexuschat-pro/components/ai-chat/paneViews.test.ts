/**
 * Terminal and browser as pane views (the reference: closable `cmd ×` / `Browser ×` tabs in
 * the workspace pane, and a welcome list of 更改 / 文件 / 终端 / 浏览器 when it is empty).
 *
 * Two things are worth pinning. First the plumbing that makes a new tab kind actually
 * render: `parseContentTabKey` is a whitelist, so a kind that is missing there silently
 * shows "nothing open" with the tab still in the list. Second, that the shortcut printed on
 * a welcome row is the same one that opens it — a row advertising a dead key is worse than
 * no hint at all.
 */
import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { PANE_VIEW_IDS, PANE_VIEW_SHORTCUT, RAIL_VIEWS, matchesPaneViewShortcut, paneViewForKey } from '../../utils/paneViews';
import { parseContentTabKey } from '../../utils/workspaceStore';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');
const SHELL = read('WorkspacePaneShell.tsx');
const TAB_BAR = read('ContentTabBar.tsx');
const PAGE = read('../AIChatPage.tsx');
const RAIL = read('ProjectFilesPanel.tsx');

/** A keydown-shaped object, so the matcher can be tested without a DOM. */
const key = (k: string, mods: Partial<Record<'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey', boolean>> = {}) => ({
  key: k,
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  altKey: false,
  ...mods,
});

describe('pane views', () => {
  it('lists the four views the welcome shows', () => {
    expect(PANE_VIEW_IDS).toEqual(['changes', 'files', 'terminal', 'browser']);
    // the two file-ish views live in the right-hand rail, the other two are pane tabs
    expect(RAIL_VIEWS).toEqual(['changes', 'files']);
  });

  it('matches exactly the shortcut it prints', () => {
    expect(matchesPaneViewShortcut(key('`', { ctrlKey: true }), 'terminal')).toBe(true);
    expect(matchesPaneViewShortcut(key('~', { ctrlKey: true }), 'terminal')).toBe(true); // shift+`
    expect(matchesPaneViewShortcut(key('b', { ctrlKey: true, shiftKey: true }), 'browser')).toBe(true);
    expect(matchesPaneViewShortcut(key('e', { ctrlKey: true, shiftKey: true }), 'files')).toBe(true);
    expect(matchesPaneViewShortcut(key('g', { metaKey: true, shiftKey: true }), 'changes')).toBe(true);

    // …and nothing else does
    expect(matchesPaneViewShortcut(key('`'), 'terminal')).toBe(false); // no modifier
    expect(matchesPaneViewShortcut(key('b', { ctrlKey: true }), 'browser')).toBe(false); // missing shift
    expect(matchesPaneViewShortcut(key('b', { ctrlKey: true, shiftKey: true }), 'terminal')).toBe(false);
    expect(matchesPaneViewShortcut(key('x', { ctrlKey: true, shiftKey: true }), 'browser')).toBe(false);
  });

  it('resolves a keydown to one view', () => {
    expect(paneViewForKey(key('b', { ctrlKey: true, shiftKey: true }))).toBe('browser');
    expect(paneViewForKey(key('a', { ctrlKey: true }))).toBeNull();
  });

  it('every printed shortcut is a real combination', () => {
    for (const id of PANE_VIEW_IDS) {
      expect(PANE_VIEW_SHORTCUT[id], id).toMatch(/^Ctrl\+/);
    }
  });
});

describe('the new tab kinds parse', () => {
  it('round-trips a terminal and a browser tab', () => {
    expect(parseContentTabKey('terminal:terminal')).toEqual({ kind: 'terminal', id: 'terminal' });
    expect(parseContentTabKey('browser:browser')).toEqual({ kind: 'browser', id: 'browser' });
  });

  it('still refuses a kind it does not know, and an empty id', () => {
    expect(parseContentTabKey('nonsense:x')).toBeNull();
    expect(parseContentTabKey('terminal:')).toBeNull();
    expect(parseContentTabKey(null)).toBeNull();
  });
});

describe('the pane renders them', () => {
  it('has a flag and a branch for each', () => {
    expect(SHELL).toContain("active.kind === 'terminal'");
    expect(SHELL).toContain("active.kind === 'browser'");
    expect(SHELL).toContain('data-testid="pane-terminal"');
    expect(SHELL).toContain('data-testid="pane-browser"');
    expect(SHELL).toContain('<TerminalPanel agentId={agentId} rootPath={rootPath} />');
    expect(SHELL).toContain('<BrowserPanel />');
    // a render throw costs the pane that view, not the workspace
    expect(SHELL).toContain('<ErrorBoundary label="terminal"');
    expect(SHELL).toContain('<ErrorBoundary label="browser"');
  });

  it('keeps a live terminal (and a loaded page) mounted across tab switches', () => {
    // remounting the terminal would kill the shell; remounting the webview would reload a
    // page the user was logged into
    expect(SHELL).toContain('const [terminalMounted, setTerminalMounted] = useState(false)');
    expect(SHELL).toContain('const [browserMounted, setBrowserMounted] = useState(false)');
    expect(SHELL).toContain("tabs.open.some((t) => t.kind === 'terminal')");
    expect(SHELL).toContain("tabs.open.some((t) => t.kind === 'browser')");
  });

  it('names the tabs like the reference', () => {
    expect(SHELL).toContain("t('aiChat.panelTabs.terminal')");
    expect(SHELL).toContain("t('aiChat.panelTabs.browser')");
    // the tab bar has an icon for each kind
    expect(TAB_BAR).toContain("kind === 'terminal'");
    expect(TAB_BAR).toContain("kind === 'browser'");
    expect(TAB_BAR).toContain('Terminal');
    expect(TAB_BAR).toContain('Globe');
  });

  it('the welcome lists all four views, with the shortcut that opens each', () => {
    expect(SHELL).toContain('data-testid="pane-welcome"');
    expect(SHELL).toContain('PANE_VIEWS.map');
    expect(SHELL).toContain('data-testid={`pane-view-${view.id}`}');
    expect(SHELL).toContain('{view.shortcut}');
    expect(SHELL).toContain('PANE_VIEW_SHORTCUT');
    for (const view of PANE_VIEW_IDS) {
      expect(SHELL, view).toContain(`'${view}'`);
    }
    // the rows ask the page (the rail is not reachable from the pane directly)
    for (const handler of ['onOpenChanges', 'onOpenFiles', 'onOpenTerminal', 'onOpenBrowser']) {
      expect(SHELL, handler).toContain(`${handler}?:`);
      expect(SHELL, handler).toContain(`handler: '${handler}'`);
    }
  });
});

describe('the page wires them', () => {
  it('opens a terminal/browser tab in the focused pane', () => {
    const opener = PAGE.slice(PAGE.indexOf('const openPaneView'), PAGE.indexOf('const handleOpenTerminal'));

    expect(opener).toContain('openContentTab(agentId, activeWorkspace.id, { kind: view, id: view }, pane)');
    expect(opener).toContain('setFocusedPane(agentId, pane)');
    // 更改 / 文件 are the rail, not a tab
    expect(opener).toContain('openFilesRail(');
    expect(opener).toContain('setFilesPanelOpen(true)');
  });

  it('binds the same shortcuts the rows print', () => {
    const start = PAGE.indexOf('const onKey = (event: KeyboardEvent)');
    const end = PAGE.indexOf("window.addEventListener('keydown', onKey)", start);
    const hotkeys = PAGE.slice(start, end + 240); // the effect, including its cleanup

    expect(hotkeys).toContain('paneViewForKey(event)');
    expect(hotkeys).toContain('window.addEventListener(\'keydown\', onKey)');
    expect(hotkeys).toContain('window.removeEventListener(\'keydown\', onKey)');
    // typing in a field is never intercepted
    expect(hotkeys).toContain("target.tagName === 'INPUT'");
    expect(hotkeys).toContain('event.preventDefault()');
  });

  it('hands the pane shell the four welcome callbacks', () => {
    expect(PAGE).toContain("onOpenTerminal: () => openPaneView('terminal')");
    expect(PAGE).toContain("onOpenBrowser: () => openPaneView('browser')");
    expect(PAGE).toContain("onOpenChanges: () => openPaneView('changes')");
    expect(PAGE).toContain("onOpenFiles: () => openPaneView('files')");
  });

  it('the rail obeys the 更改 / 文件 rows, and no longer carries its own tab row', () => {
    expect(RAIL).toContain('OPEN_FILES_RAIL_EVENT');
    expect(RAIL).toContain("setTab(wanted === 'changed' ? 'changed' : 'all')");
    expect(RAIL).toContain('window.removeEventListener(OPEN_FILES_RAIL_EVENT');
    // the pane owns the tabs now — the rail is a file list with its own 变动/所有 sub-tabs
    expect(RAIL).not.toContain('data-testid="panel-tabs"');
    expect(RAIL).not.toContain('panelTab ===');
    expect(RAIL).toContain("t('aiChat.workspaceFiles')");
  });
});

describe('the panels themselves', () => {
  it('the terminal still streams over the existing events', () => {
    const terminal = read('TerminalPanel.tsx');

    expect(terminal).toContain("svc.on('job_stdout'");
    expect(terminal).toContain("if (String(d.job_id || '') !== tunnel) return;");
    expect(terminal).toContain("t('aiChat.terminal.noTtyNote')");
  });

  it('the browser still has both renderers, and the desktop shell is hardened', () => {
    const browser = read('BrowserPanel.tsx');
    const main = fs.readFileSync(path.resolve(__dirname, '..', '..', 'electron', 'main.ts'), 'utf8');

    expect(browser).toContain("React.createElement('webview'");
    expect(browser).toContain('data-testid="browser-iframe"');
    expect(main).toContain('webviewTag:       true');
    expect(main).toContain("on('will-attach-webview'");
    expect(main).toContain('setWindowOpenHandler');
  });

  it('every new label exists in both locales', () => {
    for (const locale of ['zh', 'en']) {
      const json = JSON.parse(read(`../../locales/${locale}.json`));
      expect(json.aiChat.welcome.title, locale).toBeTruthy();
      expect(json.aiChat.welcome.subtitle, locale).toBeTruthy();
      for (const view of PANE_VIEW_IDS) {
        expect(json.aiChat.views[view], `${locale}.${view}`).toBeTruthy();
        expect(json.aiChat.views[`${view}Hint`], `${locale}.${view}Hint`).toBeTruthy();
      }
      expect(json.aiChat.panelTabs.terminal, locale).toBeTruthy();
      expect(json.aiChat.panelTabs.browser, locale).toBeTruthy();
    }
  });
});
