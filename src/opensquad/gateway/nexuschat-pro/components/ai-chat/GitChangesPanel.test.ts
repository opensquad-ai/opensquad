// @vitest-environment jsdom
/**
 * GitChangesPanel — staging, discarding, committing and undoing from the panel.
 *
 * The panel's job is to keep two lists apart (this conversation's files vs git's
 * uncommitted ones) and to route each checked row to the one operation it can
 * have. The derivations are pinned in `utils/gitChanges.test.ts`; what is pinned
 * here is that the panel actually *uses* them: which endpoint fires with which
 * paths, that a destructive click asks first, and that the commit form refuses
 * the two things it cannot do.
 *
 *   R1  a non-repo workspace says so instead of showing an empty list
 *   R2  本对话 lists the session baseline, annotating (and locking) git state
 *   R3  全部 lists git's view, including files the conversation never touched
 *   R4  select-all + 暂存 sends only the stageable paths
 *   R5  取消暂存 sends the staged row back
 *   R6  丢弃 asks first, and asks with `confirm_untracked` when new files are in it
 *   R7  the commit form reports the missing title, then the empty index
 *   R8  撤销上次提交 is hidden where it could not work, and asks before running
 *   R9  expanding a row asks for the diff mode that row is in
 *
 * Mutations verified:
 *   MR1  stage: send `rows.map(path)` (ignore the selection)          → R4
 *   MR2  unstage: call `gitAPI.stage`                                 → R5
 *   MR3  discard: send `confirm_untracked: false` always              → R6
 *   MR4  commit button: drop the `blocker` from `disabled`            → R7
 *       (the early return inside `doCommit` is a second line of defence
 *        that jsdom cannot reach — a disabled button swallows the click)
 *   MR5  undo button: render unconditionally                          → R8
 *   MR6  expand: derive the mode as a constant `worktree`             → R9
 *   MR7  scope chip: keep the row set on the session scope            → R3
 *
 * `React.createElement` on purpose: the vitest `include` glob is `**\/*.test.ts`.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../../i18n';
import { gitAPI, type GitStatus } from '../../services/api';
import { GitChangesPanel } from './GitChangesPanel';

vi.mock('../../services/api', () => ({
  gitAPI: {
    status: vi.fn(),
    branches: vi.fn(),
    init: vi.fn(),
    checkout: vi.fn(),
    deleteBranch: vi.fn(),
    mergeAbort: vi.fn(),
    fetch: vi.fn(),
    pull: vi.fn(),
    push: vi.fn(),
    syncStatus: vi.fn(),
    diff: vi.fn(),
    stage: vi.fn(),
    unstage: vi.fn(),
    discard: vi.fn(),
    commit: vi.fn(),
    undoCommit: vi.fn(),
  },
}));

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;
const api = vi.mocked(gitAPI);
const ROOT_PATH = 'C:/work/repo';

const repoStatus = (over: Partial<GitStatus> = {}): GitStatus => ({
  is_repo: true,
  branch: 'dev',
  upstream: 'origin/dev',
  ahead: 0,
  behind: 0,
  counts: { staged: 0, unstaged: 0, untracked: 0, conflicts: 0, total: 0 },
  ...over,
});

/** a.ts edited, b.ts staged, new.txt new, both.txt conflicted. */
const MIXED = repoStatus({
  staged: [{ path: 'src/b.ts', status: 'M' }],
  unstaged: [{ path: 'src/a.ts', status: 'M' }],
  untracked: [{ path: 'new.txt', status: '?' }],
  conflicts: [{ path: 'both.txt', status: 'UU' }],
  counts: { staged: 1, unstaged: 1, untracked: 1, conflicts: 1, total: 4 },
});

const SESSION_ROWS = [
  { name: 'a.ts', path: 'src/a.ts', additions: 3, deletions: 1 },
  { name: 'gone.ts', path: 'src/gone.ts', additions: 0, deletions: 0 },
];

let container: HTMLDivElement;
let root: Root;
let onChanged: ReturnType<typeof vi.fn>;

const byId = (id: string, within: ParentNode = container) =>
  within.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;

const allById = (id: string, within: ParentNode = container) =>
  [...within.querySelectorAll(`[data-testid="${id}"]`)] as HTMLElement[];

