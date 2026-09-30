/**
 * GitRepoBar — the branch chip's data owner, wired to the agent's git routes.
 *
 * `RepoStatusBar` is the chip markup and `BranchPicker` is the menu; this is the
 * one place that owns the data. It exists as a separate component so the page's
 * diff stays a single mount: the composer renders it in the footer next to the
 * folder, and every fetch, poll, confirmation and checkout lives here.
 *
 * Two behaviours are deliberate:
 *
 * * **The user's answer to a refusal is not an error path.** A dirty worktree
 *   before a switch and an unmerged branch before a delete come back as a code,
 *   and each one opens the confirmation that unblocks it; anything else becomes
 *   the chip's floating notice.
 * * **Polling is scoped to the current agent + folder.** A response that arrives
 *   after the user switched folders or sessions is dropped, not painted.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { gitAPI, type GitBranchList, type GitResult, type GitStatus } from '../../services/api';
import { gitErrorKey, pushRecentBranch, worktreeModeOf } from '../../utils/gitRepoState';
import { GitConfirmModal, type GitConfirmSpec } from './GitConfirmModal';
import { RepoStatusBar, type RepoNotice } from './RepoStatusBar';

const RECENT_BRANCHES_KEY = 'opensquad.git.recentBranches';
/** Status is re-read on this cadence, plus on window focus / tab visibility. */
const STATUS_POLL_MS = 10_000;

function readRecentBranches(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_BRANCHES_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((name): name is string => typeof name === 'string') : [];
  } catch {
    return [];
  }
}

export interface GitRepoBarProps {
  agentId: string;
  /** Folder the repository is read from — the same value the file panel uses. */
  cwd?: string;
  /** False once the session has messages: the mode chip turns read-only. */
  modeEditable?: boolean;
  /**
   * Local/Worktree switch. The page owns the cwd (agent state + working
   * directory), so this callback does the prepare + rebind; a returned
   * `{ok:false}` becomes the chip's floating notice.
   */
  onModeChange?: (next: 'local' | 'worktree') => Promise<{ ok: boolean; error?: string } | void>;
}

