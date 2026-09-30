/**
 * The changes view's decisions, without React.
 *
 * Two different questions live in the file panel and they must not be conflated:
 * *what did this conversation touch* (the session baseline, which the agent's
 * edits populate) and *what is uncommitted in git* (everything, including work
 * from before the conversation and from other sessions). The 本对话 / 全部 chips
 * switch between them, and this module is what makes that switch a pure function
 * of two lists instead of a tangle of conditionals in the row renderer.
 *
 * `本对话` keeps its existing meaning: the session baseline, unchanged. A file
 * the conversation touched that git does not see as dirty (reverted, ignored)
 * still appears — it just cannot be staged, because there is nothing to stage.
 * `全部` is the git view, which can also contain files the conversation never
 * touched.
 *
 *   R1  rows are merged by path; a file both staged and modified keeps both columns
 *   R2  the scope decides the row set, and each row says whether it is stageable
 *   R3  the label is one glyph, and a conflicted row outranks its columns
 *   R4  the selection is split into the three operations the panel offers
 *   R5  undo is only offered where the last commit could still be unpushed
 */
import type { GitFileEntry, GitStatus } from '../services/api';

export type ChangeScope = 'session' | 'all';

export interface GitChangeRow {
  /** Repo-relative path with forward slashes. */
  path: string;
  name: string;
  /** The session baseline has this file — the conversation touched it. */
  inSession: boolean;
  /** Index column from porcelain v2 ('' when the file is not staged). */
  staged: string;
  /** Worktree column ('' when there is no unstaged change). */
  unstaged: string;
  untracked: boolean;
  conflicted: boolean;
  additions?: number;
  deletions?: number;
  oversized?: boolean;
  /** Deleted from disk (and, if staged, from the index). */
  missing?: boolean;
}

/** A row of the session baseline, as the panel already holds it. */
export interface SessionChangeInput {
  name?: string;
  path: string;
  status?: string;
  additions?: number;
  deletions?: number;
  oversized?: boolean;
  missing?: boolean;
}

