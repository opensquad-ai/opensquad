/**
 * RepoStatusBar — the git cluster in the composer footer, next to the folder.
 *
 * Deliberately minimal: the mode chip (本地 / Worktree, only switchable while
 * the session is still a draft) and the branch chip (switch + create, via
 * :class:`BranchPicker`). No status yet — loading or a failed request — is
 * silence; a directory without a repository keeps the chips but renders them
 * inert: grey, unclickable, the branch slot labelled "非 Git 仓库". A refused
 * command surfaces as a floating pill above the chips; the
 * data, polling and confirmations live in :class:`GitRepoBar`.
 */
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ChevronDown, GitBranch, Monitor } from 'lucide-react';
import type { GitBranchList, GitStatus } from '../../services/api';
import { branchDisplay, type GitWorktreeMode } from '../../utils/gitRepoState';
import { BranchPicker } from './BranchPicker';
import { POPOVER_SURFACE_CLASS, usePopMenuMounted } from './popoverSurface';

export interface RepoNotice {
  tone: 'info' | 'error';
  text: string;
}

export interface RepoStatusBarProps {
  status: GitStatus | null;
  branches?: GitBranchList | null;
  loading?: boolean;
  /** A command is in flight: the chips are disabled. */
  busy?: boolean;
  notice?: RepoNotice | null;
  recentBranches?: string[];
  onCheckout: (branch: string, opts?: { create?: boolean; base?: string; stashDirty?: boolean }) => void;
  onDeleteBranch?: (branch: string) => void;
  /** The picker is opening: a hint that the branch list may be stale. */
  onOpenBranches?: () => void;
  /** Current Local/Worktree mode; absent when the page does not offer switching. */
  mode?: GitWorktreeMode;
  /** A session that already has messages cannot change its mode any more. */
  modeEditable?: boolean;
  onModeChange?: (next: GitWorktreeMode) => void;
}

const _MODE_ROW: Array<{ value: GitWorktreeMode; labelKey: string; icon: typeof Monitor }> = [
  { value: 'local', labelKey: 'git.mode.local', icon: Monitor },
  { value: 'worktree', labelKey: 'git.mode.worktree', icon: GitBranch },
];

