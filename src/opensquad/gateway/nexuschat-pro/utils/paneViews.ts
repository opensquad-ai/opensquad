/**
 * What a pane can open, and the shortcut that opens it.
 *
 * One list, two consumers: the pane's welcome rows print these shortcuts and the Agent Web
 * page binds them, so a row can never advertise a key that does nothing — and a test can
 * assert both sides from a single source.
 */
import { FileDiff, Files, Globe, Terminal } from 'lucide-react';

export type PaneViewId = 'changes' | 'files' | 'terminal' | 'browser';

export const PANE_VIEW_IDS: PaneViewId[] = ['changes', 'files', 'terminal', 'browser'];

/**
 * Editor-conventional keys (VS Code: explorer / source control / terminal). `Ctrl` is the
 * primary modifier on Windows and Linux and `⌘` on macOS — the matcher accepts either.
 */
export const PANE_VIEW_SHORTCUT: Record<PaneViewId, string> = {
  changes: 'Ctrl+Shift+G',
  files: 'Ctrl+Shift+E',
  terminal: 'Ctrl+`',
  browser: 'Ctrl+Shift+B',
};

/** The two views that live in the right-hand files rail rather than in a pane tab. */
export const RAIL_VIEWS: PaneViewId[] = ['changes', 'files'];

/**
 * The view list, once: the pane's welcome rows, the tab bar's "open" menu and the shortcut
 * binder all read it, so none of them can offer an entry the others do not have.
 */
export const PANE_VIEWS: Array<{
  id: PaneViewId;
  Icon: typeof FileDiff;
  labelKey: string;
  hintKey: string;
  shortcut: string;
}> = [
  {
    id: 'changes',
    Icon: FileDiff,
    labelKey: 'aiChat.views.changes',
    hintKey: 'aiChat.views.changesHint',
    shortcut: PANE_VIEW_SHORTCUT.changes,
  },
  {
    id: 'files',
    Icon: Files,
    labelKey: 'aiChat.views.files',
    hintKey: 'aiChat.views.filesHint',
    shortcut: PANE_VIEW_SHORTCUT.files,
  },
  {
    id: 'terminal',
    Icon: Terminal,
    labelKey: 'aiChat.views.terminal',
    hintKey: 'aiChat.views.terminalHint',
    shortcut: PANE_VIEW_SHORTCUT.terminal,
  },
  {
    id: 'browser',
    Icon: Globe,
    labelKey: 'aiChat.views.browser',
    hintKey: 'aiChat.views.browserHint',
    shortcut: PANE_VIEW_SHORTCUT.browser,
  },
];

interface Combo {
  ctrl: boolean;
  shift: boolean;
  alt: boolean;
  key: string;
}

const parseCombo = (combo: string): Combo => {
  const parts = String(combo)
    .split('+')
    .map((p) => p.trim())
    .filter(Boolean);
  return {
    ctrl: parts.includes('Ctrl'),
    shift: parts.includes('Shift'),
    alt: parts.includes('Alt'),
    key: parts[parts.length - 1] || '',
  };
};

type KeyLike = Pick<KeyboardEvent, 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey' | 'key'>;

/** True when this keydown is the shortcut for `id`. */
export const matchesPaneViewShortcut = (event: KeyLike, id: PaneViewId): boolean => {
  const combo = parseCombo(PANE_VIEW_SHORTCUT[id]);
  if (combo.ctrl !== Boolean(event.ctrlKey || event.metaKey)) return false;
  if (combo.shift !== Boolean(event.shiftKey)) return false;
  if (combo.alt !== Boolean(event.altKey)) return false;
  const key = String(event.key || '');
  if (combo.key === '`') return key === '`' || key === '~';
  return key.toLowerCase() === combo.key.toLowerCase();
};

/** The view a keydown opens, or null. */
export const paneViewForKey = (event: KeyLike): PaneViewId | null =>
  PANE_VIEW_IDS.find((id) => matchesPaneViewShortcut(event, id)) ?? null;
