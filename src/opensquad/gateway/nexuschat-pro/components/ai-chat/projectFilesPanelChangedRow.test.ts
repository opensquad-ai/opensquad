// @vitest-environment jsdom
/**
 * Behavioural lock for「工作区文件 → 变动文件」点文件名不打开文件、而是展开 diff.
 *
 * The panel is rendered in tree-only mode (AIChatPage passes `treeOnly` +
 * `onOpenFile`), where the contract is: the chevron toggles the inline diff
 * accordion, the file name opens the file in the centre editor. The 所有文件
 * tab already did that; the 变动文件 row wired BOTH the chevron and the name to
 * `toggleChangedExpand`, so a name click only expanded the diff and the file
 * could not be opened from that tab at all.
 *
 * L1  clicking the changed file's NAME calls `onOpenFile(path)` and leaves the
 *     row collapsed (the chevron's title does not flip);
 * L2  clicking the CHEVRON flips the row to expanded without calling
 *     `onOpenFile` — the two affordances are independent.
 *
 * `React.createElement` on purpose: the vitest `include` glob is `**\/*.test.ts`.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import '../../i18n';

const listSessionChanges = vi.fn();
const listProjectTree = vi.fn();
const getSessionDiffsBatch = vi.fn();
const getSessionDiff = vi.fn();

vi.mock('../../services/api', () => ({
  adminAPI: {
    listSessionChanges: (...a: unknown[]) => listSessionChanges(...a),
    listProjectTree: (...a: unknown[]) => listProjectTree(...a),
    getSessionDiffsBatch: (...a: unknown[]) => getSessionDiffsBatch(...a),
    getSessionDiff: (...a: unknown[]) => getSessionDiff(...a),
  },
}));

import { ProjectFilesPanel } from './ProjectFilesPanel';

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
const PATH = 'scripts/_find_dsh.ps1';

const changedFile = {
  name: '_find_dsh.ps1',
  path: PATH,
  type: 'file',
  status: 'M',
  additions: 15,
  deletions: 0,
  oversized: false,
  mtime: 1_700_000_000_000,
  size: 120,
  created: false,
};

let container: HTMLDivElement;
let root: Root;

/** Flush the promise chains the panel kicks off on load / click. */
const flush = () =>
  act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

const buttons = () => Array.from(container.querySelectorAll('button'));

function nameButton(): HTMLButtonElement {
  const found = buttons().find((b) => (b.textContent || '').trim() === PATH);
  if (!found) throw new Error(`no name button for ${PATH}`);
  return found;
}

/** The row's chevron: first button in the same row as the name button. */
function chevronOf(btn: HTMLButtonElement): HTMLButtonElement {
  const row = btn.parentElement;
  if (!row) throw new Error('name button has no row');
  return row.querySelectorAll('button')[0];
}

const titleOf = (el: Element | null | undefined) => el?.getAttribute('title') ?? null;

async function renderChangedTab(onOpenFile: (p: string) => void) {
  await act(async () => {
    root.render(
      h(ProjectFilesPanel, {
        isOpen: true,
        onClose: () => {},
        agentId: 'agent-1',
        rootPath: '/proj',
        width: 320,
        onWidthChange: () => {},
        treeOnly: true,
        onOpenFile,
      }),
    );
  });
  await flush();

  const tab = buttons().find((b) => /changed|变动/i.test(b.textContent || ''));
  if (!tab) {
    throw new Error(
      `changed tab button not found; buttons=${JSON.stringify(buttons().map((b) => b.textContent))} html=${container.innerHTML.slice(0, 400)}`,
    );
  }
  await act(async () => {
    tab.click();
  });
  await flush();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = NoopResizeObserver;
  // jsdom ships neither of these; useSoftPresence reads matchMedia on mount.
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    media: '',
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
  listSessionChanges.mockReset().mockResolvedValue({
    files: [changedFile],
    additions: 15,
    deletions: 0,
    count: 1,
  });
  listProjectTree.mockReset().mockResolvedValue({ entries: [], truncated: false, count: 0 });
  getSessionDiffsBatch
    .mockReset()
    .mockResolvedValue({ files: { [PATH]: { lines: [], additions: 15, deletions: 0 } } });
  getSessionDiff.mockReset().mockResolvedValue({ lines: [], additions: 15, deletions: 0 });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('changed-files row: open vs expand', () => {
  it('opens the file (does not expand) when the name is clicked', async () => {
    const onOpenFile = vi.fn();
    await renderChangedTab(onOpenFile);

    const name = nameButton();
    const chevronTitle = titleOf(chevronOf(name));

    await act(async () => {
      name.click();
    });
    await flush();

    expect(onOpenFile).toHaveBeenCalledTimes(1);
    expect(onOpenFile).toHaveBeenCalledWith(PATH);
    // The name click must not have doubled as the expand toggle.
    expect(titleOf(chevronOf(nameButton()))).toBe(chevronTitle);
  });

  it('expands the diff (does not open the file) when the chevron is clicked', async () => {
    const onOpenFile = vi.fn();
    await renderChangedTab(onOpenFile);

    const chevronTitle = titleOf(chevronOf(nameButton()));

    await act(async () => {
      chevronOf(nameButton()).click();
    });
    await flush();

    expect(onOpenFile).not.toHaveBeenCalled();
    // Expanded ⇔ the chevron's title flipped to the collapse label.
    expect(titleOf(chevronOf(nameButton()))).not.toBe(chevronTitle);
  });
});