const click = (id: string, within: ParentNode = container) => {
  const el = byId(id, within);
  expect(el, `[data-testid="${id}"] did not render`).toBeTruthy();
  act(() => {
    el!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
};

/** Click the checkbox of the row whose data-path ends with `suffix`. */
const checkRow = (suffix: string) => {
  const row = allById('git-change-row').find((el) => el.getAttribute('data-path')?.endsWith(suffix));
  expect(row, `no row ending in ${suffix}`).toBeTruthy();
  const box = byId('git-change-check', row!);
  expect(box, `row ${suffix} has no checkbox`).toBeTruthy();
  act(() => {
    (box as HTMLInputElement).click();
  });
};

const rowPaths = () =>
  allById('git-change-row').map((el) => el.getAttribute('data-path') || '');

async function render(props: Partial<React.ComponentProps<typeof GitChangesPanel>> = {}) {
  await act(async () => {
    root.render(
      h(GitChangesPanel, {
        agentId: 'agent-1',
        rootPath: ROOT_PATH,
        sessionRows: SESSION_ROWS,
        onChanged,
        ...props,
      }),
    );
  });
}

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.clearAllMocks();
  onChanged = vi.fn();
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
  api.status.mockResolvedValue(MIXED);
  api.stage.mockResolvedValue({ ok: true });
  api.unstage.mockResolvedValue({ ok: true });
  api.discard.mockResolvedValue({ ok: true });
  api.commit.mockResolvedValue({ ok: true });
  api.undoCommit.mockResolvedValue({ ok: true });
  api.diff.mockResolvedValue({ path: 'x', status: 'M', additions: 1, deletions: 0, lines: [] });
  await act(async () => {
    await i18n.changeLanguage('zh');
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('R1 — a workspace with no repository', () => {
  it('says so instead of offering an empty list', async () => {
    api.status.mockResolvedValue({ is_repo: false });
    await render();
    expect(byId('git-changes-no-repo')?.textContent).toContain('不是 Git 仓库');
    expect(byId('git-changes-rows')).toBeNull();
  });
});

describe('R2 — 本对话', () => {
  it('lists the session baseline and locks the rows git cannot see', async () => {
    await render();
    expect(rowPaths()).toEqual(['src/a.ts', 'src/gone.ts']);
    expect(byId('git-changes')).toBeTruthy();

    const touched = allById('git-change-row').find((el) => el.getAttribute('data-path') === 'src/a.ts')!;
    expect(byId('git-change-check', touched)).toBeTruthy();
    expect((byId('git-change-check', touched) as HTMLInputElement).disabled).toBe(false);

    const gone = allById('git-change-row').find((el) => el.getAttribute('data-path') === 'src/gone.ts')!;
    expect((byId('git-change-check', gone) as HTMLInputElement).disabled).toBe(true);
  });
});

describe('R3 — 全部', () => {
  it('switches to git’s row set, including files the conversation never touched', async () => {
    await render();
    await act(async () => {
      click('git-scope-all');
    });
    // git's four rows plus the baseline-only one, so nothing the session changed
    // disappears just because git no longer sees it.
    expect(rowPaths()).toEqual(['both.txt', 'new.txt', 'src/a.ts', 'src/b.ts', 'src/gone.ts']);
    await act(async () => {
      click('git-scope-session');
    });
    expect(rowPaths()).toEqual(['src/a.ts', 'src/gone.ts']);
  });
});

describe('R4 — 暂存', () => {
  it('sends only the stageable paths, never the whole list', async () => {
    await render();
    expect(byId('git-stage')!.hasAttribute('disabled')).toBe(true);
    await act(async () => {
      click('git-select-all');
    });
    expect(byId('git-changes-selected')?.textContent).toContain('已选 1');
    await act(async () => {
      click('git-stage');
    });
    expect(api.stage).toHaveBeenCalledWith('agent-1', ['src/a.ts'], ROOT_PATH);
  });
});

describe('R5 — 取消暂存', () => {
  it('takes a staged row back out of the index', async () => {
    await render();
    await act(async () => {
      click('git-scope-all');
    });
    await act(async () => {
      checkRow('src/b.ts');
    });
    await act(async () => {
      click('git-unstage');
    });
    expect(api.unstage).toHaveBeenCalledWith('agent-1', ['src/b.ts'], ROOT_PATH);
    expect(api.stage).not.toHaveBeenCalled();
  });
});

describe('R6 — 丢弃', () => {
  it('asks first, and does nothing until the answer comes', async () => {
    await render();
    await act(async () => {
      checkRow('src/a.ts');
    });
    await act(async () => {
      click('git-discard');
    });
    expect(api.discard).not.toHaveBeenCalled();
    expect(container.textContent).toContain('丢弃改动');
    await act(async () => {
      click('git-confirm-discard-go');
    });
    expect(api.discard).toHaveBeenCalledWith('agent-1', ['src/a.ts'], ROOT_PATH, false);
    expect(onChanged).toHaveBeenCalled();
  });

  it('consents to deleting new files by name when the selection has one', async () => {
    await render();
    await act(async () => {
      click('git-scope-all');
    });
    await act(async () => {
      click('git-select-all');
    });
    await act(async () => {
      click('git-discard');
    });
    expect(container.textContent).toContain('新文件');
    await act(async () => {
      click('git-confirm-discard-go');
    });
    const [, paths, , confirmUntracked] = api.discard.mock.calls[0];
    // Row order, and the staged row is discardable too (unstage + revert).
    expect(paths).toEqual(['new.txt', 'src/a.ts', 'src/b.ts']);
    expect(confirmUntracked).toBe(true);
  });
});

describe('R7 — the commit form', () => {
  it('names the empty title first, then the empty index, and only fires when both are there', async () => {
    await render();
    expect(byId('git-commit-hint')?.textContent).toContain('请填写提交标题');
    expect(byId('git-commit')!.hasAttribute('disabled')).toBe(true);

    await act(async () => {
      click('git-select-all');
    });
    await act(async () => {
      click('git-stage');
    });
    // Staged nothing yet (the action is async) — the staged list drives the form.
    const title = byId('git-commit-title') as HTMLInputElement;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(title, 'fix: thing');
      title.dispatchEvent(new Event('input', { bubbles: true }));
    });

    // Both halves of the guard: the button says it is unavailable, and a click
    // that gets through anyway does not reach the endpoint.
    await act(async () => {
      click('git-commit');
    });
    expect(api.commit).not.toHaveBeenCalled();
  });

  it('commits with the title and description once something is staged', async () => {
    api.status.mockResolvedValue(repoStatus({ staged: [{ path: 'src/b.ts', status: 'M' }] }));
    // The staged file has to be in 本对话's list for this scope to offer commit —
    // staging a file the conversation never touched is the 全部 view's job.
    await render({ sessionRows: [...SESSION_ROWS, { name: 'b.ts', path: 'src/b.ts' }] });
    const type = (id: string, value: string) => {
      const el = byId(id) as HTMLInputElement | HTMLTextAreaElement;
      // A textarea has its own `value` accessor; the input one rejects it.
      const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!;
      setter.call(el, value);
      act(() => {
        el.dispatchEvent(new Event('input', { bubbles: true }));
      });
    };
    act(() => {
      type('git-commit-title', 'fix: staged work');
      type('git-commit-description', 'because');
    });
    expect(byId('git-commit')!.hasAttribute('disabled')).toBe(false);
    await act(async () => {
      click('git-commit');
    });
    expect(api.commit).toHaveBeenCalledWith('agent-1', 'fix: staged work', 'because', ROOT_PATH);
    expect((byId('git-commit-title') as HTMLInputElement).value).toBe('');
  });
});

describe('R8 — 撤销上次提交', () => {
  it('is hidden when the last commit is already on the remote', async () => {
    api.status.mockResolvedValue(repoStatus({ upstream: 'origin/dev', ahead: 0 }));
    await render();
    expect(byId('git-undo-commit')).toBeNull();
  });

  it('asks before undoing a commit that is still local', async () => {
    api.status.mockResolvedValue(repoStatus({ upstream: 'origin/dev', ahead: 2 }));
    await render();
    await act(async () => {
      click('git-undo-commit');
    });
    expect(api.undoCommit).not.toHaveBeenCalled();
    expect(container.textContent).toContain('撤销上次提交');
    await act(async () => {
      click('git-confirm-undo-commit-go');
    });
    expect(api.undoCommit).toHaveBeenCalledWith('agent-1', ROOT_PATH);
  });
});

describe('R9 — expanding a row', () => {
  it('asks for the diff mode the row is in', async () => {
    await render();
    await act(async () => {
      click('git-scope-all');
    });
    await act(async () => {
      checkRow('src/b.ts');
    });
    const stagedRow = allById('git-change-row').find((el) => el.getAttribute('data-path') === 'src/b.ts')!;
    await act(async () => {
      click('git-change-toggle', stagedRow);
    });
    expect(api.diff).toHaveBeenCalledWith('agent-1', 'src/b.ts', ROOT_PATH, { mode: 'staged' });

    const modifiedRow = allById('git-change-row').find((el) => el.getAttribute('data-path') === 'src/a.ts')!;
    await act(async () => {
      click('git-change-toggle', modifiedRow);
    });
    expect(api.diff).toHaveBeenLastCalledWith('agent-1', 'src/a.ts', ROOT_PATH, { mode: 'worktree' });
  });
});
