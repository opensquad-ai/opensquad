/**
 * The right panel's top-level tab list: order, labels, and the remembered selection.
 *
 * The panel used to be a single file area; the file list is now one of three co-equal tabs,
 * so the list itself is the contract — a test that spells it out is what stops a tab from
 * silently disappearing (or the chat drawer, which mounts the same panel, from growing
 * tabs it should not have).
 */
import { describe, expect, it } from 'vitest';

import {
  DEFAULT_PANEL_TAB,
  PANEL_TAB_LABELS,
  PANEL_TABS,
  initialPanelTab,
  isPanelTabId,
  readPanelTab,
  writePanelTab,
} from './panelTabs';

const fakeStorage = (initial: Record<string, string> = {}): Storage => {
  const map = new Map(Object.entries(initial));
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (k: string) => map.get(k) ?? null,
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    removeItem: (k: string) => void map.delete(k),
    setItem: (k: string, v: string) => void map.set(k, v),
  } as Storage;
};

describe('panel tabs', () => {
  it('lists the three tools, browser first and files third', () => {
    expect(PANEL_TABS).toEqual(['browser', 'terminal', 'files']);
    expect(DEFAULT_PANEL_TAB).toBe('files');
  });

  it('has a label and a tooltip for every tab', () => {
    for (const id of PANEL_TABS) {
      expect(PANEL_TAB_LABELS[id]?.labelKey, id).toBeTruthy();
      expect(PANEL_TAB_LABELS[id]?.hintKey, id).toBeTruthy();
    }
  });

  it('recognises only its own ids', () => {
    expect(isPanelTabId('browser')).toBe(true);
    expect(isPanelTabId('files')).toBe(true);
    expect(isPanelTabId('changed')).toBe(false);
    expect(isPanelTabId(undefined)).toBe(false);
  });

  it('remembers the selection', () => {
    const storage = fakeStorage();

    writePanelTab('terminal', storage);

    expect(readPanelTab(storage)).toBe('terminal');
  });

  it('falls back to files for a missing or corrupt value', () => {
    expect(readPanelTab(fakeStorage())).toBe('files');
    expect(readPanelTab(fakeStorage({ 'opensquad.panel.tab': 'nonsense' }))).toBe('files');
  });

  it('survives storage being unavailable', () => {
    const broken = {
      getItem: () => {
        throw new Error('private mode');
      },
      setItem: () => {
        throw new Error('private mode');
      },
    } as unknown as Storage;

    expect(readPanelTab(broken)).toBe('files');
    expect(() => writePanelTab('browser', broken)).not.toThrow();
  });

  it('the chat drawer (treeOnly) is always the file list', () => {
    const storage = fakeStorage({ 'opensquad.panel.tab': 'browser' });

    expect(initialPanelTab(true, storage)).toBe('files');
    // …while the work/code panel honours what the user last picked
    expect(initialPanelTab(false, storage)).toBe('browser');
  });
});
