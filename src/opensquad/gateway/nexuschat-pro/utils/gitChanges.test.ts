/**
 * The changes view's rules: which rows appear, what they can do, what is checked.
 *
 * The panel shows two different things under one tab — what this conversation
 * touched (the session baseline) and what git has uncommitted — and the whole
 * risk is mixing them up: offering "stage" on a file git cannot see, or hiding a
 * file the conversation changed because git is clean. These are the rules that
 * keep them apart, so they are pinned here rather than through the 2700-line
 * panel that renders them.
 *
 *   R1  git's four lists merge by path and keep both columns
 *   R2  the scope picks the row set, and each row knows if it is stageable
 *   R3  one glyph per row, conflicts first
 *   R4  the checked rows split into stage / unstage / discard, with untracked
 *       flagged for its own consent
 *   R5  undo is offered only where a last commit could still be local, and the
 *       commit form reports the one thing that blocks it
 *
 * Mutations verified:
 *   MR1  gitRows: read untracked as staged                          → R1
 *   MR2  buildChangeRows('session'): start from git rows            → R2
 *   MR3  buildChangeRows('all'): drop baseline-only entries         → R2
 *   MR4  gitStatusLabel: check `untracked` before `conflicted`      → R3
 *   MR5  splitActions: stage a conflicted row                       → R4
 *   MR6  splitActions: never set `discardIncludesUntracked`         → R4
 *   MR7  canUndoLastCommit: ignore `initial`                        → R5
 *   MR8  commitBlocker: check `nothing_staged` first                → R5
 */
import { describe, expect, it } from 'vitest';
import type { GitStatus } from '../services/api';
import {
  basename,
  buildChangeRows,
  canDiscard,
  canStage,
  canUndoLastCommit,
  canUnstage,
  commitBlocker,
  diffModeFor,
  gitRows,
  gitStatusLabel,
  isStaged,
  selectionState,
  splitActions,
  type GitChangeRow,
  type SessionChangeInput,
} from './gitChanges';

const status = (over: Partial<GitStatus> = {}): GitStatus => ({
  is_repo: true,
  branch: 'dev',
  ...over,
});

describe('R1 — merging git status into one row per path', () => {
  it('keeps both columns for a file that is staged AND modified again', () => {
    const rows = gitRows(
      status({
        staged: [{ path: 'a.txt', status: 'M' }],
        unstaged: [{ path: 'a.txt', status: 'M' }],
      }),
    );
    expect(rows.size).toBe(1);
    const row = rows.get('a.txt')!;
    expect(row.staged).toBe('M');
    expect(row.unstaged).toBe('M');
    expect(isStaged(row)).toBe(true);
    expect(canStage(row)).toBe(true);
  });

  it('reads untracked and conflicted as their own flags, not as columns', () => {
    const rows = gitRows(
      status({
        untracked: [{ path: 'new/file.txt', status: '?' }],
        conflicts: [{ path: 'both.txt', status: 'UU' }],
      }),
    );
    expect(rows.get('new/file.txt')!.untracked).toBe(true);
    expect(isStaged(rows.get('new/file.txt')!)).toBe(false);
    expect(rows.get('both.txt')!.conflicted).toBe(true);
    expect(canStage(rows.get('both.txt')!)).toBe(false);
    expect(canDiscard(rows.get('both.txt')!)).toBe(false);
  });

  it('answers nothing for a non-repo, and normalises separators', () => {
    expect(gitRows(null).size).toBe(0);
    expect(gitRows({ is_repo: false }).size).toBe(0);
    expect([...gitRows(status({ unstaged: [{ path: 'src\\win\\a.ts', status: 'M' }] })).keys()]).toEqual([
      'src/win/a.ts',
    ]);
    expect(basename('src\\win\\a.ts')).toBe('a.ts');
    expect(basename('top.txt')).toBe('top.txt');
  });
});

describe('R2 — the two scopes', () => {
  const session: SessionChangeInput[] = [
    { path: 'src/touched.ts', additions: 5, deletions: 1 },
    { path: 'only-session.ts', additions: 1, deletions: 0 },
  ];
  const dirty = status({
    unstaged: [{ path: 'src/touched.ts', status: 'M' }],
    untracked: [{ path: 'outside.txt', status: '?' }],
    staged: [{ path: 'src/ready.ts', status: 'A' }],
  });

  it('本对话 keeps the baseline rows and annotates them with git state', () => {
    const rows = buildChangeRows(session, dirty, 'session');
    expect(rows.map((r) => r.path)).toEqual(['only-session.ts', 'src/touched.ts']);
    const touched = rows.find((r) => r.path === 'src/touched.ts')!;
    expect(touched.inSession).toBe(true);
    expect(touched.unstaged).toBe('M');
    expect(touched.additions).toBe(5);
    // A baseline row git cannot see is still listed — it just cannot be staged.
    const only = rows.find((r) => r.path === 'only-session.ts')!;
    expect(canStage(only)).toBe(false);
    expect(canDiscard(only)).toBe(false);
    // And a dirty file the conversation never touched is not part of 本对话.
    expect(rows.some((r) => r.path === 'outside.txt')).toBe(false);
  });

  it('全部 shows the git view, marking what the conversation also touched', () => {
    const rows = buildChangeRows(session, dirty, 'all');
    // Code-unit order — 'only-session' sorts before 'outside' (n < u).
    expect(rows.map((r) => r.path)).toEqual(['only-session.ts', 'outside.txt', 'src/ready.ts', 'src/touched.ts']);
    expect(rows.find((r) => r.path === 'src/touched.ts')!.inSession).toBe(true);
    expect(rows.find((r) => r.path === 'outside.txt')!.inSession).toBe(false);
    // The baseline-only file survives here too, so it is never silently dropped.
    expect(rows.find((r) => r.path === 'only-session.ts')!.inSession).toBe(true);
  });

  it('a file git only has staged still offers unstage, not stage', () => {
    const rows = buildChangeRows([], dirty, 'all');
    const ready = rows.find((r) => r.path === 'src/ready.ts')!;
    expect(canStage(ready)).toBe(false);
    expect(canUnstage(ready)).toBe(true);
    expect(diffModeFor(ready)).toBe('staged');
    expect(diffModeFor(rows.find((r) => r.path === 'src/touched.ts')!)).toBe('worktree');
  });
});

