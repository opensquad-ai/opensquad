/**
 * SessionSearchModal — search modal for the Agent Web sidebar.
 *
 * Two modes:
 *   - No query: shows the current agent's recent sessions grouped by time
 *     (今天 / 昨天 / 最近七天 / 更早). The list comes from the ``sessions``
 *     prop (the same source as the sidebar list), so it is always fresh.
 *   - With query: runs a backend fuzzy search across user input + agent
 *     non-tool text replies, also grouped by the same time buckets.
 *
 * The empty state is intentionally friendly: when the user has typed
 * something but found nothing, we show a hint with a one-click "新建对话"
 * shortcut, instead of a red error.
 *
 * Triggered by the "搜索" button in SessionSidebar or Ctrl/Cmd+K.
 */
import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Search,
  X,
  CornerDownLeft,
  MessageSquare,
  Sparkles,
  Loader2,
  MessageSquarePlus,
} from 'lucide-react';
import { SoftOverlay } from '../SoftOverlay';
import { agentSessionAPI, type AgentSession } from '../../services/api';

type SearchMatch = {
  role: 'user' | 'assistant';
  snippet: string;
  timestamp?: string;
};

type SearchResult = {
  id: string;
  title: string;
  matches: SearchMatch[];
  last_updated?: string | null;
  created_at?: string | null;
  /** Optional pre-baked preview text (only present for default list mode). */
  preview?: string;
};

interface SessionSearchModalProps {
  open: boolean;
  agentId: string;
  /** Existing session list (sidebar). Used as the default list when no query. */
  sessions: AgentSession[];
  /** Active workspace root path — empty = no filter, used to annotate results. */
  workspaceRootPath: string | null;
  onCancel: () => void;
  onPick: (sessionId: string) => void;
  onNewSession: () => void;
}

const SEARCH_DEBOUNCE_MS = 220;
const MAX_RESULTS = 50;
const MAX_VISIBLE_RESULTS = 12;

type Bucket = 'today' | 'yesterday' | 'last7days' | 'earlier';

const BUCKET_ORDER: Bucket[] = ['today', 'yesterday', 'last7days', 'earlier'];

function dayBucket(iso: string | null | undefined, now: number): Bucket {
  if (!iso) return 'earlier';
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return 'earlier';
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const startToday = today.getTime();
  const startYesterday = startToday - 86400000;
  const start7days = startToday - 7 * 86400000;
  if (ts >= startToday) return 'today';
  if (ts >= startYesterday) return 'yesterday';
  if (ts >= start7days) return 'last7days';
  return 'earlier';
}

function formatBucketLabel(bucket: Bucket, locale: 'zh' | 'en'): string {
  if (locale === 'en') {
    if (bucket === 'today') return 'Today';
    if (bucket === 'yesterday') return 'Yesterday';
    if (bucket === 'last7days') return 'Last 7 days';
    return 'Earlier';
  }
  if (bucket === 'today') return '今天';
  if (bucket === 'yesterday') return '昨天';
  if (bucket === 'last7days') return '最近七天';
  return '更早';
}

function formatTime(iso: string | null | undefined, locale: 'zh' | 'en'): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  if (locale === 'en') {
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
  }
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function highlightSnippet(snippet: string, query: string): React.ReactNode {
  if (!snippet) return null;
  const q = query.trim();
  if (!q) return snippet;
  // Case-insensitive split on the first token to keep DOM small.
  const token = q.split(/\s+/).find((t) => t.length > 0) || q;
  const lowerSnippet = snippet.toLowerCase();
  const lowerToken = token.toLowerCase();
  const out: React.ReactNode[] = [];
  let cursor = 0;
  let idx = lowerSnippet.indexOf(lowerToken, cursor);
  let key = 0;
  while (idx >= 0) {
    if (idx > cursor) {
      out.push(<React.Fragment key={`t-${key}`}>{snippet.slice(cursor, idx)}</React.Fragment>);
    }
    out.push(
      <mark
        key={`m-${key}`}
        className="bg-primary/20 text-textMain rounded px-[1px] mx-[-1px]"
      >
        {snippet.slice(idx, idx + token.length)}
      </mark>,
    );
    cursor = idx + token.length;
    idx = lowerSnippet.indexOf(lowerToken, cursor);
    key += 1;
  }
  if (cursor < snippet.length) {
    out.push(<React.Fragment key={`t-end`}>{snippet.slice(cursor)}</React.Fragment>);
  }
  return out;
}

