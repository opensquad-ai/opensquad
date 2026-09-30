// @vitest-environment jsdom
/**
 * GitRepoBar — the answers a refused git command must turn into.
 *
 * The backend refuses a command by answering 200 with `{ok:false, code}`, and
 * the whole point of that code is that the UI does something *other* than print
 * a message: a dirty worktree opens the stash question, an unmerged branch opens
 * the force-delete question, and any other refusal surfaces as the chip's
 * floating notice worded by its code. That mapping is the thing worth pinning;
 * the API is mocked so each test states one refusal and asserts the follow-up,
 * with no git and no launcher.
 *
 *   R1  the chip loads status on mount, and only asks for branches when needed
 *   R2  a dirty worktree opens the stash confirmation, and the answer retries
 *   R3  deleting an unmerged branch needs the force confirmation
 *   R4  a refusal with a known code is worded by code; an unknown one is not
 *
 * Mutations verified:
 *   MR1  checkout: print an error instead of the stash question    → R2
 *   MR2  stash confirm: re-call without `stash_dirty`              → R2
 *   MR3  delete: use the plain confirm spec for `not_merged`       → R3
 *   MR4  failText: always `t('git.error.generic')`                 → R4
 *
 * `React.createElement` on purpose: the vitest `include` glob is `**\/*.test.ts`.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../../i18n';
import { gitAPI, type GitStatus } from '../../services/api';
import { GitRepoBar } from './GitRepoBar';

vi.mock('../../services/api', () => ({
  gitAPI: {
    status: vi.fn(),
    branches: vi.fn(),
    checkout: vi.fn(),
    deleteBranch: vi.fn(),
  },
}));

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;
const api = vi.mocked(gitAPI);

const repoStatus = (over: Partial<GitStatus> = {}): GitStatus => ({
  is_repo: true,
  name: 'repo',
  branch: 'dev',
  ahead: 0,
  behind: 0,
  counts: { staged: 0, unstaged: 0, untracked: 0, conflicts: 0, total: 0 },
  ...over,
});

const branchList = () => ({
  current: 'dev',
  default_branch: 'main',
  local: [
    { name: 'dev', sha: 'aaaaaaa', ts: 1_700_000_000, current: true },
    { name: 'main', sha: 'bbbbbbb', ts: 1_700_000_000 },
  ],
  remote: [],
});

let container: HTMLDivElement;
let root: Root;

const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`);

const click = (id: string) => {
  const el = byId(id) as HTMLElement | null;
  expect(el, `[data-testid="${id}"] did not render`).toBeTruthy();
  act(() => {
    el!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
};

/** Mount the bar and let the mount-time status request settle. */
async function render() {
  await act(async () => {
    root.render(h(GitRepoBar, { agentId: 'agent-1', cwd: 'C:/work/repo' }));
  });
}

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.clearAllMocks();
  localStorage.clear();
  // jsdom ships no matchMedia; the confirm modal's overlay reads it on mount.
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
  api.status.mockResolvedValue(repoStatus());
  api.branches.mockResolvedValue(branchList());
  await act(async () => {
    await i18n.changeLanguage('zh');
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('R1 — what the chip fetches, and when', () => {
  it('loads status on mount, and leaves the branch list alone until asked', async () => {
    await render();
    expect(api.status).toHaveBeenCalledWith('agent-1', 'C:/work/repo');
    expect(api.branches).not.toHaveBeenCalled();
    expect(container.textContent).toContain('dev');
  });

  it('requests the branch list when the picker opens', async () => {
    await render();
    await act(async () => {
      click('repo-branch');
    });
    expect(api.branches).toHaveBeenCalledWith('agent-1', 'C:/work/repo');
    expect(container.textContent).toContain('main');
  });

  it('renders grey inert chips for a workspace that is not a repository', async () => {
    api.status.mockResolvedValue({ is_repo: false } as GitStatus);
    await render();
    expect(byId('repo-status-bar')).toBeTruthy();
    const branch = byId('repo-branch')!;
    expect(branch.tagName).toBe('SPAN');
    expect(branch.textContent).toContain('非 Git 仓库');
    expect(container.querySelector('button[data-testid="repo-branch"]')).toBeNull();
  });
});

describe('R2 — a dirty worktree before a switch', () => {
  it('asks about stashing, and the answer retries with stash_dirty', async () => {
    api.checkout
      .mockResolvedValueOnce({
        ok: false,
        code: 'dirty_worktree',
        error: 'The worktree has uncommitted changes',
        paths: ['a.txt', 'b.txt'],
        counts: { staged: 0, unstaged: 2, untracked: 0, conflicts: 0, total: 2 },
      })
      .mockResolvedValueOnce({ ok: true, branch: 'main', stashed: true });

    await render();
    await act(async () => {
      click('repo-branch');
    });
    await act(async () => {
      const rows = [...container.querySelectorAll('[data-testid="branch-row"]')] as HTMLElement[];
      rows.find((el) => el.textContent?.includes('main'))!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(api.checkout).toHaveBeenCalledTimes(1);
    expect(byId('git-confirm-stash-switch')).toBeTruthy();
    expect(container.textContent).toContain('暂存改动后切换');
    expect(container.textContent).toContain('a.txt');

    await act(async () => {
      click('git-confirm-stash-switch-go');
    });
    expect(api.checkout).toHaveBeenLastCalledWith('agent-1', {
      branch: 'main',
      root: 'C:/work/repo',
      create: undefined,
      base: undefined,
      stash_dirty: true,
    });
    expect(container.textContent).toContain('已暂存改动并切换到 main');
  });
});

describe('R3 — deleting an unmerged branch', () => {
  it('asks first, then asks again for the force delete', async () => {
    api.deleteBranch
      .mockResolvedValueOnce({ ok: false, code: 'not_merged', error: 'not fully merged' })
      .mockResolvedValueOnce({ ok: true });

    await render();
    await act(async () => {
      click('repo-branch');
    });
    await act(async () => {
      const deletes = [...container.querySelectorAll('[data-testid="branch-delete"]')] as HTMLElement[];
      expect(deletes.length).toBeGreaterThan(0);
      deletes[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    // Nothing is deleted before the user says so.
    expect(api.deleteBranch).not.toHaveBeenCalled();
    expect(byId('git-confirm-delete')).toBeTruthy();

    await act(async () => {
      click('git-confirm-delete-go');
    });
    expect(api.deleteBranch).toHaveBeenLastCalledWith('agent-1', 'main', 'C:/work/repo', false);

    expect(byId('git-confirm-delete-force')).toBeTruthy();
    await act(async () => {
      click('git-confirm-delete-force-go');
    });
    expect(api.deleteBranch).toHaveBeenLastCalledWith('agent-1', 'main', 'C:/work/repo', true);
    expect(container.textContent).toContain('已删除分支 main');
  });
});

describe('R4 — how a refusal is worded', () => {
  it('uses the code’s own translation when there is one', async () => {
    api.checkout.mockResolvedValue({ ok: false, code: 'locked', error: 'index.lock' });
    await render();
    await act(async () => {
      click('repo-branch');
    });
    await act(async () => {
      const rows = [...container.querySelectorAll('[data-testid="branch-row"]')] as HTMLElement[];
      rows.find((el) => el.textContent?.includes('main'))!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(byId('repo-notice')?.textContent).toContain('仓库正忙');
    expect(byId('repo-notice')?.textContent).not.toContain('index.lock');
  });

  it('falls back to the generic line plus the raw reason for an unknown code', async () => {
    api.checkout.mockResolvedValue({ ok: false, code: 'brand_new_code', error: 'something odd' });
    await render();
    await act(async () => {
      click('repo-branch');
    });
    await act(async () => {
      const rows = [...container.querySelectorAll('[data-testid="branch-row"]')] as HTMLElement[];
      rows.find((el) => el.textContent?.includes('main'))!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(byId('repo-notice')?.textContent).toContain('操作失败');
    expect(byId('repo-notice')?.textContent).toContain('something odd');
  });
});
