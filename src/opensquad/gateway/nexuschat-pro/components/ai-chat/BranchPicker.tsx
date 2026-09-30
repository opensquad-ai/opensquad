/**
 * BranchPicker — the branch menu behind the repo status bar.
 *
 * Grouped by how the user thinks about branches: the ones they were just on,
 * then the rest of the local ones, then the remotes they could check out. Each
 * row carries the last commit's age and subject, because a branch name alone
 * rarely says which one holds the work in progress.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, GitBranch, Plus, Search, Trash2, X } from 'lucide-react';
import type { GitBranch as GitBranchRow, GitBranchList } from '../../services/api';
import { groupBranches } from '../../utils/gitRepoState';
import { formatRelativeAge } from '../../utils/time';
import { POPOVER_SURFACE_CLASS, usePopMenuMounted } from './popoverSurface';

export interface BranchPickerProps {
  open: boolean;
  branches: GitBranchList | null;
  loading?: boolean;
  busy?: boolean;
  /** Recently used branch names, newest first (owned by the caller). */
  recentNames?: string[];
  onSelect: (name: string) => void;
  onCreate: (name: string, base?: string) => void;
  onDelete?: (name: string) => void;
  onClose: () => void;
}

export const BranchPicker: React.FC<BranchPickerProps> = ({
  open,
  branches,
  loading = false,
  busy = false,
  recentNames = [],
  onSelect,
  onCreate,
  onDelete,
  onClose,
}) => {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [base, setBase] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const mounted = usePopMenuMounted(open);
  const locale = i18n.language?.startsWith('en') ? 'en' : 'zh';

  useEffect(() => {
    if (!open) {
      setQuery('');
      setCreating(false);
      setNewName('');
      return;
    }
    requestAnimationFrame(() => searchRef.current?.focus());
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const groups = useMemo(() => groupBranches(branches, recentNames), [branches, recentNames]);

  const filter = (rows: GitBranchRow[]): GitBranchRow[] => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) =>
      `${row.name} ${row.subject || ''} ${row.remote || ''}`.toLowerCase().includes(q),
    );
  };

  if (!mounted) return null;

  const row = (branch: GitBranchRow, keyPrefix: string) => {
    const label = branch.remote ? `${branch.remote}/${branch.name}` : branch.name;
    const age = branch.ts ? formatRelativeAge(branch.ts * 1000, { locale }) : '';
    return (
      <div key={`${keyPrefix}:${label}`} className="group/row flex items-center">
        <button
          type="button"
          data-testid="branch-row"
          disabled={busy}
          onClick={() => {
            if (branch.current) {
              onClose();
              return;
            }
            if (branch.remote) onSelect(`${branch.remote}/${branch.name}`);
            else onSelect(branch.name);
          }}
          className={`flex-1 min-w-0 flex items-center gap-2 px-3 py-1.5 text-left text-[12px] transition-colors border-0 cursor-pointer disabled:opacity-50 ${
            branch.current
              ? 'bg-black/[0.06] dark:bg-white/[0.08] text-blue-600 dark:text-blue-400'
              : 'bg-transparent text-textMain hover:bg-black/[0.06] dark:hover:bg-white/[0.10]'
          }`}
        >
          <span className="w-4 shrink-0 flex items-center justify-center">
            {branch.current ? <Check size={13} className="text-blue-600 dark:text-blue-400" /> : null}
          </span>
          <span className="flex-1 min-w-0">
            <span className="block truncate font-medium">{label}</span>
            {branch.subject ? (
              <span className="block truncate text-[10px] text-textMuted">{branch.subject}</span>
            ) : null}
          </span>
          {branch.ahead ? (
            <span className="shrink-0 text-[10px] tabular-nums text-emerald-500">↑{branch.ahead}</span>
          ) : null}
          {branch.behind ? (
            <span className="shrink-0 text-[10px] tabular-nums text-amber-500">↓{branch.behind}</span>
          ) : null}
          {age ? <span className="shrink-0 text-[10px] text-textMuted">{age}</span> : null}
        </button>
        {onDelete && !branch.current && !branch.remote ? (
          <button
            type="button"
            data-testid="branch-delete"
            title={t('git.branch.delete')}
            disabled={busy}
            onClick={() => onDelete(branch.name)}
            className="opacity-100 md:opacity-0 md:group-hover/row:opacity-100 p-1 mr-1 rounded text-textMuted hover:text-rose-600 hover:bg-rose-500/10 transition-opacity disabled:opacity-50"
          >
            <Trash2 size={11} />
          </button>
        ) : null}
      </div>
    );
  };

  const section = (titleKey: string, rows: GitBranchRow[], keyPrefix: string) => {
    const filtered = filter(rows);
    if (filtered.length === 0) return null;
    return (
      <div key={titleKey}>
        <div
          data-testid="branch-group"
          className="px-3 pt-2 pb-1 text-[10px] font-medium text-textMuted/80"
        >
          {t(titleKey)}
        </div>
        {filtered.map((branch) => row(branch, keyPrefix))}
      </div>
    );
  };

  return (
    <div
      ref={rootRef}
      data-testid="branch-picker"
      className={`absolute bottom-[calc(100%+8px)] left-0 z-[220] w-[300px] origin-bottom-left rounded-xl border border-border ${POPOVER_SURFACE_CLASS} overflow-hidden ${
        open ? 'os-pop-menu' : 'os-pop-menu-out'
      }`}
      role="listbox"
    >
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border/60">
        <Search size={12} className="text-textMuted shrink-0" />
        <input
          ref={searchRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('git.branch.search')}
          className="flex-1 min-w-0 bg-transparent text-[12px] outline-none placeholder:text-textMuted/70"
        />
        {query ? (
          <button type="button" onClick={() => setQuery('')} className="text-textMuted hover:text-textMain">
            <X size={12} />
          </button>
        ) : null}
      </div>

      <div className="max-h-[320px] overflow-y-auto">
        {loading && !branches ? (
          <div className="px-3 py-4 text-[12px] text-textMuted text-center">{t('git.branch.loading')}</div>
        ) : (
          <>
            {section('git.branch.recent', groups.recent, 'recent')}
            {section('git.branch.local', groups.local, 'local')}
            {section('git.branch.remote', groups.remote, 'remote')}
          </>
        )}
      </div>

      <div className="border-t border-border/60">
        {creating ? (
          <div className="px-3 py-2 space-y-1.5">
            <input
              value={newName}
              autoFocus
              onChange={(e) => setNewName(e.target.value)}
              placeholder={t('git.branch.newName')}
              className="w-full rounded-lg border border-border bg-transparent px-2 py-1 text-[12px] outline-none focus:border-primary/60"
            />
            <input
              value={base}
              onChange={(e) => setBase(e.target.value)}
              placeholder={t('git.branch.newBase', { branch: branches?.current || '' })}
              className="w-full rounded-lg border border-border bg-transparent px-2 py-1 text-[12px] outline-none focus:border-primary/60"
            />
            <div className="flex items-center gap-1.5 justify-end">
              <button
                type="button"
                onClick={() => {
                  setCreating(false);
                  setNewName('');
                  setBase('');
                }}
                className="px-2 py-1 rounded-lg text-[11px] text-textMuted hover:bg-black/5 dark:hover:bg-white/10"
              >
                {t('common.cancel')}
              </button>
              <button
                type="button"
                data-testid="branch-create-confirm"
                disabled={!newName.trim() || busy}
                onClick={() => onCreate(newName.trim(), base.trim() || undefined)}
                className="px-2 py-1 rounded-lg text-[11px] font-medium bg-primary text-white disabled:opacity-50"
              >
                {t('git.branch.create')}
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            data-testid="branch-create"
            onClick={() => {
              setCreating(true);
              setBase('');
            }}
            className="w-full flex items-center gap-2 px-3 py-2 text-left text-[12px] text-textMain hover:bg-black/[0.06] dark:hover:bg-white/[0.10] border-0 bg-transparent cursor-pointer"
          >
            <span className="w-4 shrink-0 flex items-center justify-center">
              <Plus size={13} className="text-textMuted" />
            </span>
            <span className="flex-1 min-w-0 truncate font-medium">{t('git.branch.create')}</span>
            <GitBranch size={12} className="text-textMuted/50" />
          </button>
        )}
      </div>
    </div>
  );
};

export default BranchPicker;
