// @vitest-environment jsdom
/**
 * The branch chip: the one git surface left in the composer footer.
 *
 * While no status exists the bar is silent (loading, failed request), but a
 * directory without a repository keeps the chips visible and inert — grey,
 * unclickable, the branch slot labelled "非 Git 仓库". The assertions pin what
 * remains: the branch label, the picker wiring (opening asks for the list, a
 * selection checks out, creating passes name + base), the floating notice pill
 * for a refused command, and the inert non-repo rendering. The row is
 * presentational, so every assertion is about a callback firing or a label
 * coming from i18n.
 *
 *   R1  no status yet → nothing; a non-repo → grey inert chips
 *   R2  the chip names the branch and asks for the list when it opens
 *   R3  picker selection switches; remote rows pass the qualified name
 *   R4  the create-branch form calls back with a name and base
 *   R5  a notice renders as the floating pill above the chip
 *   R6  the mode chip shows the current mode and offers the two options
 *   R7  a started session's mode chip is read-only
 *
 * Mutations verified:
 *   MR1  non-repo: drop the inert branch (render the live chip)             → R1
 *   MR2  non-repo: drop the inert mode chip (render the menu button)        → R1
 *   MR3  branch button: never call `onOpenBranches`                         → R2
 *   MR4  notice pill: drop the tone class                                   → R5
 *   MR5  mode option: call back even when it is the current mode            → R6
 *   MR6  modeEditable=false still renders the menu button                   → R7
 *
 * `React.createElement` on purpose: the vitest `include` glob is `**\/*.test.ts`.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../../i18n';
import type { GitBranchList, GitStatus } from '../../services/api';
import { RepoStatusBar } from './RepoStatusBar';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

const repoStatus = (over: Partial<GitStatus> = {}): GitStatus => ({
  is_repo: true,
  name: 'opensquad_deploy_test',
  repo_root: 'C:/work/opensquad_deploy_test',
  branch: 'dev',
  ahead: 2,
  behind: 3,
  counts: { staged: 1, unstaged: 2, untracked: 2, conflicts: 0, total: 5 },
  stash_count: 1,
  remotes: [{ name: 'origin', url: 'https://example.test/repo.git' }],
  ...over,
});

const branchList = (): GitBranchList => ({
  current: 'dev',
  default_branch: 'main',
  local: [
    { name: 'dev', sha: 'aaaaaaa', ts: 1_700_000_000, current: true },
    { name: 'main', sha: 'bbbbbbb', ts: 1_700_000_000 },
    { name: 'feature/x', sha: 'ccccccc', ts: 1_700_000_000 },
  ],
  remote: [{ name: 'main', sha: 'ddddddd', ts: 1_700_000_000, remote: 'origin' }],
});

let container: HTMLDivElement;
let root: Root;
let handlers: {
  onCheckout: ReturnType<typeof vi.fn>;
  onDeleteBranch: ReturnType<typeof vi.fn>;
  onOpenBranches: ReturnType<typeof vi.fn>;
  onModeChange: ReturnType<typeof vi.fn>;
};

function render(props: Partial<React.ComponentProps<typeof RepoStatusBar>> = {}) {
  act(() => {
    root.render(h(RepoStatusBar, { status: repoStatus(), ...handlers, ...props }));
  });
}

/** Element by test id, queried inside this test's container. */
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`);

const click = (id: string) => {
  const el = byId(id) as HTMLElement | null;
  expect(el, `[data-testid="${id}"] did not render`).toBeTruthy();
  act(() => {
    el!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
};

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  handlers = {
    onCheckout: vi.fn(),
    onDeleteBranch: vi.fn(),
    onOpenBranches: vi.fn(),
    onModeChange: vi.fn(),
  };
  await act(async () => {
    await i18n.changeLanguage('zh');
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('R1 — no status vs. not a repository', () => {
  it('is absent before the first status arrives', () => {
    render({ status: null, loading: true });
    expect(byId('repo-status-bar')).toBeNull();
    render({ status: null, loading: false });
    expect(byId('repo-status-bar')).toBeNull();
  });

  it('renders grey inert chips for a workspace that is not a repository', () => {
    render({ status: { is_repo: false } as GitStatus, mode: 'local' });
    expect(byId('repo-status-bar')).toBeTruthy();

    const branch = byId('repo-branch')!;
    expect(branch.tagName).toBe('SPAN');
    expect(branch.textContent).toContain('非 Git 仓库');
    expect(container.querySelector('button[data-testid="repo-branch"]')).toBeNull();
    expect(byId('branch-picker')).toBeNull();

    const modeChip = byId('repo-mode')!;
    expect(modeChip.tagName).toBe('SPAN');
    expect(modeChip.getAttribute('data-mode')).toBe('local');
    // The inert variant, not the locked-session variant: the tooltip names the
    // reason (no repository), not the lock.
    expect(modeChip.getAttribute('title')).toContain('非 Git 仓库');
    expect(container.querySelector('button[data-testid="repo-mode"]')).toBeNull();
    expect(container.querySelectorAll('[data-testid="repo-mode-option"]').length).toBe(0);
  });

  it('keeps the inert chips when git itself is unavailable', () => {
    render({ status: { is_repo: false, error: 'git not found on PATH' } as GitStatus, mode: 'local' });
    expect(byId('repo-branch')?.textContent).toContain('非 Git 仓库');
    expect(byId('repo-branch')?.tagName).toBe('SPAN');
  });
});

describe('R2 — the chip', () => {
  it('names the branch and asks for the list when it opens', () => {
    render({ branches: branchList() });
    expect(container.textContent).toContain('dev');
    expect(byId('branch-picker')).toBeNull();

    click('repo-branch');
    expect(handlers.onOpenBranches).toHaveBeenCalledTimes(1);
    expect(byId('branch-picker')).toBeTruthy();
    expect(container.textContent).toContain('本地分支');
    expect(container.textContent).toContain('远程分支');
  });
});

describe('R3 — switching', () => {
  it('checks out the picked branch by name, not by its remote-qualified label', () => {
    render({ branches: branchList() });
    click('repo-branch');

    const rows = [...container.querySelectorAll('[data-testid="branch-row"]')] as HTMLElement[];
    const main = rows.find((el) => el.textContent?.includes('main'))!;
    act(() => {
      main.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(handlers.onCheckout).toHaveBeenCalledWith('main');
    // The popover plays its exit animation, so it stays mounted for a moment —
    // what matters is that it is no longer the open one.
    expect(byId('branch-picker')?.className).toContain('os-pop-menu-out');
  });

  it('passes the remote-qualified name for a remote row', () => {
    render({ branches: branchList() });
    click('repo-branch');
    const rows = [...container.querySelectorAll('[data-testid="branch-row"]')] as HTMLElement[];
    const remoteRow = rows.find((el) => el.textContent?.startsWith('origin/main'))!;
    act(() => {
      remoteRow.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(handlers.onCheckout).toHaveBeenCalledWith('origin/main');
  });
});

describe('R4 — creating a branch', () => {
  it('calls back with the typed name and base', () => {
    render({ branches: branchList() });
    click('repo-branch');
    click('branch-create');

    const inputs = [...container.querySelectorAll('input')] as HTMLInputElement[];
    const [nameInput, baseInput] = inputs.slice(-2);
    const setValue = (el: HTMLInputElement, value: string) => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(el, value);
      act(() => {
        el.dispatchEvent(new Event('input', { bubbles: true }));
      });
    };
    setValue(nameInput, 'feature/new');
    setValue(baseInput, 'main');
    click('branch-create-confirm');
    expect(handlers.onCheckout).toHaveBeenCalledWith('feature/new', { create: true, base: 'main' });
  });
});

describe('R5 — a refused command', () => {
  it('surfaces as the floating pill above the chip', () => {
    render({ notice: { tone: 'error', text: '仓库正忙' } });
    expect(byId('repo-notice')?.textContent).toContain('仓库正忙');
  });

  it('leaves no pill when there is nothing to say', () => {
    render();
    expect(byId('repo-notice')).toBeNull();
  });
});

describe('R6 — the mode chip', () => {
  it('shows the current mode, offers both, and calls back with the pick', () => {
    render({ mode: 'local', modeEditable: true });
    expect(byId('repo-mode')?.getAttribute('data-mode')).toBe('local');
    expect(byId('repo-mode')?.tagName).toBe('BUTTON');

    click('repo-mode');
    const options = [...container.querySelectorAll('[data-testid="repo-mode-option"]')] as HTMLElement[];
    expect(options.map((el) => el.getAttribute('data-mode'))).toEqual(['local', 'worktree']);

    const worktree = options.find((el) => el.getAttribute('data-mode') === 'worktree')!;
    act(() => {
      worktree.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(handlers.onModeChange).toHaveBeenCalledWith('worktree');
  });

  it('does not call back for the mode that is already active', () => {
    render({ mode: 'worktree', modeEditable: true });
    click('repo-mode');
    const options = [...container.querySelectorAll('[data-testid="repo-mode-option"]')] as HTMLElement[];
    const active = options.find((el) => el.getAttribute('data-mode') === 'worktree')!;
    act(() => {
      active.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(handlers.onModeChange).not.toHaveBeenCalled();
  });

  it('is absent when the page does not offer switching', () => {
    render();
    expect(byId('repo-mode')).toBeNull();
  });
});

describe('R7 — a started session cannot change mode', () => {
  it('renders the chip read-only, with no menu to open', () => {
    render({ mode: 'worktree', modeEditable: false });
    const chip = byId('repo-mode')!;
    expect(chip.tagName).toBe('SPAN');
    expect(chip.getAttribute('data-mode')).toBe('worktree');
    // Nothing clickable: no button, no menu options anywhere.
    expect(container.querySelector('button[data-testid="repo-mode"]')).toBeNull();
    expect(container.querySelectorAll('[data-testid="repo-mode-option"]').length).toBe(0);
  });
});