function toSearchResult(s: AgentSession): SearchResult {
  return {
    id: s.id,
    title: s.title || s.id,
    matches: [],
    last_updated: s.last_updated || null,
    created_at: s.created_at || null,
    preview: s.preview || '',
  };
}

export const SessionSearchModal: React.FC<SessionSearchModalProps> = ({
  open,
  agentId,
  sessions,
  workspaceRootPath,
  onCancel,
  onPick,
  onNewSession,
}) => {
  const { t, i18n } = useTranslation();
  const locale: 'zh' | 'en' = i18n.language?.startsWith('zh') ? 'zh' : 'en';
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Debounce typing → debounced query
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setDebounced('');
      return;
    }
    const id = window.setTimeout(() => setDebounced(trimmed), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [query]);

  // Reset state when modal opens; auto-focus input
  useEffect(() => {
    if (open) {
      setQuery('');
      setDebounced('');
      setSearchResults([]);
      setError(null);
      setLoading(false);
      setActiveIndex(0);
      // Defer focus so the soft-overlay transition doesn't steal it.
      const id = window.setTimeout(() => {
        inputRef.current?.focus();
      }, 60);
      return () => window.clearTimeout(id);
    }
    return;
  }, [open]);

  // Run search whenever the debounced query changes
  useEffect(() => {
    if (!open || !agentId || !debounced) {
      setSearchResults([]);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const resp = await agentSessionAPI.searchSessions(agentId, debounced, MAX_RESULTS);
        if (cancelled) return;
        setSearchResults(Array.isArray(resp.results) ? resp.results : []);
        setActiveIndex(0);
      } catch (err: any) {
        if (cancelled) return;
        // Keep the friendly empty state; only stash the raw message for
        // diagnostic purposes (rendered in muted text, not red).
        setError(err?.message || t('aiChat.search.failed'));
        setSearchResults([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, agentId, debounced, t]);

  // The list we actually render: either the live sidebar list (no query) or
  // search results (with a query). Hide scheduled_task origin entries.
  const visible: SearchResult[] = useMemo(() => {
    const hasQuery = !!debounced;
    const list = hasQuery ? searchResults : sessions.map(toSearchResult);
    return list.filter((r) => {
      if (!r || !r.id) return false;
      // Hide scheduled-task origin sessions in both modes.
      const origin = (sessions.find((s) => s.id === r.id)?.origin || '').toString();
      if (origin === 'scheduled_task') return false;
      return true;
    });
  }, [debounced, searchResults, sessions]);

  // Group by date bucket (今天 / 昨天 / 最近七天 / 更早)
  const grouped = useMemo(() => {
    const now = Date.now();
    const map: Record<Bucket, SearchResult[]> = {
      today: [],
      yesterday: [],
      last7days: [],
      earlier: [],
    };
    for (const r of visible) {
      map[dayBucket(r.last_updated || r.created_at, now)].push(r);
    }
    return map;
  }, [visible]);

  // Flat list of pickable session ids (for keyboard navigation)
  const flatIds = useMemo(() => {
    const out: string[] = [];
    for (const bucket of BUCKET_ORDER) {
      for (const r of grouped[bucket]) {
        out.push(r.id);
      }
    }
    return out;
  }, [grouped]);

  // Build index offsets for keyboard nav
  const offsets = useMemo(() => {
    const today = 0;
    const yesterday = grouped.today.length;
    const last7days = yesterday + grouped.yesterday.length;
    const earlier = last7days + grouped.last7days.length;
    return { today, yesterday, last7days, earlier };
  }, [grouped]);

  const handlePick = useCallback(
    (sessionId: string) => {
      if (!sessionId) return;
      onPick(sessionId);
    },
    [onPick],
  );

  const scrollActiveIntoView = (idx: number) => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-search-idx="${idx}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  };

  // Keyboard navigation
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCancel();
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIndex((idx) => {
          const next = flatIds.length === 0 ? 0 : Math.min(flatIds.length - 1, idx + 1);
          scrollActiveIntoView(next);
          return next;
        });
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex((idx) => {
          const next = Math.max(0, idx - 1);
          scrollActiveIntoView(next);
          return next;
        });
        return;
      }
      if (e.key === 'Enter') {
        if (flatIds.length === 0) {
          // No results — let Enter create a new conversation (matches the
          // empty-state CTA shown in the UI).
          e.preventDefault();
          onNewSession();
          onCancel();
          return;
        }
        e.preventDefault();
        handlePick(flatIds[activeIndex] || flatIds[0]);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, flatIds, activeIndex, handlePick, onCancel, onNewSession]);

  const sessionLabel = (id: string, fallback: string): string => {
    const s = sessions.find((it) => it.id === id);
    if (s) return s.title || s.id;
    return fallback || id;
  };

  const renderRow = (r: SearchResult, flatIdx: number, mode: 'search' | 'list') => {
    const isActive = flatIdx === activeIndex;
    return (
      <div
        key={r.id}
        data-search-idx={flatIdx}
        role="option"
        aria-selected={isActive}
        onMouseEnter={() => setActiveIndex(flatIdx)}
        onClick={() => handlePick(r.id)}
        className={`group flex items-start gap-2 px-3 py-2 cursor-pointer rounded-lg transition-colors ${
          isActive ? 'bg-primary/10' : 'hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'
        }`}
      >
        <div className="w-7 h-7 rounded-md border border-border/60 bg-bgPage flex items-center justify-center shrink-0 mt-0.5">
          <MessageSquare size={14} className="text-textMuted/70" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <div className="text-[13px] font-medium text-textMain truncate flex-1 min-w-0">
              {sessionLabel(r.id, r.title)}
            </div>
            <div className="text-[10px] text-textMuted/70 tabular-nums shrink-0">
              {formatTime(r.last_updated || r.created_at, locale)}
            </div>
          </div>
          {mode === 'search' && r.matches.length > 0 ? (
            <>
              {r.matches.slice(0, 1).map((m, i) => (
                <div
                  key={`${r.id}-m-${i}`}
                  className="text-[12px] text-textMuted/80 truncate flex items-center gap-1.5 mt-0.5"
                >
                  <span
                    className={`shrink-0 text-[10px] px-1 rounded ${
                      m.role === 'user'
                        ? 'bg-sky-500/15 text-sky-600 dark:text-sky-300'
                        : 'bg-violet-500/15 text-violet-600 dark:text-violet-300'
                    }`}
                  >
                    {m.role === 'user'
                      ? locale === 'en'
                        ? 'You'
                        : '你'
                      : locale === 'en'
                      ? 'Agent'
                      : 'Agent'}
                  </span>
                  <span className="truncate">{highlightSnippet(m.snippet, debounced)}</span>
                </div>
              ))}
              {r.matches.length > 1 ? (
                <div className="text-[10px] text-textMuted/60 mt-0.5">
                  {locale === 'en'
                    ? `+${r.matches.length - 1} more match${r.matches.length - 1 > 1 ? 'es' : ''}`
                    : `+${r.matches.length - 1} 条匹配`}
                </div>
              ) : null}
            </>
          ) : r.preview ? (
            <div className="text-[12px] text-textMuted/80 truncate mt-0.5">{r.preview}</div>
          ) : null}
        </div>
      </div>
    );
  };

  const renderSection = (
    bucket: Bucket,
    baseIdx: number,
    mode: 'search' | 'list',
  ) => {
    const list = grouped[bucket];
    if (list.length === 0) return null;
    return (
      <div key={bucket} className="mb-2">
        <div className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-textMuted/70">
          {formatBucketLabel(bucket, locale)}
        </div>
        {list.map((r, i) => renderRow(r, baseIdx + i, mode))}
      </div>
    );
  };

  const hasQuery = !!debounced;
  const totalResults = visible.length;
  const noResults = hasQuery && !loading && totalResults === 0;
  const noSessions = !hasQuery && totalResults === 0;
  const mode: 'search' | 'list' = hasQuery ? 'search' : 'list';

  return (
    <SoftOverlay
      open={open}
      onBackdrop={onCancel}
      panelClassName="w-[min(560px,94vw)] max-h-[80vh] flex flex-col rounded-2xl bg-white dark:bg-[#252526] border border-black/10 dark:border-white/10 shadow-2xl overflow-hidden"
      durationMs={150}
    >
      <div className="flex items-center gap-2 px-4 h-12 border-b border-border/60 shrink-0">
        <Search size={16} className="text-textMuted shrink-0" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('aiChat.search.placeholder')}
          spellCheck={false}
          autoComplete="off"
          className="flex-1 bg-transparent outline-none text-[14px] text-textMain placeholder:text-textMuted/60"
        />
        {loading ? (
          <Loader2 size={14} className="text-textMuted animate-spin shrink-0" />
        ) : null}
        <button
          type="button"
          onClick={onCancel}
          className="p-1 rounded text-textMuted hover:bg-black/5 dark:hover:bg-white/10 shrink-0"
          title={t('common.close')}
          aria-label={t('common.close')}
        >
          <X size={16} />
        </button>
      </div>

      <div
        ref={listRef}
        className="flex-1 min-h-0 overflow-y-auto os-depth-nest os-depth-nest--flush"
        role="listbox"
      >
        {/* Always-on "新建对话" action so the user has a quick way out even
            when the search list is empty. */}
        <div className="px-3 pt-2">
          <button
            type="button"
            onClick={() => {
              onNewSession();
              onCancel();
            }}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-[13px] text-textMain bg-amber-50 hover:bg-amber-100 dark:bg-amber-500/10 dark:hover:bg-amber-500/15 border border-amber-200/60 dark:border-amber-500/20 transition-colors"
          >
            <MessageSquarePlus size={14} className="text-amber-600 dark:text-amber-300" />
            <span className="flex-1 text-left">{t('aiChat.search.newChat')}</span>
            <CornerDownLeft
              size={12}
              className="text-amber-600/70 dark:text-amber-300/70"
            />
          </button>
        </div>

        {noSessions ? (
          <div className="px-4 py-10 flex flex-col items-center gap-2 text-textMuted/70">
            <Sparkles size={20} className="text-textMuted/50" />
            <div className="text-[13px]">
              {t('aiChat.search.emptyTitle', { defaultValue: '还没有任何会话' })}
            </div>
            <div className="text-[12px] text-textMuted/60">
              {t('aiChat.search.emptyBody', {
                defaultValue: '从上方新建一个对话，开始聊天吧。',
              })}
            </div>
          </div>
        ) : noResults ? (
          <div className="px-4 py-10 flex flex-col items-center gap-2 text-textMuted/70">
            <div className="text-[13px] text-textMuted">
              {t('aiChat.search.noMatchesTitle', {
                defaultValue: '没有找到匹配的对话',
              })}
            </div>
            <div className="text-[12px] text-textMuted/60 text-center max-w-[360px]">
              {t('aiChat.search.noMatchesBody', {
                defaultValue: '换个关键词试试，或者直接新建一个对话开始聊天吧',
              })}
            </div>
            {error ? (
              <div className="text-[10px] text-textMuted/40 mt-1 max-w-[360px] text-center">
                {error}
              </div>
            ) : null}
          </div>
        ) : (
          <>
            {renderSection('today', offsets.today, mode)}
            {renderSection('yesterday', offsets.yesterday, mode)}
            {renderSection('last7days', offsets.last7days, mode)}
            {renderSection('earlier', offsets.earlier, mode)}
            {hasQuery && totalResults > MAX_VISIBLE_RESULTS ? (
              <div className="px-3 py-2 text-[10px] text-textMuted/60 text-center">
                {t('aiChat.search.moreResults', { count: totalResults - MAX_VISIBLE_RESULTS })}
              </div>
            ) : null}
          </>
        )}
      </div>

      <div className="border-t border-border/60 px-3 py-2 flex items-center gap-2 shrink-0">
        <div className="flex items-center gap-2 text-[10px] text-textMuted/60">
          <kbd className="px-1.5 py-0.5 rounded border border-border bg-bgPage">↑</kbd>
          <kbd className="px-1.5 py-0.5 rounded border border-border bg-bgPage">↓</kbd>
          <span>{t('aiChat.search.navigateHint')}</span>
          <span className="mx-1">·</span>
          <kbd className="px-1.5 py-0.5 rounded border border-border bg-bgPage flex items-center gap-0.5">
            <CornerDownLeft size={10} />
          </kbd>
          <span>{t('aiChat.search.openHint')}</span>
        </div>
      </div>
    </SoftOverlay>
  );
};
