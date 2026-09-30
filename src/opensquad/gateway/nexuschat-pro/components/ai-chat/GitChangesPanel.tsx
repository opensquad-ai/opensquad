/**
 * GitChangesPanel — the git half of the file panel's 改动 view.
 *
 * The panel already answers "what did this conversation change" from the session
 * baseline. This adds the other question — "what is uncommitted in git" — and the
 * actions that only make sense with an index: stage, unstage, discard, commit,
 * and undo the last commit while it is still local.
 *
 * The 本对话 / 全部 chips are the switch between the two lists, and the rows come
 * from :mod:`utils/gitChanges` so the derivation (which rows, what each can do,
 * what is checked) is testable without rendering. Everything write-shaped goes
 * through `gitAPI`, and every refusal is answered with the confirmation that
 * unblocks it rather than an error line: a discard asks first (and asks again by
 * name when the selection includes a file git does not track yet), and undo is
 * only offered where the last commit could still be local.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ChevronRight, FileDiff, RefreshCw, Trash2, Undo2 } from 'lucide-react';
import { gitAPI, type GitDiff, type GitStatus } from '../../services/api';
import {
  buildChangeRows,
  canStage,
  canUndoLastCommit,
  canUnstage,
  commitBlocker,
  diffModeFor,
  gitStatusLabel,
  isStaged,
  selectionState,
  splitActions,
  type ChangeScope,
  type SessionChangeInput,
} from '../../utils/gitChanges';
import { gitErrorKey } from '../../utils/gitRepoState';
import { ControlledFold, FoldChevron } from '../Collapse';
import { OpenSquadLoader } from '../OpenSquadLoader';
import { UnifiedDiffView, type DiffLine } from './UnifiedDiffView';
import { GitConfirmModal, type GitConfirmSpec } from './GitConfirmModal';

export interface GitChangesPanelProps {
  agentId: string;
  rootPath: string;
  /** The session baseline, already normalised to repo-relative paths. */
  sessionRows: SessionChangeInput[];
  /** Fired after a write so the caller can refresh its own badges. */
  onChanged?: () => void;
  /** The caller draws its own 本对话 / 全部 chips (the file panel does). */
  hideScopeChips?: boolean;
}

const _rowGlyphClass = (glyph: string): string =>
  glyph === '?'
    ? 'text-emerald-700/80 dark:text-emerald-400/70'
    : glyph === 'D'
      ? 'text-rose-500/80'
      : glyph === 'U'
        ? 'text-amber-600 dark:text-amber-400'
        : 'text-textMuted/60';