export const RepoStatusBar: React.FC<RepoStatusBarProps> = ({
  status,
  branches = null,
  loading = false,
  busy = false,
  notice = null,
  recentBranches = [],
  onCheckout,
  onDeleteBranch,
  onOpenBranches,
  mode,
  modeEditable = false,
  onModeChange,
}) => {
  const { t } = useTranslation();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [modeOpen, setModeOpen] = useState(false);
  const modeRootRef = useRef<HTMLDivElement>(null);
  const modeMounted = usePopMenuMounted(modeOpen);

  useEffect(() => {
    if (!modeOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!modeRootRef.current?.contains(e.target as Node)) setModeOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setModeOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [modeOpen]);

  if (!status) return null;

  const notRepo = !status.is_repo;
  const branch = branchDisplay(status);
  const modeChipClass =
    'flex items-center gap-1 min-w-0 border-0 bg-transparent p-0 text-textMuted/70 hover:text-textMuted transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-default';

  return (
    <div data-testid="repo-status-bar" className="relative flex items-center gap-2.5 min-w-0 shrink-0">
      {mode && onModeChange ? (
        <div ref={modeRootRef} className="relative min-w-0 shrink-0">
          {notRepo ? (
            <span
              data-testid="repo-mode"
              data-mode={mode}
              title={t('git.notRepo')}
              className="flex items-center gap-1 min-w-0 text-textMuted/40"
            >
              {mode === 'worktree' ? <GitBranch size={12} className="shrink-0" /> : <Monitor size={12} className="shrink-0" />}
              <span className="text-[11px]">{t(mode === 'worktree' ? 'git.mode.worktree' : 'git.mode.local')}</span>
            </span>
          ) : modeEditable ? (
            <button
              type="button"
              data-testid="repo-mode"
              data-mode={mode}
              disabled={busy}
              onClick={() => setModeOpen((v) => !v)}
              title={t('git.mode.switch')}
              className={modeChipClass}
            >
              {mode === 'worktree' ? <GitBranch size={12} className="shrink-0" /> : <Monitor size={12} className="shrink-0" />}
              <span className="text-[11px]">{t(mode === 'worktree' ? 'git.mode.worktree' : 'git.mode.local')}</span>
              <ChevronDown size={11} className={`shrink-0 opacity-45 transition-transform ${modeOpen ? 'rotate-180' : ''}`} />
            </button>
          ) : (
            <span data-testid="repo-mode" data-mode={mode} title={t('git.mode.locked')} className="flex items-center gap-1 min-w-0 text-textMuted/45">
              {mode === 'worktree' ? <GitBranch size={12} className="shrink-0" /> : <Monitor size={12} className="shrink-0" />}
              <span className="text-[11px]">{t(mode === 'worktree' ? 'git.mode.worktree' : 'git.mode.local')}</span>
            </span>
          )}

          {modeMounted ? (
            <div
              className={`absolute bottom-[calc(100%+6px)] left-0 z-[220] w-[160px] origin-bottom-left rounded-xl border border-border ${POPOVER_SURFACE_CLASS} overflow-hidden ${
                modeOpen ? 'os-pop-menu' : 'os-pop-menu-out'
              }`}
              role="menu"
            >
              {_MODE_ROW.map(({ value, labelKey, icon: Icon }) => (
                <button
                  key={value}
                  type="button"
                  role="menuitem"
                  data-testid="repo-mode-option"
                  data-mode={value}
                  disabled={busy}
                  onClick={() => {
                    setModeOpen(false);
                    if (value !== mode) onModeChange(value);
                  }}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-[12px] text-textMain hover:bg-black/[0.06] dark:hover:bg-white/[0.10] border-0 bg-transparent cursor-pointer disabled:opacity-50"
                >
                  <Icon size={13} className="shrink-0 text-textMuted" />
                  <span className="flex-1 min-w-0 truncate">{t(labelKey)}</span>
                  {value === mode ? <Check size={12} className="shrink-0 text-primary" /> : null}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="relative min-w-0 shrink-0">
        {notRepo ? (
          <span data-testid="repo-branch" title={t('git.notRepo')} className="flex items-center gap-1 min-w-0 text-textMuted/40">
            <GitBranch size={12} className="shrink-0" />
            <span className="max-w-[120px] truncate text-[11px]">{t('git.notRepo')}</span>
          </span>
        ) : (
          <>
            <button
              type="button"
              data-testid="repo-branch"
              disabled={busy}
              onClick={() => {
                const next = !pickerOpen;
                if (next) onOpenBranches?.();
                setPickerOpen(next);
              }}
              title={t('git.branch.switch')}
              className={modeChipClass}
            >
              <GitBranch size={12} className="shrink-0" />
              <span className="max-w-[120px] truncate text-[11px]">
                {branch.kind === 'branch'
                  ? branch.name
                  : branch.kind === 'detached'
                    ? `${t('git.detached')} ${branch.sha}`
                    : t('git.noBranch')}
              </span>
              <ChevronDown size={11} className={`shrink-0 opacity-45 transition-transform ${pickerOpen ? 'rotate-180' : ''}`} />
            </button>

            <BranchPicker
              open={pickerOpen}
              branches={branches}
              loading={loading}
              busy={busy}
              recentNames={recentBranches}
              onSelect={(name) => {
                setPickerOpen(false);
                onCheckout(name);
              }}
              onCreate={(name, base) => {
                setPickerOpen(false);
                onCheckout(name, { create: true, base });
              }}
              onDelete={onDeleteBranch}
              onClose={() => setPickerOpen(false)}
            />
          </>
        )}
      </div>

      {notice ? (
        <div
          data-testid="repo-notice"
          className={`absolute bottom-[calc(100%+6px)] left-0 z-50 flex w-max max-w-[320px] items-start rounded-md px-2 py-1 text-[11px] font-medium shadow-lg pointer-events-none ${
            notice.tone === 'error' ? 'bg-rose-600 text-white' : 'bg-black/80 text-white dark:bg-white/90 dark:text-black'
          }`}
        >
          <span className="min-w-0">{notice.text}</span>
        </div>
      ) : null}
    </div>
  );
};

export default RepoStatusBar;
