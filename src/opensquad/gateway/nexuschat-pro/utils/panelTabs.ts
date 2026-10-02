/**
 * The right-hand panel's top-level tabs (Work/Code mode).
 *
 * The panel used to be one thing — a file area with a title row. It now hosts three
 * co-equal tools, so the tab list, its labels and its persisted selection live here rather
 * than being spelled out again in the component (or in a test).
 *
 * The chat drawer mounts the same panel with `treeOnly`, and must stay a plain file list:
 * callers pass `treeOnly` and get `files`, whatever is stored.
 */
export type PanelTabId = 'browser' | 'terminal' | 'files';

/** Order as shown, left to right. */
export const PANEL_TABS: PanelTabId[] = ['browser', 'terminal', 'files'];

export const DEFAULT_PANEL_TAB: PanelTabId = 'files';

const STORAGE_KEY = 'opensquad.panel.tab';

export const PANEL_TAB_LABELS: Record<PanelTabId, { labelKey: string; hintKey: string }> = {
  browser: { labelKey: 'aiChat.panelTabs.browser', hintKey: 'aiChat.browser.hint' },
  terminal: { labelKey: 'aiChat.panelTabs.terminal', hintKey: 'aiChat.terminal.hint' },
  files: { labelKey: 'aiChat.panelTabs.files', hintKey: 'aiChat.panelTabs.filesHint' },
};

export const isPanelTabId = (value: unknown): value is PanelTabId =>
  typeof value === 'string' && (PANEL_TABS as string[]).includes(value);

/** The remembered tab, or `files` (never throws: private mode has no storage). */
export const readPanelTab = (storage?: Storage): PanelTabId => {
  try {
    const raw = (storage ?? window.localStorage).getItem(STORAGE_KEY);
    return isPanelTabId(raw) ? raw : DEFAULT_PANEL_TAB;
  } catch {
    return DEFAULT_PANEL_TAB;
  }
};

export const writePanelTab = (id: PanelTabId, storage?: Storage): void => {
  try {
    (storage ?? window.localStorage).setItem(STORAGE_KEY, id);
  } catch {
    /* storage unavailable — the selection simply is not remembered */
  }
};

/** What the panel should start on: the chat drawer is always the file list. */
export const initialPanelTab = (treeOnly: boolean, storage?: Storage): PanelTabId =>
  treeOnly ? 'files' : readPanelTab(storage);