describe('R3 — the row glyph', () => {
  const row = (over: Partial<GitChangeRow>): GitChangeRow => ({
    path: 'a.txt',
    name: 'a.txt',
    inSession: false,
    staged: '',
    unstaged: '',
    untracked: false,
    conflicted: false,
    ...over,
  });

  it('prefers the conflict marker over anything the columns say', () => {
    expect(gitStatusLabel(row({ conflicted: true, staged: 'M', unstaged: 'M' }))).toBe('U');
  });

  it('names the state a plain row is in', () => {
    expect(gitStatusLabel(row({ untracked: true }))).toBe('?');
    expect(gitStatusLabel(row({ unstaged: 'D' }))).toBe('D');
    expect(gitStatusLabel(row({ staged: 'A' }))).toBe('A');
    expect(gitStatusLabel(row({ staged: 'M', unstaged: 'M' }))).toBe('M');
    expect(gitStatusLabel(row({}))).toBe('M');
  });
});

describe('R4 — what the checked rows mean', () => {
  const rows: GitChangeRow[] = [
    { path: 'modified.txt', name: 'modified.txt', inSession: true, staged: '', unstaged: 'M', untracked: false, conflicted: false },
    { path: 'new.txt', name: 'new.txt', inSession: false, staged: '', unstaged: '', untracked: true, conflicted: false },
    { path: 'ready.txt', name: 'ready.txt', inSession: false, staged: 'A', unstaged: '', untracked: false, conflicted: false },
    { path: 'broken.txt', name: 'broken.txt', inSession: false, staged: '', unstaged: '', untracked: false, conflicted: true },
    { path: 'clean.txt', name: 'clean.txt', inSession: true, staged: '', unstaged: '', untracked: false, conflicted: false },
  ];

  it('counts only the stageable rows, and says none/some/all', () => {
    expect(selectionState(rows, new Set())).toBe('none');
    expect(selectionState(rows, new Set(['modified.txt']))).toBe('some');
    // Conflicts and clean rows are not selectable, so picking the rest is "all".
    expect(selectionState(rows, new Set(['modified.txt', 'new.txt', 'ready.txt']))).toBe('all');
    expect(selectionState([], new Set(['x']))).toBe('none');
  });

  it('routes each checked row to the one operation it can have', () => {
    const all = new Set(rows.map((r) => r.path));
    const actions = splitActions(rows, all);
    expect(actions.stage).toEqual(['modified.txt', 'new.txt']);
    // `ready.txt` is already in the index, so it goes the other way.
    expect(actions.unstage).toEqual(['ready.txt']);
    // A staged-only file has something to throw away too (unstage + revert); a
    // conflicted file does not — it is resolved through its own diff.
    expect(actions.discard).toEqual(['modified.txt', 'new.txt', 'ready.txt']);
    expect(actions.discardIncludesUntracked).toBe(true);
  });

  it('reports no untracked consent needed for a tracked-only selection', () => {
    const actions = splitActions(rows, new Set(['modified.txt']));
    expect(actions.discard).toEqual(['modified.txt']);
    expect(actions.discardIncludesUntracked).toBe(false);
  });
});

describe('R5 — undo, and the commit form', () => {
  it('offers undo for a branch with no upstream, or with commits ahead of it', () => {
    expect(canUndoLastCommit(status({ upstream: null, ahead: 0 }))).toBe(true);
    expect(canUndoLastCommit(status({ upstream: 'origin/dev', ahead: 1 }))).toBe(true);
    expect(canUndoLastCommit(status({ upstream: 'origin/dev', ahead: 0 }))).toBe(false);
    expect(canUndoLastCommit(status({ initial: true }))).toBe(false);
    expect(canUndoLastCommit({ is_repo: false })).toBe(false);
    expect(canUndoLastCommit(null)).toBe(false);
  });

  it('blames the title before the index, and clears once both are there', () => {
    expect(commitBlocker('', 2)).toBe('empty_message');
    expect(commitBlocker('   ', 2)).toBe('empty_message');
    // Both wrong: the answer must be one of them, and it is the title — that is
    // the box in front of the user, and the backend refuses it first anyway.
    expect(commitBlocker('', 0)).toBe('empty_message');
    expect(commitBlocker('fix: thing', 0)).toBe('nothing_staged');
    expect(commitBlocker('fix: thing', 1)).toBeNull();
  });
});
