/**
 * Pure derivations for the repo status bar and the branch picker.
 *
 * Deliberately free of React and of any Chinese/English string: the wording
 * rules — which action the sync control becomes, what a detached HEAD is
 * called, how branches are grouped — are unit-testable here without rendering,
 * and the labels stay in `locales/*.json` where the i18n guards can see them.
 */
import type { GitBranch, GitBranchList, GitStatus, GitSyncTask } from '../services/api';

/** What the sync button does for the current state. */
export type SyncAction = 'push' | 'pull' | 'fetch';

/** The multi-step operation a repository can be in the middle of. */
export type MergeState = 'none' | 'merge' | 'rebase' | 'cherry_pick' | 'revert';

export type BranchDisplay =
  | { kind: 'none' }
  | { kind: 'branch'; name: string }
  | { kind: 'detached'; sha: string };

/**
 * The one sync action worth offering, in the order that keeps the user out of
 * trouble: pull before push while the remote has commits we lack (pushing then
 * would only be rejected), push when we are the one ahead, fetch otherwise —
 * including for a clean tree, where fetching is what tells you whether
 * anything changed at all.
 */
export function syncAction(status: GitStatus | null): SyncAction {
  if (!status?.is_repo) return 'fetch';
  if ((status.behind || 0) > 0) return 'pull';
  if ((status.ahead || 0) > 0) return 'push';
  return 'fetch';
}

export function shortSha(sha: string | null | undefined, length = 7): string {
  return (sha || '').slice(0, length);
}

/** Branch name, a detached HEAD, or nothing to show. */
export function branchDisplay(status: GitStatus | null): BranchDisplay {
  if (!status?.is_repo) return { kind: 'none' };
  if (status.branch) return { kind: 'branch', name: status.branch };
  if (status.detached) return { kind: 'detached', sha: shortSha(status.head_sha) };
  return { kind: 'none' };
}

/** Which operation the repository is stuck in (conflicts + how to get out). */
export function mergeState(status: GitStatus | null): MergeState {
  const inProgress = status?.in_progress;
  if (!inProgress) return 'none';
  if (inProgress.merge) return 'merge';
  if (inProgress.rebase) return 'rebase';
  if (inProgress.cherry_pick) return 'cherry_pick';
  if (inProgress.revert) return 'revert';
  return 'none';
}

/** Files that differ from HEAD (the number behind "n uncommitted files"). */
export function uncommittedCount(status: GitStatus | null): number {
  return status?.counts?.total ?? 0;
}

/** The mode the composer runs in, derived from where the cwd points. */
export type GitWorktreeMode = 'local' | 'worktree';

/** A cwd inside `<repo>/.os-worktrees/` is the mode-switch worktree. */
export function worktreeModeOf(cwd: string | null | undefined): GitWorktreeMode {
  const norm = (cwd || '').replace(/\\/g, '/');
  return norm.includes('/.os-worktrees/') ? 'worktree' : 'local';
}

/** Conflict paths, as the panel and the merge banner both need them. */
export function conflictPaths(status: GitStatus | null): string[] {
  return (status?.conflicts || []).map((entry) => entry.path);
}

export interface BranchGroups {
  /** Recently checked out branches, still existing, current excluded. */
  recent: GitBranch[];
  /** Everything else local, current first. */
  local: GitBranch[];
  remote: GitBranch[];
}

/**
 * Split the branch list for the picker: recently used first (the caller owns the
 * order, usually localStorage), then the rest of the local branches, then the
 * remotes. The current branch never appears in `recent` — it is already pinned
 * above them, and showing it twice invites a pointless click.
 */
export function groupBranches(list: GitBranchList | null, recentNames: string[] = []): BranchGroups {
  if (!list) return { recent: [], local: [], remote: [] };
  const local = list.local || [];
  const byName = new Map(local.map((branch) => [branch.name, branch]));
  const recent: GitBranch[] = [];
  const taken = new Set<string>();
  for (const name of recentNames) {
    const branch = byName.get(name);
    if (!branch || branch.current || taken.has(name)) continue;
    recent.push(branch);
    taken.add(name);
  }
  const rest = local.filter((branch) => !taken.has(branch.name));
  rest.sort((a, b) => Number(!!b.current) - Number(!!a.current));
  return { recent, local: rest, remote: list.remote || [] };
}

/** Move `name` to the front of a most-recently-used list (dedup + cap). */
export function pushRecentBranch(recentNames: string[], name: string, cap = 5): string[] {
  if (!name) return recentNames;
  return [name, ...recentNames.filter((entry) => entry !== name)].slice(0, cap);
}

/**
 * Codes the backend can answer with, each of which has its own wording in
 * `locales/*.json`. Anything outside this set falls back to `git.error.generic`
 * rather than interpolating the raw code into the UI — a code with no
 * translation would otherwise surface as `git.error.foo_bar`.
 */
const GIT_ERROR_CODES: ReadonlySet<string> = new Set([
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
]);

/** Locale key for a refused git command; unknown codes get the generic text. */
export function gitErrorKey(code?: string | null): string {
  return code && GIT_ERROR_CODES.has(code) ? `git.error.${code}` : 'git.error.generic';
}

/**
 * The notice a settled sync task deserves, or `null` when the task failed with
 * a code that needs the error wording instead (credentials, network, rejection).
 *
 * Separated from the caller so the mapping from a task to a sentence is a pure
 * function of the task — the caller only decides where to draw it.
 */
export function syncNoticeKey(task: GitSyncTask | null): string | null {
  if (!task) return null;
  if (!task.ok) {
    if (task.code === 'conflicts') return 'git.notice.conflicts';
    if (task.code === 'non_fast_forward') return 'git.notice.pushRejected';
    return null;
  }
  if (task.op === 'fetch') return 'git.notice.fetched';
  if (task.op === 'push') return 'git.notice.pushed';
  if (task.mode === 'up_to_date') return 'git.notice.upToDate';
  if (task.mode === 'merge') return 'git.notice.pulledMerge';
  return 'git.notice.pulledFastForward';
}
