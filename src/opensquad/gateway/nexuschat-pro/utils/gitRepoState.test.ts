/**
 * The wording rules behind the repo status bar, branch picker and sync results.
 *
 * These are the decisions the UI makes *before* it renders: which single action
 * the sync control becomes, what a detached HEAD is called, how branches are
 * grouped, and which sentence a failed command earns. They live here rather than
 * in a component test because they are pure — pinning them separately means a
 * behaviour change fails at the rule, not at a querySelector three layers up.
 *
 *   R1  syncAction picks pull before push, and fetch when there is nothing to do
 *   R2  branchDisplay / mergeState / uncommittedCount / conflictPaths read the
 *       status payload the launcher actually sends
 *   R3  groupBranches puts recently-used first, never duplicates the current
 *       branch, and drops a remembered branch that no longer exists
 *   R4  gitErrorKey only ever produces a key that exists in both locales
 *   R5  syncNoticeKey maps a settled task to the sentence it deserves
 *
 * Mutations verified:
 *   MR1  syncAction: `behind > 0 → 'push'`                     → R1
 *   MR2  syncAction: drop the `behind` branch (clean→fetch)     → R1
 *   MR3  branchDisplay: ignore `detached`                       → R2
 *   MR4  mergeState: check `rebase` before `merge`              → R2
 *   MR5  groupBranches: keep the current branch in `recent`     → R3
 *   MR6  groupBranches: skip the "branch still exists" lookup   → R3
 *   MR7  gitErrorKey: interpolate the raw code                  → R4
 *   MR8  syncNoticeKey: fetch → 'git.notice.pushed'             → R5
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import type { GitBranch, GitBranchList, GitStatus, GitSyncTask } from '../services/api';
import {
  branchDisplay,
  conflictPaths,
  gitErrorKey,
  groupBranches,
  mergeState,
  pushRecentBranch,
  shortSha,
  syncAction,
  syncNoticeKey,
  uncommittedCount,
  worktreeModeOf,
} from './gitRepoState';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf8'));

const repoStatus = (over: Partial<GitStatus> = {}): GitStatus => ({
  is_repo: true,
  branch: 'dev',
  ahead: 0,
  behind: 0,
  counts: { staged: 0, unstaged: 0, untracked: 0, conflicts: 0, total: 0 },
  ...over,
});

const branch = (name: string, over: Partial<GitBranch> = {}): GitBranch => ({
  name,
  sha: `${name}0000000`,
  ts: 1_700_000_000,
  ...over,
});

const branchList = (over: Partial<GitBranchList> = {}): GitBranchList => ({
  current: 'dev',
  default_branch: 'main',
  local: [],
  remote: [],
  ...over,
});

const task = (over: Partial<GitSyncTask> = {}): GitSyncTask => ({
  ok: true,
  task_id: 't1',
  op: 'fetch',
  status: 'completed',
  ...over,
});

describe('R1 — which sync action the bar offers', () => {
  it('pulls while the remote is ahead, even when we also have local commits', () => {
    expect(syncAction(repoStatus({ ahead: 2, behind: 1 }))).toBe('pull');
    expect(syncAction(repoStatus({ behind: 3 }))).toBe('pull');
  });

  it('pushes when only we are ahead', () => {
    expect(syncAction(repoStatus({ ahead: 2 }))).toBe('push');
  });

  it('fetches when the branch is level, unset, or not a repo at all', () => {
    expect(syncAction(repoStatus())).toBe('fetch');
    expect(syncAction(repoStatus({ ahead: 0, behind: 0 }))).toBe('fetch');
    expect(syncAction(null)).toBe('fetch');
    expect(syncAction({ is_repo: false })).toBe('fetch');
  });
});

describe('R2 — reading the status payload', () => {
  it('names the branch, or spells out a detached HEAD with a short sha', () => {
    expect(branchDisplay(repoStatus())).toEqual({ kind: 'branch', name: 'dev' });
    expect(branchDisplay(repoStatus({ branch: null, detached: true, head_sha: 'abcdef1234567' }))).toEqual({
      kind: 'detached',
      sha: 'abcdef1',
    });
    expect(branchDisplay(repoStatus({ branch: null }))).toEqual({ kind: 'none' });
    expect(branchDisplay(null)).toEqual({ kind: 'none' });
  });

  it('shortSha tolerates a missing sha', () => {
    expect(shortSha(null)).toBe('');
    expect(shortSha('abcdef1234', 4)).toBe('abcd');
  });

  it('reports the operation in progress, with merge taking precedence over rebase', () => {
    const flags = { merge: false, rebase: false, cherry_pick: false, revert: false };
    expect(mergeState(repoStatus({ in_progress: flags }))).toBe('none');
    expect(mergeState(repoStatus({ in_progress: { ...flags, merge: true, rebase: true } }))).toBe('merge');
    expect(mergeState(repoStatus({ in_progress: { ...flags, rebase: true } }))).toBe('rebase');
    expect(mergeState(repoStatus({ in_progress: { ...flags, cherry_pick: true } }))).toBe('cherry_pick');
    expect(mergeState(repoStatus({ in_progress: { ...flags, revert: true } }))).toBe('revert');
    expect(mergeState(null)).toBe('none');
  });

  it('counts uncommitted files from counts.total, and conflicts by path', () => {
    expect(uncommittedCount(repoStatus({ counts: { staged: 1, unstaged: 2, untracked: 0, conflicts: 0, total: 3 } }))).toBe(3);
    expect(uncommittedCount(repoStatus({ counts: undefined }))).toBe(0);
    expect(uncommittedCount(null)).toBe(0);
    expect(
      conflictPaths(repoStatus({ conflicts: [{ path: 'a.txt', status: 'UU' }, { path: 'b.txt', status: 'AA' }] })),
    ).toEqual(['a.txt', 'b.txt']);
    expect(conflictPaths(null)).toEqual([]);
  });
});

describe('R3 — grouping the branch list for the picker', () => {
  it('puts recently used first, then the rest with the current branch on top', () => {
    const list = branchList({
      local: [branch('main'), branch('dev', { current: true }), branch('feature/x')],
      remote: [branch('main', { remote: 'origin' })],
    });
    const groups = groupBranches(list, ['feature/x', 'main']);
    expect(groups.recent.map((b) => b.name)).toEqual(['feature/x', 'main']);
    // `dev` is current, so it stays out of `recent` and lands in the local rest.
    expect(groups.local.map((b) => b.name)).toEqual(['dev']);
    expect(groups.remote.map((b) => b.name)).toEqual(['main']);
  });

  it('never repeats the current branch in the recent group', () => {
    const list = branchList({ local: [branch('dev', { current: true }), branch('main')] });
    const groups = groupBranches(list, ['dev', 'main']);
    expect(groups.recent.map((b) => b.name)).toEqual(['main']);
  });

  it('drops a remembered branch that no longer exists, and dedups the list', () => {
    const list = branchList({ local: [branch('main')] });
    const groups = groupBranches(list, ['gone', 'main', 'main']);
    expect(groups.recent.map((b) => b.name)).toEqual(['main']);
    expect(groups.local).toEqual([]);
  });

  it('answers empty groups for a missing list', () => {
    expect(groupBranches(null, ['main'])).toEqual({ recent: [], local: [], remote: [] });
  });

  it('pushRecentBranch moves a name to the front, dedups, and caps the list', () => {
    expect(pushRecentBranch(['a', 'b'], 'b')).toEqual(['b', 'a']);
    expect(pushRecentBranch(['a', 'b', 'c', 'd', 'e'], 'f')).toEqual(['f', 'a', 'b', 'c', 'd']);
    expect(pushRecentBranch(['a'], 'b', 1)).toEqual(['b']);
    expect(pushRecentBranch(['a'], '')).toEqual(['a']);
  });
});

describe('R4 — error codes always have a translation', () => {
  it('maps known codes to their own key and everything else to the generic one', () => {
    expect(gitErrorKey('dirty_worktree')).toBe('git.error.dirty_worktree');
    expect(gitErrorKey('non_fast_forward')).toBe('git.error.non_fast_forward');
    expect(gitErrorKey('something_new')).toBe('git.error.generic');
    expect(gitErrorKey(undefined)).toBe('git.error.generic');
    expect(gitErrorKey(null)).toBe('git.error.generic');
  });

  it('every produced key exists in both locales', () => {
    const zh = read('locales/zh.json');
    const en = read('locales/en.json');
    const lookup = (dict: any, key: string) =>
      key.split('.').reduce((node: any, part) => (node == null ? node : node[part]), dict);
    const codes = [
      'not_a_repo',
      'dirty_worktree',
      'locked',
      'bad_ref',
      'current_branch',
      'not_merged',
      'already_pushed',
      'no_parent',
      'no_head',
      'no_remote',
      'no_upstream',
      'non_fast_forward',
      'stale_lease',
      'auth_failed',
      'network',
      'detached_head',
      'empty_message',
      'nothing_staged',
      'no_paths',
      'bad_path',
      'conflicts',
      'sync_failed',
      'unknown_code',
    ];
    for (const code of codes) {
      const key = gitErrorKey(code);
      expect(lookup(zh, key), `zh.json is missing ${key}`).toBeTruthy();
      expect(typeof lookup(en, key), `en.json is missing ${key}`).toBe('string');
    }
  });
});

describe('R5 — the sentence a settled sync earns', () => {
  it('names the operation that finished', () => {
    expect(syncNoticeKey(task({ op: 'fetch' }))).toBe('git.notice.fetched');
    expect(syncNoticeKey(task({ op: 'push' }))).toBe('git.notice.pushed');
  });

  it('distinguishes the three ways a pull can land', () => {
    expect(syncNoticeKey(task({ op: 'pull', mode: 'up_to_date' }))).toBe('git.notice.upToDate');
    expect(syncNoticeKey(task({ op: 'pull', mode: 'merge' }))).toBe('git.notice.pulledMerge');
    expect(syncNoticeKey(task({ op: 'pull', mode: 'fast_forward' }))).toBe('git.notice.pulledFastForward');
  });

  it('turns the two failures that need a next step into a notice, and leaves the rest to the error line', () => {
    expect(syncNoticeKey(task({ ok: false, code: 'conflicts' }))).toBe('git.notice.conflicts');
    expect(syncNoticeKey(task({ ok: false, op: 'push', code: 'non_fast_forward' }))).toBe('git.notice.pushRejected');
    expect(syncNoticeKey(task({ ok: false, code: 'auth_failed' }))).toBeNull();
    expect(syncNoticeKey(task({ ok: false, code: 'network' }))).toBeNull();
    expect(syncNoticeKey(null)).toBeNull();
  });
});

describe('R6 — which mode the cwd points at', () => {
  it('detects the mode-switch worktree on either slash style', () => {
    expect(worktreeModeOf('C:/repo/.os-worktrees/ws-abc123')).toBe('worktree');
    expect(worktreeModeOf('C:\\repo\\.os-worktrees\\ws-abc123')).toBe('worktree');
    expect(worktreeModeOf('C:/repo/.os-worktrees')).toBe('local');
    expect(worktreeModeOf('C:/repo')).toBe('local');
    expect(worktreeModeOf('')).toBe('local');
    expect(worktreeModeOf(null)).toBe('local');
    expect(worktreeModeOf(undefined)).toBe('local');
  });
});