const _norm = (p: string) => (p || '').replace(/\\/g, '/').replace(/^\.\//, '');

/** Code-unit order, not `localeCompare`: the row order must not move with the UI language. */
const _byPath = (a: GitChangeRow, b: GitChangeRow): number =>
  a.path < b.path ? -1 : a.path > b.path ? 1 : 0;

export const basename = (path: string): string => {
  const p = _norm(path);
  const at = p.lastIndexOf('/');
  return at === -1 ? p : p.slice(at + 1);
};

/** Merge the four porcelain lists into one entry per path, keeping both columns. */
export function gitRows(status: GitStatus | null): Map<string, GitChangeRow> {
  const rows = new Map<string, GitChangeRow>();
  if (!status?.is_repo) return rows;

  const at = (entry: GitFileEntry): GitChangeRow => {
    const path = _norm(entry.path);
    let row = rows.get(path);
    if (!row) {
      row = {
        path,
        name: basename(path),
        inSession: false,
        staged: '',
        unstaged: '',
        untracked: false,
        conflicted: false,
      };
      rows.set(path, row);
    }
    return row;
  };

  for (const entry of status.staged || []) at(entry).staged = entry.status || 'M';
  for (const entry of status.unstaged || []) at(entry).unstaged = entry.status || 'M';
  for (const entry of status.untracked || []) {
    const row = at(entry);
    row.untracked = true;
  }
  for (const entry of status.conflicts || []) {
    const row = at(entry);
    row.conflicted = true;
  }
  return rows;
}

/**
 * The rows to show for one scope.
 *
 * `session` starts from the baseline (so today's list keeps its meaning) and
 * annotates each row with git state; `all` is the git view plus any baseline row
 * git cannot see, so a file that only the conversation knows about is never
 * silently dropped from the panel.
 */
export function buildChangeRows(
  sessionRows: SessionChangeInput[],
  status: GitStatus | null,
  scope: ChangeScope,
): GitChangeRow[] {
  const git = gitRows(status);
  const rows: GitChangeRow[] = [];
  const seen = new Set<string>();

  if (scope === 'session') {
    for (const entry of sessionRows) {
      const path = _norm(entry.path);
      if (!path || seen.has(path)) continue;
      seen.add(path);
      const fromGit = git.get(path);
      rows.push({
        path,
        name: entry.name || basename(path),
        inSession: true,
        staged: fromGit?.staged || '',
        unstaged: fromGit?.unstaged || '',
        untracked: fromGit?.untracked || false,
        conflicted: fromGit?.conflicted || false,
        additions: entry.additions,
        deletions: entry.deletions,
        oversized: entry.oversized,
        missing: entry.missing || fromGit?.missing,
      });
    }
    // A dirty file the conversation did not touch is not part of 本对话, but a
    // baseline entry git does not see is — that is the case above.
    return rows.sort(_byPath);
  }

  for (const row of git.values()) {
    const sessionEntry = sessionRows.find((e) => _norm(e.path) === row.path);
    rows.push({
      ...row,
      inSession: !!sessionEntry,
      additions: sessionEntry?.additions,
      deletions: sessionEntry?.deletions,
      oversized: sessionEntry?.oversized ?? row.oversized,
      missing: sessionEntry?.missing ?? row.missing,
    });
    seen.add(row.path);
  }
  for (const entry of sessionRows) {
    const path = _norm(entry.path);
    if (!path || seen.has(path)) continue;
    seen.add(path);
    rows.push({
      path,
      name: entry.name || basename(path),
      inSession: true,
      staged: '',
      unstaged: '',
      untracked: false,
      conflicted: false,
      additions: entry.additions,
      deletions: entry.deletions,
      oversized: entry.oversized,
      missing: entry.missing,
    });
  }
  return rows.sort(_byPath);
}

/** One glyph for the row: the state that matters most, most severe first. */
export function gitStatusLabel(row: GitChangeRow): string {
  if (row.conflicted) return 'U';
  if (row.untracked) return '?';
  if (row.staged && row.unstaged) return 'M';
  return row.staged || row.unstaged || 'M';
}

/** True when this row is in the index — that is what a commit would include. */
export const isStaged = (row: GitChangeRow): boolean =>
  !!row.staged && row.staged !== '?' && !row.untracked && !row.conflicted;

/** Nothing to stage: the file has no git change at all (or is conflicted). */
export const canStage = (row: GitChangeRow): boolean =>
  !row.conflicted && (!!row.unstaged || row.untracked);

/** Nothing to unstage: not in the index. */
export const canUnstage = (row: GitChangeRow): boolean => isStaged(row) || !!row.staged;

/**
 * Only a row with a real git change can be discarded — a baseline-only entry
 * (the conversation touched it, git does not see it) has nothing to throw away.
 * An untracked file is discardable too, but deleting it needs its own consent,
 * which the caller asks for by name (`confirm_untracked`).
 */
export const canDiscard = (row: GitChangeRow): boolean =>
  !row.conflicted && (!!row.staged || !!row.unstaged || row.untracked);

/** Which diff a row's viewer should ask for. */
export const diffModeFor = (row: GitChangeRow): 'worktree' | 'staged' =>
  isStaged(row) && !row.unstaged && !row.untracked ? 'staged' : 'worktree';

export type SelectionState = 'none' | 'some' | 'all';

export function selectionState(rows: GitChangeRow[], selected: ReadonlySet<string>): SelectionState {
  const eligible = rows.filter(canStage);
  if (eligible.length === 0) return 'none';
  const picked = eligible.filter((row) => selected.has(row.path)).length;
  if (picked === 0) return 'none';
  return picked === eligible.length ? 'all' : 'some';
}

export interface ChangeActions {
  /** Paths to `git add`. */
  stage: string[];
  /** Paths to take back out of the index. */
  unstage: string[];
  /** Paths to discard; the untracked ones also need `confirm_untracked`. */
  discard: string[];
  /** The discard selection contains a file git does not track yet. */
  discardIncludesUntracked: boolean;
}

/** Split the checked rows into the operations, each of which has its own endpoint. */
export function splitActions(rows: GitChangeRow[], selected: ReadonlySet<string>): ChangeActions {
  const actions: ChangeActions = {
    stage: [],
    unstage: [],
    discard: [],
    discardIncludesUntracked: false,
  };
  for (const row of rows) {
    if (!selected.has(row.path)) continue;
    if (canStage(row)) actions.stage.push(row.path);
    else if (canUnstage(row)) actions.unstage.push(row.path);
    if (canDiscard(row)) {
      actions.discard.push(row.path);
      if (row.untracked) actions.discardIncludesUntracked = true;
    }
  }
  return actions;
}

/**
 * Whether "undo the last commit" may be offered.
 *
 * The backend refuses with `already_pushed` / `no_parent`, but showing a button
 * whose only outcome is a refusal wastes the click. A branch with no upstream has
 * every commit local, so the last one is undoable; with an upstream, only commits
 * ahead of it qualify — and no commits ahead means the last one is already out.
 */
export function canUndoLastCommit(status: GitStatus | null): boolean {
  if (!status?.is_repo || status.initial) return false;
  if (!status.upstream) return true;
  return (status.ahead || 0) > 0;
}

/** The commit form is only useful with a title and something in the index. */
export function commitBlocker(title: string, stagedCount: number): 'empty_message' | 'nothing_staged' | null {
  if (!title.trim()) return 'empty_message';
  if (stagedCount <= 0) return 'nothing_staged';
  return null;
}