export const GitChangesPanel: React.FC<GitChangesPanelProps> = ({
  agentId,
  rootPath,
  sessionRows,
  onChanged,
  hideScopeChips = false,
}) => {
  const { t } = useTranslation();
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<ChangeScope>('session');
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [diffs, setDiffs] = useState<Record<string, GitDiff>>({});
  const [diffLoading, setDiffLoading] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [confirmStack, setConfirmStack] = useState<GitConfirmSpec[]>([]);

  const genRef = useRef(0);

  const failText = useCallback(
    (res: { code?: string; error?: string }): string => {
      const key = gitErrorKey(res.code);
      return key === 'git.error.generic' && res.error ? `${t(key)}: ${res.error}` : t(key);
    },
    [t],
  );

  const loadStatus = useCallback(async () => {
    const gen = genRef.current;
    try {
      const res = await gitAPI.status(agentId, rootPath);
      if (gen === genRef.current) setStatus(res);
    } catch (e) {
      if (gen === genRef.current) setError((e as Error)?.message || null);
    } finally {
      if (gen === genRef.current) setLoading(false);
    }
  }, [agentId, rootPath]);

  useEffect(() => {
    genRef.current += 1;
    setSelected(new Set());
    setExpanded(new Set());
    setDiffs({});
    setError(null);
    setLoading(true);
    void loadStatus();
    return () => {
      genRef.current += 1;
    };
  }, [loadStatus]);

  const rows = useMemo(() => buildChangeRows(sessionRows, status, scope), [sessionRows, status, scope]);
  const stagedRows = useMemo(() => rows.filter(isStaged), [rows]);
  const allState = selectionState(rows, selected);
  const blocker = commitBlocker(title, stagedRows.length);

  // ---------------------------------------------------------------- actions

  const runGit = useCallback(
    async (fn: () => Promise<{ ok: boolean; code?: string; error?: string }>): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        const res = await fn();
        if (!res.ok) {
          setError(failText(res));
          return false;
        }
        setSelected(new Set());
        await loadStatus();
        onChanged?.();
        return true;
      } catch (e) {
        setError((e as Error)?.message || t('git.error.generic'));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [failText, loadStatus, onChanged, t],
  );

  const applySelection = useCallback(
    async (kind: 'stage' | 'unstage' | 'discard', extra?: { confirmUntracked?: boolean }) => {
      const actions = splitActions(rows, selected);
      if (kind === 'discard') {
        await runGit(() =>
          gitAPI.discard(agentId, actions.discard, rootPath, extra?.confirmUntracked ?? false),
        );
        return;
      }
      const paths = kind === 'stage' ? actions.stage : actions.unstage;
      if (paths.length === 0) return;
      await runGit(() =>
        kind === 'stage' ? gitAPI.stage(agentId, paths, rootPath) : gitAPI.unstage(agentId, paths, rootPath),
      );
    },
    [agentId, rows, rootPath, runGit, selected],
  );

  const askDiscard = useCallback(() => {
    const actions = splitActions(rows, selected);
    if (actions.discard.length === 0) return;
    setConfirmStack((stack) => [
      ...stack,
      {
        testId: 'discard',
        tone: 'danger',
        title: t('git.confirm.discardTitle'),
        body: t('git.confirm.discardBody'),
        hint: actions.discardIncludesUntracked ? t('git.changes.discardUntrackedHint') : undefined,
        confirmLabel: t('git.confirm.discardConfirm'),
        onConfirm: () => void applySelection('discard', { confirmUntracked: actions.discardIncludesUntracked }),
      },
    ]);
  }, [applySelection, rows, selected, t]);

  const askUndo = useCallback(() => {
    setConfirmStack((stack) => [
      ...stack,
      {
        testId: 'undo-commit',
        tone: 'danger',
        title: t('git.confirm.undoTitle'),
        body: t('git.confirm.undoBody'),
        confirmLabel: t('git.confirm.undoConfirm'),
        onConfirm: () => {
          void runGit(() => gitAPI.undoCommit(agentId, rootPath));
        },
      },
    ]);
  }, [agentId, rootPath, runGit, t]);

  const doCommit = useCallback(async () => {
    if (blocker) {
      setError(t(gitErrorKey(blocker)));
      return;
    }
    const ok = await runGit(() => gitAPI.commit(agentId, title.trim(), description.trim(), rootPath));
    if (ok) {
      setTitle('');
      setDescription('');
    }
  }, [agentId, blocker, description, rootPath, runGit, t, title]);

  const toggleDiff = useCallback(
    async (path: string, mode: 'worktree' | 'staged') => {
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(path)) next.delete(path);
        else next.add(path);
        return next;
      });
      if (diffs[path]) return;
      setDiffLoading(path);
      try {
        const res = await gitAPI.diff(agentId, path, rootPath, { mode });
        setDiffs((prev) => ({ ...prev, [path]: res }));
      } catch (e) {
        setError((e as Error)?.message || t('git.error.generic'));
      } finally {
        setDiffLoading((prev) => (prev === path ? null : prev));
      }
    },
    [agentId, diffs, rootPath, t],
  );

  const toggleSelected = (path: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const toggleAll = () => {
    if (allState === 'all') {
      setSelected(new Set());
      return;
    }
    const next = new Set<string>();
    for (const row of rows) {
      if (canStage(row) || canUnstage(row)) next.add(row.path);
    }
    setSelected(next);
  };

  const actions = splitActions(rows, selected);
  const confirm = confirmStack.length > 0 ? confirmStack[confirmStack.length - 1] : null;

  if (loading && !status) {
    return (
      <div className="flex items-center gap-2 px-3 py-3 text-[11px] text-textMuted">
        <OpenSquadLoader size={18} />
      </div>
    );
  }

  if (!status?.is_repo) {
    return (
      <div className="px-3 py-3 text-[11px] text-textMuted/70" data-testid="git-changes-no-repo">
        {t('git.changes.notRepo')}
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-0 flex-1" data-testid="git-changes">
      <div className="flex items-center gap-1.5 px-2 py-1.5 border-b border-border/40 flex-shrink-0">
        {hideScopeChips
          ? null
          : (['session', 'all'] as ChangeScope[]).map((id) => (
          <button
            key={id}
            type="button"
            data-testid={`git-scope-${id}`}
            onClick={() => {
              setScope(id);
              setSelected(new Set());
            }}
            className={`px-2 py-0.5 rounded-full text-[10px] transition-colors ${
              scope === id
                ? 'bg-black/[0.07] dark:bg-white/[0.10] text-textMain font-medium'
                : 'text-textMuted/60 hover:text-textMuted'
            }`}
          >
            {t(id === 'session' ? 'git.changes.scopeSession' : 'git.changes.scopeAll')}
          </button>
        ))}
        <span className="flex-1" />
        <span className="text-[10px] text-textMuted/50 tabular-nums" data-testid="git-changes-counts">
          {t('git.changes.stagedCount', { n: stagedRows.length })}
        </span>
        <button
          type="button"
          data-testid="git-changes-refresh"
          disabled={busy}
          onClick={() => void loadStatus()}
          title={t('git.refresh')}
          className="p-1 rounded text-textMuted/60 hover:text-textMain hover:bg-black/[0.05] dark:hover:bg-white/[0.08] disabled:opacity-40"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="flex items-center gap-2 px-2 py-1 border-b border-border/40 flex-shrink-0">
        <button
          type="button"
          data-testid="git-select-all"
          disabled={busy || rows.length === 0}
          onClick={toggleAll}
          className="inline-flex items-center gap-1.5 text-[10px] text-textMuted/70 hover:text-textMuted disabled:opacity-40"
        >
          <span
            className={`w-3 h-3 rounded-[3px] border flex items-center justify-center ${
              allState === 'none' ? 'border-border' : 'bg-primary border-primary'
            }`}
          >
            {allState === 'all' ? (
              <Check size={9} className="text-white" />
            ) : allState === 'some' ? (
              <span className="w-1.5 h-[1.5px] rounded bg-white" />
            ) : null}
          </span>
          {t('git.changes.selectAll')}
        </button>
        <span className="flex-1" />
        <span className="text-[10px] text-textMuted/50 tabular-nums" data-testid="git-changes-selected">
          {t('git.changes.selectedCount', { n: selected.size })}
        </span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto" data-testid="git-changes-rows">
        {rows.length === 0 ? (
          <div className="px-3 py-3 text-[11px] text-textMuted/60">{t('git.changes.noChanges')}</div>
        ) : (
          rows.map((row) => {
            const glyph = gitStatusLabel(row);
            const isOpenRow = expanded.has(row.path);
            const diff = diffs[row.path];
            const stageable = canStage(row) || canUnstage(row);
            // One derivation for both the chevron and the label, so the row can
            // never open two different diffs depending on where it was clicked.
            const mode = diffModeFor(row);
            const openRowDiff = () => void toggleDiff(row.path, mode);
            return (
              <div key={row.path} className="border-b border-border/40 last:border-b-0">
                <div
                  className={`group flex items-center gap-1 px-2 py-[5px] text-[11px] ${
                    isOpenRow ? 'text-textMuted' : 'text-textMuted/70'
                  }`}
                  data-testid="git-change-row"
                  data-path={row.path}
                >
                  <input
                    type="checkbox"
                    data-testid="git-change-check"
                    className="w-3 h-3 shrink-0 accent-[var(--color-primary,#3b82f6)] disabled:opacity-30"
                    checked={selected.has(row.path)}
                    disabled={busy || !stageable}
                    onChange={() => toggleSelected(row.path)}
                  />
                  <span
                    className={`w-3 shrink-0 text-center font-mono text-[10px] ${_rowGlyphClass(glyph)}`}
                    title={glyph}
                  >
                    {glyph}
                  </span>
                  <button
                    type="button"
                    data-testid="git-change-toggle"
                    className="p-0.5 rounded shrink-0 text-textMuted/50 hover:text-textMuted"
                    onClick={openRowDiff}
                  >
                    {isOpenRow ? <FoldChevron open /> : <ChevronRight size={11} />}
                  </button>
                  <button
                    type="button"
                    className="flex-1 min-w-0 text-left truncate font-mono text-[11px]"
                    title={row.path}
                    onClick={openRowDiff}
                  >
                    {row.path}
                  </button>
                  {isStaged(row) ? (
                    <span
                      data-testid="git-change-staged"
                      className="shrink-0 rounded px-1 text-[9px] text-emerald-700/80 dark:text-emerald-400/70 bg-emerald-500/10"
                    >
                      {t('git.changes.stagedBadge')}
                    </span>
                  ) : null}
                  <span className="flex items-center gap-1 shrink-0 text-[10px] tabular-nums font-mono">
                    {(row.additions || 0) > 0 ? (
                      <span className="text-emerald-600/70">+{row.additions}</span>
                    ) : null}
                    {(row.deletions || 0) > 0 ? (
                      <span className="text-rose-500/60">-{row.deletions}</span>
                    ) : null}
                  </span>
                </div>
                <ControlledFold open={isOpenRow}>
                  <div className="max-h-[280px] overflow-auto border-t border-border/30 bg-bgLight/80">
                    {diffLoading === row.path && !diff ? (
                      <div className="flex items-center gap-2 px-3 py-2 text-[11px] text-textMuted">
                        <OpenSquadLoader size={14} />
                      </div>
                    ) : diff?.error ? (
                      <div className="px-3 py-2 text-[11px] text-textMuted">{diff.error}</div>
                    ) : diff ? (
                      <UnifiedDiffView
                        fileName={row.name}
                        lines={diff.lines as DiffLine[]}
                        additions={diff.additions}
                        deletions={diff.deletions}
                        oversized={diff.oversized}
                      />
                    ) : null}
                  </div>
                </ControlledFold>
              </div>
            );
          })
        )}
      </div>

      <div className="flex-shrink-0 border-t border-border/60 px-2 py-1.5 space-y-1.5">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            data-testid="git-stage"
            disabled={busy || actions.stage.length === 0}
            onClick={() => void applySelection('stage')}
            className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] border border-border/60 text-textMain hover:bg-primary/10 disabled:opacity-40"
          >
            {t('git.changes.stage')}
          </button>
          <button
            type="button"
            data-testid="git-unstage"
            disabled={busy || actions.unstage.length === 0}
            onClick={() => void applySelection('unstage')}
            className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] border border-border/60 text-textMain hover:bg-primary/10 disabled:opacity-40"
          >
            {t('git.changes.unstage')}
          </button>
          <button
            type="button"
            data-testid="git-discard"
            disabled={busy || actions.discard.length === 0}
            onClick={askDiscard}
            className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] border border-border/60 text-rose-600 dark:text-rose-400 hover:bg-rose-500/10 disabled:opacity-40"
          >
            <Trash2 size={10} />
            {t('git.changes.discard')}
          </button>
          <span className="flex-1" />
          {canUndoLastCommit(status) ? (
            <button
              type="button"
              data-testid="git-undo-commit"
              disabled={busy}
              onClick={askUndo}
              title={t('git.changes.undoLastHint')}
              className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] border border-border/60 text-textMain hover:bg-primary/10 disabled:opacity-40"
            >
              <Undo2 size={10} />
              {t('git.changes.undoLast')}
            </button>
          ) : null}
        </div>

        <div className="space-y-1">
          <input
            value={title}
            data-testid="git-commit-title"
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t('git.changes.commitTitlePlaceholder')}
            className="w-full rounded-md border border-border/60 bg-transparent px-2 py-1 text-[11px] outline-none focus:border-primary/50"
          />
          <textarea
            value={description}
            data-testid="git-commit-description"
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t('git.changes.commitDescriptionPlaceholder')}
            rows={2}
            className="w-full resize-none rounded-md border border-border/60 bg-transparent px-2 py-1 text-[11px] outline-none focus:border-primary/50"
          />
          <div className="flex items-center gap-2">
            <span className="flex-1 min-w-0 truncate text-[10px] text-textMuted/60" data-testid="git-commit-hint">
              {blocker ? t(`git.error.${blocker}`) : t('git.changes.commitHint', { n: stagedRows.length })}
            </span>
            <button
              type="button"
              data-testid="git-commit"
              disabled={busy || !!blocker}
              onClick={() => void doCommit()}
              className="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 text-[11px] font-medium text-white hover:bg-primary/90 disabled:opacity-40"
            >
              {busy ? <OpenSquadLoader size={12} /> : <FileDiff size={11} />}
              {t('git.changes.commit')}
            </button>
          </div>
        </div>

        {error ? (
          <div className="text-[10px] text-rose-500" data-testid="git-changes-error">
            {error}
          </div>
        ) : null}
      </div>

      <GitConfirmModal spec={confirm} busy={busy} onCancel={() => setConfirmStack((s) => s.slice(0, -1))} />
    </div>
  );
};

export default GitChangesPanel;