export const GitRepoBar: React.FC<GitRepoBarProps> = ({ agentId, cwd, modeEditable = false, onModeChange }) => {
  const { t } = useTranslation();
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [branches, setBranches] = useState<GitBranchList | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [branchesLoading, setBranchesLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<RepoNotice | null>(null);
  const [recent, setRecent] = useState<string[]>(readRecentBranches);
  const [confirmStack, setConfirmStack] = useState<GitConfirmSpec[]>([]);

  /** Bumped whenever agent/folder changes: late responses compare against it. */
  const genRef = useRef(0);
  const noticeTimerRef = useRef<number | null>(null);

  const rootArg = useMemo(() => cwd || undefined, [cwd]);

  const pushNotice = useCallback((tone: RepoNotice['tone'], text: string) => {
    setNotice({ tone, text });
    if (noticeTimerRef.current !== null) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => {
      noticeTimerRef.current = null;
      setNotice(null);
    }, 8000);
  }, []);

  useEffect(
    () => () => {
      if (noticeTimerRef.current !== null) window.clearTimeout(noticeTimerRef.current);
    },
    [],
  );

  const failText = useCallback(
    (res: { code?: string; error?: string }): string => {
      const key = gitErrorKey(res.code);
      // The translated line is what the user acts on; the raw git wording is
      // only worth showing when there is no translation to stand in for it.
      return key === 'git.error.generic' && res.error ? `${t(key)}: ${res.error}` : t(key);
    },
    [t],
  );

  const pushConfirm = useCallback((spec: GitConfirmSpec) => {
    setConfirmStack((stack) => [...stack, spec]);
  }, []);

  const dismissConfirm = useCallback(() => {
    setConfirmStack((stack) => stack.slice(0, -1));
  }, []);

  // ------------------------------------------------------------------ reads

  const loadStatus = useCallback(async () => {
    const gen = genRef.current;
    try {
      const res = await gitAPI.status(agentId, rootArg);
      if (gen === genRef.current) setStatus(res);
    } catch {
      // Keep the previous status: a failed poll is not news, and blanking the
      // bar would make a transient gateway hiccup look like "not a repo".
    } finally {
      if (gen === genRef.current) setStatusLoading(false);
    }
  }, [agentId, rootArg]);

  const loadBranches = useCallback(async () => {
    const gen = genRef.current;
    setBranchesLoading(true);
    try {
      const res = await gitAPI.branches(agentId, rootArg);
      if (gen === genRef.current) setBranches(res);
    } catch {
      /* the picker keeps the list it has */
    } finally {
      if (gen === genRef.current) setBranchesLoading(false);
    }
  }, [agentId, rootArg]);

  const refreshAll = useCallback(async () => {
    const gen = genRef.current;
    const [st, br] = await Promise.all([
      gitAPI.status(agentId, rootArg).catch(() => null),
      gitAPI.branches(agentId, rootArg).catch(() => null),
    ]);
    if (gen !== genRef.current) return;
    if (st) setStatus(st);
    if (br) setBranches(br);
  }, [agentId, rootArg]);

  useEffect(() => {
    genRef.current += 1;
    setStatus(null);
    setBranches(null);
    setStatusLoading(true);
    setNotice(null);
    void loadStatus();

    const timer = window.setInterval(() => void loadStatus(), STATUS_POLL_MS);
    const onFocus = () => void loadStatus();
    const onVisibility = () => {
      if (document.visibilityState === 'visible') void loadStatus();
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      // Invalidate anything still in flight for the old agent/folder.
      genRef.current += 1;
      window.clearInterval(timer);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [loadStatus]);

  const rememberBranch = useCallback((name: string) => {
    setRecent((prev) => {
      const next = pushRecentBranch(prev, name);
      try {
        localStorage.setItem(RECENT_BRANCHES_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  // -------------------------------------------------------------- commands

  /** Run one command, funnelling a transport failure into the notice line. */
  const callGit = useCallback(
    async (fn: () => Promise<GitResult>): Promise<GitResult | null> => {
      setBusy(true);
      try {
        return await fn();
      } catch (e) {
        pushNotice('error', (e as Error)?.message || t('git.error.generic'));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [pushNotice, t],
  );

  const doCheckout = useCallback(
    async (
      branch: string,
      opts?: { create?: boolean; base?: string; stashDirty?: boolean },
    ): Promise<void> => {
      const res = await callGit(() =>
        gitAPI.checkout(agentId, {
          branch,
          root: rootArg,
          create: opts?.create,
          base: opts?.base,
          stash_dirty: opts?.stashDirty,
        }),
      );
      if (!res) return;
      if (!res.ok) {
        if (res.code === 'dirty_worktree') {
          const n = res.counts?.total ?? 0;
          pushConfirm({
            testId: 'stash-switch',
            title: t('git.confirm.stashTitle'),
            body: t('git.confirm.stashBody', { n }),
            hint: (res.paths || []).slice(0, 8).join('\n') || undefined,
            confirmLabel: t('git.confirm.stashConfirm'),
            onConfirm: () => void doCheckout(branch, { ...opts, stashDirty: true }),
          });
          return;
        }
        pushNotice('error', failText(res));
        return;
      }
      rememberBranch(branch);
      if (res.stashed) pushNotice('info', t('git.notice.switchedStashed', { branch }));
      else if (opts?.create) pushNotice('info', t('git.notice.created', { branch }));
      else pushNotice('info', t('git.notice.switched', { branch }));
      await refreshAll();
    },
    [agentId, callGit, failText, pushConfirm, pushNotice, refreshAll, rememberBranch, rootArg, t],
  );

  const doDelete = useCallback(
    async (branch: string, force = false) => {
      const res = await callGit(() => gitAPI.deleteBranch(agentId, branch, rootArg, force));
      if (!res) return;
      if (!res.ok) {
        if (res.code === 'not_merged') {
          pushConfirm({
            testId: 'delete-force',
            tone: 'danger',
            title: t('git.confirm.deleteTitle'),
            body: t('git.confirm.deleteBody', { branch }),
            hint: res.error || undefined,
            confirmLabel: t('git.confirm.deleteForceConfirm'),
            onConfirm: () => void doDelete(branch, true),
          });
          return;
        }
        pushNotice('error', failText(res));
        return;
      }
      pushNotice('info', t('git.notice.deleted', { branch }));
      await refreshAll();
    },
    [agentId, callGit, failText, pushConfirm, pushNotice, refreshAll, rootArg, t],
  );

  const confirm = confirmStack.length > 0 ? confirmStack[confirmStack.length - 1] : null;

  const handleModeChange = useCallback(
    async (next: 'local' | 'worktree') => {
      if (!onModeChange) return;
      setBusy(true);
      try {
        const res = await onModeChange(next);
        if (res && !res.ok) pushNotice('error', res.error || t('git.error.generic'));
      } catch (e) {
        pushNotice('error', (e as Error)?.message || t('git.error.generic'));
      } finally {
        setBusy(false);
      }
    },
    [onModeChange, pushNotice, t],
  );

  return (
    <>
      <RepoStatusBar
        status={status}
        branches={branches}
        loading={statusLoading || branchesLoading}
        busy={busy}
        notice={notice}
        recentBranches={recent}
        mode={worktreeModeOf(cwd)}
        modeEditable={modeEditable}
        onModeChange={(next) => void handleModeChange(next)}
        onCheckout={(branch, opts) => void doCheckout(branch, opts)}
        onDeleteBranch={(branch) =>
          pushConfirm({
            testId: 'delete',
            tone: 'danger',
            title: t('git.confirm.deleteTitle'),
            body: t('git.confirm.deleteBody', { branch }),
            confirmLabel: t('git.confirm.deleteConfirm'),
            onConfirm: () => void doDelete(branch),
          })
        }
        onOpenBranches={() => void loadBranches()}
      />
      <GitConfirmModal spec={confirm} busy={busy} onCancel={dismissConfirm} />
    </>
  );
};

export default GitRepoBar;
