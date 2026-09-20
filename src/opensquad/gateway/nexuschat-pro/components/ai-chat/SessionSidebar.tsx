/**
 * SessionSidebar — sessions for the active workspace, grouped by
 * 置顶 / 通讯 / 最近 / 归档.
 */
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { OpenSquadLoader } from '../OpenSquadLoader';
import { Collapse, FoldChevron } from '../Collapse';
import {
  Trash2,
  Check,
  X,
  Pin,
  PinOff,
  Pencil,
  MessageSquarePlus,
  Sparkles,
  Puzzle,
  UserCircle,
  Archive,
  ChevronRight,
  BookOpen,
  Code2,
  MessageCircle,
  LayoutGrid,
  Clock,
  ListTodo,
  Search,
} from 'lucide-react';
import { agentSessionAPI, AgentSession } from '../../services/api';
import {
  loadSessionProjectMeta,
  setSessionPinned,
  setSessionArchived,
  SESSION_META_EVENT,
  SESSION_LIST_REFRESH_EVENT,
  type SessionProjectMeta,
} from '../../utils/sessionProjectMeta';
import {
  getCachedSessionTimeline,
  putCachedSessionTimeline,
  SESSION_HISTORY_PAGE_SIZE,
} from '../../utils/sessionTimelineCache';
import { buildTimelineFromSession } from '../../utils/aiChatTimeline';
import { pathsEqual } from '../../utils/workspaceStore';
import {
  groupSessionsForSidebar,
  resolveCommsSessionId,
  withCommsSession,
} from '../../utils/sessionSidebarGroups';
import {
  advanceLoadedRows,
  appendSessionPage,
  isNearListEnd,
  mergeRefreshedPrefix,
  refreshLimit,
  SESSION_LIST_PAGE_SIZE,
} from '../../utils/sessionListWindow';
import { SOFT_PRESENCE_MS, useSoftPresence } from '../../utils/useSoftPresence';
import { formatRelativeAge } from '../../utils/time';
import { PulseDotsOrbit } from './PulseDotsStatus';
import { AccountRailFooter, type AccountUser } from '../AccountRailFooter';
import { AgentNavShortcutAvatars } from '../AgentNavShortcutAvatars';
import { navigateAppView } from '../../utils/appNavItems';

interface SessionSidebarProps {
  agentId: string;
  currentSessionId: string | null;
  /** Active workspace absolute path — filters the list. */
  workspaceRootPath: string | null;
  workspaceId: string | null;
  onViewSession: (sessionId: string) => void;
  onNewSession: (projectPath?: string) => void;
  /** @deprecated double-click now toggles batch-select mode. */
  onSwitchAndReply?: (sessionId: string) => void;
  /** Optional override for delete (e.g. abandon empty current via new_session first). */
  onDeleteSession?: (sessionId: string) => Promise<void>;
  onOpenSkills?: () => void;
  onOpenPlugins?: () => void;
  onOpenRoles?: () => void;
  onOpenScheduledTasks?: () => void;
  onOpenTasks?: () => void;
  onOpenSearch?: () => void;
  /** Highlight Skill 库 when the in-chat skills panel is open. */
  skillsActive?: boolean;
  /** Highlight 插件 when the in-chat plugins panel is open. */
  pluginsActive?: boolean;
  /** Highlight 角色 when the in-chat roles panel is open. */
  rolesActive?: boolean;
  isOpen: boolean;
  sessionTitleUpdate?: { id: string; title: string } | null;
  agentBusy?: boolean;
  /** Session ids currently running a parallel turn */
  busySessionIds?: string[];
  /** Sessions that finished while not selected — show grey unread-complete dot */
  unseenCompleteSessionIds?: string[];
  primarySessionId?: string | null;
  /** Session id waiting for server ack of set_primary_session */
  pendingPrimarySessionId?: string | null;
  onSetPrimarySession?: (sessionId: string) => void;
  /** Notify parent when the session list (titles) changes — used for L2 tab labels. */
  onSessionsChange?: (sessions: AgentSession[], complete?: boolean) => void;
  /** Chat layout mode: classic (Work) | solo (Code). */
  uiMode?: 'classic' | 'solo';
  onUiModeChange?: (mode: 'classic' | 'solo') => void;
  currentUser?: AccountUser;
  onOpenProfile?: () => void;
  onOpenSettings?: () => void;
}

const SIDEBAR_WIDTH_KEY = 'opensquad.sessionSidebar.width';
const SIDEBAR_WIDTH_DEFAULT = 256;
const SIDEBAR_WIDTH_MIN = 200;
const SIDEBAR_WIDTH_MAX = 480;

function loadSidebarWidth(): number {
  try {
    const raw = localStorage.getItem(SIDEBAR_WIDTH_KEY);
    if (!raw) return SIDEBAR_WIDTH_DEFAULT;
    const n = Number(raw);
    if (!Number.isFinite(n)) return SIDEBAR_WIDTH_DEFAULT;
    return Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, Math.round(n)));
  } catch {
    return SIDEBAR_WIDTH_DEFAULT;
  }
}

function belongsToWorkspace(
  meta: SessionProjectMeta | undefined,
  workspaceRootPath: string | null,
  workspaceId: string | null,
): boolean {
  if (!workspaceRootPath && !workspaceId) return true;
  if (workspaceId && meta?.workspaceId && meta.workspaceId === workspaceId) return true;
  const p = (meta?.projectPath || '').trim();
  if (!workspaceRootPath) return !p;
  if (!p) return false;
  return pathsEqual(p, workspaceRootPath);
}

function sessionsListEqual(a: AgentSession[], b: AgentSession[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (
      x.id !== y.id
      || x.title !== y.title
      || x.current !== y.current
      || x.primary !== y.primary
      || x.last_updated !== y.last_updated
      || x.created_at !== y.created_at
      || x.origin !== y.origin
    ) {
      return false;
    }
  }
  return true;
}

/** Must live outside SessionSidebar — an inner component remounts on every parent
 *  render and restarts .os-interactive hover transitions (row background flicker). */
const SidebarSection: React.FC<{
  title: string;
  count: number;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}> = ({ title, count, open, onToggle, children }) => (
  <div className="mb-1">
    {/* The header is a theme-tinted card (see `.os-group-header`) and is wrapped
        in `px-1.5` instead of using `w-full` + `mx-1.5`: the wrapper keeps the
        card aligned with the session rows, which use `mx-1.5` themselves. */}
    <div className="px-1.5">
      <button
        type="button"
        aria-expanded={open}
        className="os-group-header w-full flex items-center gap-1 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-textMuted hover:text-textMain"
        onClick={onToggle}
      >
        <FoldChevron open={open} />
        <span className="flex-1 text-left">{title}</span>
        <span className="tabular-nums opacity-70">{count}</span>
      </button>
    </div>
    {/* Shared fold primitive: an animatable grid row so opening/closing eases
        like the left/right rails. Rows stay mounted on purpose — unmounting
        would make it a snap, and it would remount on every expand. */}
    <Collapse open={open}>{children}</Collapse>
  </div>
);

const SessionSidebarInner: React.FC<SessionSidebarProps> = ({
  agentId,
  currentSessionId,
  workspaceRootPath,
  workspaceId,
  onViewSession,
  onNewSession,
  onDeleteSession,
  onOpenSkills,
  onOpenPlugins,
  onOpenRoles,
  onOpenScheduledTasks,
  onOpenTasks,
  onOpenSearch,
  skillsActive = false,
  pluginsActive = false,
  rolesActive = false,
  isOpen,
  sessionTitleUpdate,
  agentBusy = false,
  busySessionIds = [],
  unseenCompleteSessionIds = [],
  primarySessionId = null,
  pendingPrimarySessionId = null,
  onSetPrimarySession,
  onSessionsChange,
  uiMode = 'classic',
  onUiModeChange,
  currentUser = null,
  onOpenProfile,
  onOpenSettings,
}) => {
  const { t, i18n } = useTranslation();
  const ageLocale: 'zh' | 'en' = i18n.language?.startsWith('zh') ? 'zh' : 'en';
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [metaMap, setMetaMap] = useState<Record<string, SessionProjectMeta>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);
  const [confirmingBatchDelete, setConfirmingBatchDelete] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const [renaming, setRenaming] = useState(false);
  // 批量标记模式：双击进入，行首出现空心圆点，点击变灰实心标记，
  // 对已标记会话点归档/删除时批量作用于全部标记会话。
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [sectionOpen, setSectionOpen] = useState({
    pinned: true,
    comms: true,
    recent: true,
    archive: true,
  });
  const [sidebarWidth, setSidebarWidth] = useState(loadSidebarWidth);
  const editInputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const sessionsRef = React.useRef(sessions);
  sessionsRef.current = sessions;
  /** Current agent for in-flight callbacks, which otherwise only see the
   *  agentId captured when their closure was created. */
  const agentIdRef = useRef(agentId);
  agentIdRef.current = agentId;
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const prefetchInflightRef = useRef(new Set<string>());
  /** Server rows consumed so far — the offset for the next page. A ref, not
   *  state: `offset` in loadMoreSessions' dep array changed its identity on
   *  every page load, which recreated the scroll handler mid-scroll. */
  const loadedRowsRef = useRef(0);
  /** Page fetch in flight — stops a refresh and a load-more from interleaving
   *  and advancing the offset twice for the same rows. */
  const fetchingRef = useRef(false);
  /** Latest refresh wins: several triggers (interval / focus / visibility / WS
   *  events / post-switch timers) overlap easily, and a slow older response
   *  landing last would rewrite the list and the cursor out of order. */
  const refreshSeqRef = useRef(0);

  /** Warm timeline cache on hover so opening a session paints without「加载中」. */
  const prefetchSessionTimeline = useCallback(
    (sessionId: string) => {
      const sid = (sessionId || '').trim();
      if (!agentId || !sid) return;
      if (getCachedSessionTimeline(agentId, sid)?.length) return;
      if (prefetchInflightRef.current.has(sid)) return;
      prefetchInflightRef.current.add(sid);
      void (async () => {
        try {
          const resp = await agentSessionAPI.getSessionHistoryPaged(
            agentId,
            sid,
            0,
            SESSION_HISTORY_PAGE_SIZE,
          );
          const session = resp.session;
          if (!session) return;
          const entries = buildTimelineFromSession(
            session.messages || [],
            session.events || [],
            session.archived_messages,
            session.archived_events,
          );
          if (entries.length === 0) return;
          putCachedSessionTimeline(agentId, sid, entries, {
            complete: !(session.has_more ?? false),
            messageCount: session.messages?.length || 0,
            totalMessages: session.total_messages,
          });
        } catch {
          /* ignore prefetch errors */
        } finally {
          prefetchInflightRef.current.delete(sid);
        }
      })();
    },
    [agentId],
  );

  const reloadMeta = useCallback(() => {
    setMetaMap((prev) => {
      const next = loadSessionProjectMeta(agentId);
      const prevKeys = Object.keys(prev);
      const nextKeys = Object.keys(next);
      if (prevKeys.length === nextKeys.length) {
        let same = true;
        for (const k of nextKeys) {
          const a = prev[k];
          const b = next[k];
          if (
            !a
            || a.pinned !== b.pinned
            || a.archived !== b.archived
            || a.projectPath !== b.projectPath
            || a.workspaceId !== b.workspaceId
          ) {
            same = false;
            break;
          }
        }
        if (same) return prev;
      }
      return next;
    });
  }, [agentId]);

  const loadSessions = useCallback(async (opts?: { silent?: boolean }): Promise<AgentSession[]> => {
    if (!agentId) return [];
    if (!opts?.silent) {
      setLoading(true);
      setError(null);
    }
    const seq = (refreshSeqRef.current += 1);
    try {
      // Re-read the window the user has already scrolled into, not just page 1.
      // Overwriting a long list with page 1 shortens the scroll area, puts the
      // bottom sentinel back in view and starts a load/refresh loop — the list
      // reads as stuck. A window-sized read is idempotent for the visible rows.
      const resp = await agentSessionAPI.getSessionList(agentId, 0, refreshLimit(loadedRowsRef.current));
      if (seq !== refreshSeqRef.current) return sessionsRef.current;
      const raw = resp.sessions || [];
      const list = raw.filter((s) => s.origin !== 'scheduled_task');
      // Never let a refresh shorten the rendered list (see mergeRefreshedPrefix).
      setSessions((prev) => {
        const next = mergeRefreshedPrefix(prev, list);
        return sessionsListEqual(prev, next) ? prev : next;
      });
      // Advance only: a load-more may have completed during the await, and
      // rewinding the cursor to raw.length would re-request those rows.
      loadedRowsRef.current = Math.max(loadedRowsRef.current, raw.length);
      setHasMore(!!resp.has_more);
      reloadMeta();
      return list;
    } catch (err: any) {
      if (!opts?.silent) {
        setError(err.message || t('aiChat.sessionSidebar.loadSessionsFailed'));
      }
      return sessionsRef.current;
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }, [agentId, reloadMeta, t]);

  const loadMoreSessions = useCallback(async () => {
    if (!agentId || fetchingRef.current || !hasMore) return;
    const forAgent = agentId;
    fetchingRef.current = true;
    setLoadingMore(true);
    try {
      // `loadedRowsRef` counts server rows, not rendered rows: hidden origins
      // are dropped below, so a filtered length would under-count the offset.
      const resp = await agentSessionAPI.getSessionList(
        agentId,
        loadedRowsRef.current,
        SESSION_LIST_PAGE_SIZE,
      );
      // Switched agents mid-flight — the cursor and list were reset for the new
      // agent, so this page must not be appended nor the cursor advanced.
      if (agentIdRef.current !== forAgent) return;
      const raw = resp.sessions || [];
      const more = raw.filter((s) => s.origin !== 'scheduled_task');
      // Functional update: a refresh landing mid-flight must not be clobbered,
      // and appending is order-independent.
      setSessions((prev) => appendSessionPage(prev, more));
      loadedRowsRef.current = advanceLoadedRows(loadedRowsRef.current, raw.length);
      setHasMore(!!resp.has_more);
    } catch {
      if (agentIdRef.current === forAgent) setHasMore(false);
    } finally {
      if (agentIdRef.current === forAgent) {
        fetchingRef.current = false;
        setLoadingMore(false);
      }
    }
  }, [agentId, hasMore]);

  const handleListScroll = useCallback(() => {
    const el = listRef.current;
    if (!el || fetchingRef.current || !hasMore) return;
    if (
      isNearListEnd({
        scrollTop: el.scrollTop,
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
      })
    ) {
      void loadMoreSessions();
    }
  }, [loadMoreSessions, hasMore]);

  // A different agent is a different list: drop the rendered window and the
  // paging cursor together. Leaving them would (a) resume mid-list and (b) let
  // mergeRefreshedPrefix carry the previous agent's rows over as an unmatched
  // tail. Declared before the load effect below so the cursor is already 0 when
  // it reads refreshLimit(). Bumping the refresh sequence also orphans any
  // response still in flight for the previous agent, so it cannot write back.
  useEffect(() => {
    refreshSeqRef.current += 1;
    loadedRowsRef.current = 0;
    fetchingRef.current = false;
    setSessions((prev) => (prev.length ? [] : prev));
    setHasMore(false);
  }, [agentId]);


  useEffect(() => {
    if (isOpen) void loadSessions({ silent: false });
  }, [isOpen, loadSessions, workspaceRootPath, workspaceId]);

  // Warm the timeline cache for the newest sessions as soon as the list
  // renders — not just on hover. Clicking a session that was never hovered
  // then paints from cache instantly instead of a spinner. Throttled so a
  // burst never saturates the connection pool (current/list stay fast).
  useEffect(() => {
    if (!isOpen || !agentId) return;
    const targets = sessions
      .filter((s) => s.id && s.id !== currentSessionId && !getCachedSessionTimeline(agentId, s.id)?.length)
      .slice(0, 6);
    if (!targets.length) return;
    let cancelled = false;
    void (async () => {
      for (const s of targets) {
        if (cancelled) return;
        prefetchSessionTimeline(s.id);
        await new Promise((r) => window.setTimeout(r, 150));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isOpen, agentId, sessions, currentSessionId, prefetchSessionTimeline]);

  // Silent keep-alive refresh — no manual button; reconnect/list changes stay fresh.
  // Real-time updates arrive via SESSION_LIST_REFRESH_EVENT (WS session_list /
  // history_sync / current_session) — see the listener below — so this interval
  // is only a slow fallback for missed events / reconnects (was 6s → 30s).
  useEffect(() => {
    if (!isOpen || !agentId) return;
    const tick = () => void loadSessions({ silent: true });
    const id = window.setInterval(tick, 30000);
    const onFocus = () => tick();
    const onVis = () => {
      if (document.visibilityState === 'visible') tick();
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVis);
    return () => {
      window.clearInterval(id);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [isOpen, agentId, loadSessions]);

  useEffect(() => {
    if (!isOpen) {
      setConfirmingDeleteId(null);
      setConfirmingBatchDelete(false);
      setEditingId(null);
      setSelectMode(false);
      setSelectedIds(new Set());
    }
  }, [isOpen]);

  // Esc 退出批量标记模式
  useEffect(() => {
    if (!selectMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      setSelectMode(false);
      setSelectedIds(new Set());
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selectMode]);

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const exitSelectMode = useCallback(() => {
    setSelectMode(false);
    setConfirmingBatchDelete(false);
    setSelectedIds(new Set());
  }, []);

  const { mounted: softMounted, visible: softVisible } = useSoftPresence(isOpen, SOFT_PRESENCE_MS);
  const [railToggling, setRailToggling] = useState(false);

  useEffect(() => {
    setRailToggling(true);
    const t = window.setTimeout(() => setRailToggling(false), SOFT_PRESENCE_MS);
    return () => window.clearTimeout(t);
  }, [isOpen]);

  useEffect(() => {
    if (editingId && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingId]);

  const onResizePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      dragRef.current = { startX: e.clientX, startWidth: sidebarWidth };
      e.currentTarget.setPointerCapture(e.pointerId);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    [sidebarWidth],
  );

  const onResizePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    setSidebarWidth(
      Math.min(
        SIDEBAR_WIDTH_MAX,
        Math.max(SIDEBAR_WIDTH_MIN, Math.round(dragRef.current.startWidth + (e.clientX - dragRef.current.startX))),
      ),
    );
  }, []);

  const onResizePointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* */
    }
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    setSidebarWidth((w) => {
      try {
        localStorage.setItem(SIDEBAR_WIDTH_KEY, String(w));
      } catch {
        /* */
      }
      return w;
    });
  }, []);

  useEffect(() => {
    if (!isOpen || !currentSessionId) return;
    setSessions((prev) => {
      if (prev.some((s) => s.id === currentSessionId)) {
        return prev.map((s) =>
          s.id === currentSessionId ? { ...s, current: true } : { ...s, current: false },
        );
      }
      const nowIso = new Date().toISOString();
      return [
        {
          id: currentSessionId,
          title: currentSessionId,
          preview: '',
          current: true,
          created_at: nowIso,
          last_updated: nowIso,
        },
        ...prev.map((s) => ({ ...s, current: false })),
      ];
    });
    const t1 = window.setTimeout(() => void loadSessions({ silent: true }), 200);
    const t2 = window.setTimeout(() => void loadSessions({ silent: true }), 900);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, [isOpen, currentSessionId, loadSessions]);

  useEffect(() => {
    if (!sessionTitleUpdate) return;
    setSessions((prev) =>
      prev.map((s) =>
        s.id === sessionTitleUpdate.id ? { ...s, title: sessionTitleUpdate.title } : s,
      ),
    );
  }, [sessionTitleUpdate]);

  useEffect(() => {
    onSessionsChange?.(sessions, !hasMore);
  }, [sessions, hasMore, onSessionsChange]);

  useEffect(() => {
    const onMeta = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.agentId !== agentId) return;
      void loadSessions({ silent: true });
    };
    window.addEventListener(SESSION_META_EVENT, onMeta);
    window.addEventListener(SESSION_LIST_REFRESH_EVENT, onMeta);
    return () => {
      window.removeEventListener(SESSION_META_EVENT, onMeta);
      window.removeEventListener(SESSION_LIST_REFRESH_EVENT, onMeta);
    };
  }, [agentId, loadSessions]);

  const commsId = useMemo(
    () => resolveCommsSessionId(sessions, primarySessionId, pendingPrimarySessionId),
    [sessions, primarySessionId, pendingPrimarySessionId],
  );

  const filtered = useMemo(() => {
    const inWorkspace = sessions.filter((s) =>
      belongsToWorkspace(metaMap[s.id], workspaceRootPath, workspaceId),
    );
    return withCommsSession(inWorkspace, sessions, commsId);
  }, [sessions, metaMap, workspaceRootPath, workspaceId, commsId]);

  useEffect(() => {
    const pid = (primarySessionId || '').trim();
    if (!pid) return;
    setSessions((prev) => {
      let changed = false;
      const next = prev.map((s) => {
        const primary = s.id === pid;
        if (!!s.primary === primary) return s;
        changed = true;
        return { ...s, primary };
      });
      return changed ? next : prev;
    });
  }, [primarySessionId]);

  const sections = useMemo(
    () => groupSessionsForSidebar(filtered, metaMap, commsId),
    [filtered, metaMap, commsId],
  );

  const handleDeleteConfirm = async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    setConfirmingDeleteId(null);
    // 批量：确认删除的是已标记会话时，作用于全部标记会话。
    const ids = selectedIds.has(sessionId) ? [...selectedIds] : [sessionId];
    await runBatchDelete(ids);
  };

  /** 批量删除/单删共用：删除后从列表移除并退出批量模式。 */
  const runBatchDelete = async (ids: string[]) => {
    let failed = false;
    for (const id of ids) {
      try {
        if (onDeleteSession) {
          await onDeleteSession(id);
        } else {
          await agentSessionAPI.deleteSession(agentId, id);
        }
      } catch {
        failed = true;
      }
    }
    setSessions((prev) => prev.filter((s) => !ids.includes(s.id)));
    if (failed) {
      setError(t('aiChat.sessionSidebar.deleteSessionFailed'));
      void loadSessions({ silent: true });
    }
    exitSelectMode();
  };

  const startRename = (e: React.MouseEvent, session: AgentSession) => {
    e.stopPropagation();
    setEditingId(session.id);
    setEditingTitle(session.title || '');
  };

  const commitRename = async () => {
    if (!editingId || renaming) return;
    const title = editingTitle.trim();
    if (!title) {
      setEditingId(null);
      return;
    }
    setRenaming(true);
    try {
      await agentSessionAPI.renameSession(agentId, editingId, title);
      setSessions((prev) => prev.map((s) => (s.id === editingId ? { ...s, title } : s)));
      setEditingId(null);
    } catch (err: any) {
      setError(err.message || 'Rename failed');
    } finally {
      setRenaming(false);
    }
  };

  const renderRow = (session: AgentSession, rowKey: string) => {
    // Highlight follows UI selection only — never backend session.current (stale "live" flag).
    const isCurrent = !!currentSessionId && session.id === currentSessionId;
    const isPrimary = !!commsId && session.id === commsId;
    const isPendingPrimary = !!pendingPrimarySessionId && session.id === pendingPrimarySessionId;
    const meta = metaMap[session.id];
    const pinned = !!meta?.pinned;
    const archived = !!meta?.archived;
    const busy = busySessionIds.includes(session.id) || (!!agentBusy && !!session.current);
    const unseenComplete = !busy && !isCurrent && unseenCompleteSessionIds.includes(session.id);
    const confirming = confirmingDeleteId === session.id;
    const editing = editingId === session.id;
    const marked = selectMode && selectedIds.has(session.id);

    return (
      <div
        key={rowKey}
        title={session.title || session.id}
        className={`group os-interactive flex items-center gap-1 mx-1.5 px-2.5 py-1.5 rounded-xl cursor-pointer text-[12px] text-textMain ${
          isCurrent ? 'is-active' : ''
        } ${marked ? 'bg-black/[0.04] dark:bg-white/[0.06]' : ''}`}
        onClick={(e) => {
          if (editing || confirming) return;
          if (selectMode) {
            toggleSelect(session.id);
            return;
          }
          // 双击的第二下 click 不触发打开（交给 onDoubleClick 进入批量模式）
          if (e.detail > 1) return;
          onViewSession(session.id);
        }}
        onMouseEnter={() => prefetchSessionTimeline(session.id)}
        onDoubleClick={(e) => {
          e.stopPropagation();
          if (selectMode) exitSelectMode();
          else setSelectMode(true);
        }}
      >
        {busy ? (
          <PulseDotsOrbit size={14} className="shrink-0" />
        ) : selectMode ? (
          <button
            type="button"
            aria-label={t('aiChat.sessionSidebar.markSession')}
            title={t('aiChat.sessionSidebar.markSession')}
            className="w-3 h-3 rounded-full shrink-0 flex items-center justify-center bg-black/[0.07] dark:bg-white/[0.09] shadow-[inset_1px_1px_2px_rgba(0,0,0,0.16),inset_-1px_-1px_1px_rgba(255,255,255,0.75)] dark:shadow-[inset_1px_1px_2px_rgba(0,0,0,0.6),inset_-1px_-1px_1px_rgba(255,255,255,0.07)] transition-shadow"
            onClick={(e) => {
              e.stopPropagation();
              toggleSelect(session.id);
            }}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full transition-all ${
                marked
                  ? 'bg-primary shadow-[0.5px_0.5px_1px_rgba(0,0,0,0.45)] dark:shadow-[0.5px_0.5px_1px_rgba(0,0,0,0.8)]'
                  : 'bg-transparent group-hover:bg-primary/25'
              }`}
            />
          </button>
        ) : (
          <span
            className={`w-1.5 h-1.5 rounded-full shrink-0 ${
              unseenComplete
                ? 'bg-textMuted'
                : 'bg-transparent border border-border'
            }`}
            title={unseenComplete ? t('aiChat.taskCompleted') : undefined}
          />
        )}
        <div className="min-w-0 flex-1">
          {editing ? (
            <input
              ref={editInputRef}
              value={editingTitle}
              onChange={(e) => setEditingTitle(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void commitRename();
                if (e.key === 'Escape') setEditingId(null);
              }}
              onBlur={() => void commitRename()}
              className="w-full px-1 py-0.5 text-[12px] rounded border border-primary/40 bg-bgLight outline-none"
              disabled={renaming}
            />
          ) : (
            <>
              <div className="truncate font-normal flex items-center gap-1">
                <span className="truncate">{session.title || session.id}</span>
                {isPendingPrimary ? (
                  <span
                    className="shrink-0 text-[9px] px-1 rounded bg-black/5 dark:bg-white/10 text-textMuted"
                    title={t('aiChat.sessionSidebar.setExternalPending')}
                  >
                    …
                  </span>
                ) : isPrimary ? (
                  <span
                    className="shrink-0 text-[9px] px-1 rounded bg-amber-500/20 text-amber-700 dark:text-amber-300"
                    title={t('aiChat.sessionSidebar.externalBadgeTitle')}
                  >
                    {t('aiChat.sessionSidebar.externalBadge')}
                  </span>
                ) : null}
              </div>
              <div className="truncate text-[10px] opacity-70">
                {formatRelativeAge(session.last_updated || session.created_at, { locale: ageLocale })}
              </div>
            </>
          )}
        </div>
        {!editing && !confirming ? (
          <div className="opacity-0 group-hover:opacity-100 flex items-center gap-0.5 shrink-0">
            {!isPrimary && onSetPrimarySession ? (
              <button
                type="button"
                title={t('aiChat.sessionSidebar.setExternalSession')}
                disabled={!!pendingPrimarySessionId}
                className="p-0.5 rounded hover:bg-primary/10 disabled:opacity-40"
                onClick={(e) => {
                  e.stopPropagation();
                  if (archived) {
                    setSessionArchived(agentId, session.id, false);
                    reloadMeta();
                  }
                  onSetPrimarySession(session.id);
                }}
              >
                <Sparkles size={12} />
              </button>
            ) : null}
            <button
              type="button"
              className="p-0.5 rounded hover:bg-primary/15"
              title={pinned ? t('aiChat.unpin') : t('aiChat.sessionSidebar.pinned')}
              onClick={(e) => {
                e.stopPropagation();
                setSessionPinned(agentId, session.id, !pinned);
                reloadMeta();
              }}
            >
              {pinned ? <PinOff size={11} /> : <Pin size={11} />}
            </button>
            <button
              type="button"
              className="p-0.5 rounded hover:bg-primary/15"
              title={archived ? t('aiChat.unarchive') : t('aiChat.archive')}
              onClick={(e) => {
                e.stopPropagation();
                // 批量：操作的是已标记会话时，作用于全部标记会话。
                const ids = selectedIds.has(session.id) ? [...selectedIds] : [session.id];
                for (const id of ids) setSessionArchived(agentId, id, !archived);
                reloadMeta();
                if (ids.length > 1) exitSelectMode();
              }}
            >
              <Archive size={11} />
            </button>
            <button
              type="button"
              className="p-0.5 rounded hover:bg-primary/15"
              title={t('aiChat.sessionSidebar.rename')}
              onClick={(e) => startRename(e, session)}
            >
              <Pencil size={11} />
            </button>
            <button
              type="button"
              className="p-0.5 rounded hover:bg-rose-500/20 text-rose-500"
              title={t('common.delete')}
              onClick={(e) => {
                e.stopPropagation();
                setConfirmingDeleteId(session.id);
              }}
            >
              <Trash2 size={11} />
            </button>
          </div>
        ) : null}
        {confirming ? (
          <div className="flex items-center gap-0.5 shrink-0" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="p-0.5 rounded bg-rose-500 text-white"
              onClick={(e) => void handleDeleteConfirm(e, session.id)}
            >
              <Check size={11} />
            </button>
            <button
              type="button"
              className="p-0.5 rounded hover:bg-primary/15"
              onClick={(e) => {
                e.stopPropagation();
                setConfirmingDeleteId(null);
              }}
            >
              <X size={11} />
            </button>
          </div>
        ) : null}
      </div>
    );
  };

  if (!softMounted) return null;

  return (
    <div
      className={`os-soft-rail ${softVisible ? 'is-open' : ''} ${railToggling ? 'is-toggling' : ''}`}
      style={{ width: softVisible ? sidebarWidth : 0 }}
      aria-hidden={!softVisible}
    >
    <div
      className="relative h-full flex flex-col os-depth-card os-soft-rail-inner"
      style={{ width: sidebarWidth }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        onPointerDown={onResizePointerDown}
        onPointerMove={onResizePointerMove}
        onPointerUp={onResizePointerUp}
        className="absolute right-0 top-0 bottom-0 w-1.5 translate-x-1/2 cursor-col-resize z-10 hover:bg-primary/30"
      />
      <div className="h-11 px-2 border-b border-border box-border flex items-center shrink-0">
        <div
          className="flex min-w-0 flex-1 items-center rounded-xl bg-black/[0.055] p-[3px] dark:bg-white/[0.08]"
          role="tablist"
          aria-label={t('aiChat.uiModeLabel')}
        >
          <button
            type="button"
            role="tab"
            aria-selected={uiMode === 'classic'}
            onClick={() => onUiModeChange?.('classic')}
            title={t('aiChat.uiModeClassicHint')}
            className={`flex min-w-0 flex-1 items-center justify-center gap-1 rounded-[9px] px-1.5 py-[5px] text-[11px] font-medium transition-all duration-150 ${
              uiMode === 'classic'
                ? 'bg-white text-textMain shadow-[0_1px_2px_rgba(0,0,0,0.08)] dark:bg-panel dark:shadow-[0_1px_2px_rgba(0,0,0,0.35)]'
                : 'text-textMuted hover:text-textMain'
            }`}
          >
            {uiMode === 'classic' ? (
              <BookOpen size={13} strokeWidth={1.75} className="shrink-0 opacity-80" />
            ) : null}
            <span className="truncate">{t('aiChat.uiModeClassic')}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={uiMode === 'solo'}
            onClick={() => onUiModeChange?.('solo')}
            title={t('aiChat.uiModeSoloHint')}
            className={`flex min-w-0 flex-1 items-center justify-center gap-1 rounded-[9px] px-1.5 py-[5px] text-[11px] font-medium transition-all duration-150 ${
              uiMode === 'solo'
                ? 'bg-white text-textMain shadow-[0_1px_2px_rgba(0,0,0,0.08)] dark:bg-panel dark:shadow-[0_1px_2px_rgba(0,0,0,0.35)]'
                : 'text-textMuted hover:text-textMain'
            }`}
          >
            {uiMode === 'solo' ? (
              <Code2 size={13} strokeWidth={1.75} className="shrink-0 opacity-80" />
            ) : null}
            <span className="truncate">{t('aiChat.uiModeSolo')}</span>
          </button>
        </div>
      </div>

      <div className="px-2 py-2 space-y-1 border-b border-border/60 shrink-0">
        <button
          type="button"
          disabled={!workspaceRootPath}
          onClick={() => onNewSession(workspaceRootPath || undefined)}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[14px] font-normal text-textMain os-interactive disabled:opacity-40"
        >
          <MessageSquarePlus size={16} className="text-textMuted/70" />
          {t('aiChat.newChat')}
        </button>
        <button
          type="button"
          onClick={() => onOpenSearch?.()}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[14px] font-normal text-textMain os-interactive"
        >
          <Search size={16} className="text-textMuted/70" />
          <span className="flex-1 text-left">{t('aiChat.search.title')}</span>
        </button>
        <button
          type="button"
          disabled={!workspaceRootPath}
          onClick={() => onOpenScheduledTasks?.()}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[14px] font-normal text-textMain os-interactive disabled:opacity-40"
        >
          <Clock size={16} className="text-textMuted/70" />
          {t('aiChat.scheduledTasks')}
        </button>
        <button
          type="button"
          disabled={!workspaceRootPath}
          onClick={() => onOpenTasks?.()}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[14px] font-normal text-textMain os-interactive disabled:opacity-40"
        >
          <ListTodo size={16} className="text-textMuted/70" />
          {t('taskPanel.title')}
        </button>
        <button
          type="button"
          onClick={() => onOpenSkills?.()}
          className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[14px] font-normal os-interactive ${
            skillsActive
              ? 'bg-primary/10 text-primary'
              : 'text-textMain'
          }`}
        >
          <Sparkles size={16} className={skillsActive ? 'text-primary' : 'text-textMuted/70'} />
          {t('nav.skills')}
        </button>
        <button
          type="button"
          onClick={() => onOpenPlugins?.()}
          className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[14px] font-normal os-interactive ${
            pluginsActive
              ? 'bg-primary/10 text-primary'
              : 'text-textMain'
          }`}
        >
          <Puzzle size={16} className={pluginsActive ? 'text-primary' : 'text-textMuted/70'} />
          {t('nav.plugins')}
        </button>
        <button
          type="button"
          onClick={() => onOpenRoles?.()}
          className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[14px] font-normal os-interactive ${
            rolesActive
              ? 'bg-primary/10 text-primary'
              : 'text-textMain'
          }`}
        >
          <UserCircle size={16} className={rolesActive ? 'text-primary' : 'text-textMuted/70'} />
          {t('nav.roles')}
        </button>
      </div>

      {selectMode ? (
        <div className="px-3 py-1.5 border-b border-border/60 flex items-center justify-between text-[11px] text-textMuted shrink-0 gap-2">
          {confirmingBatchDelete ? (
            <>
              <span className="text-rose-500 truncate">
                {t('aiChat.sessionSidebar.batchDeleteConfirm', { count: selectedIds.size })}
              </span>
              <div className="flex items-center gap-1 shrink-0">
                <button
                  type="button"
                  className="p-1 rounded bg-rose-500 text-white"
                  title={t('common.confirm')}
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmingBatchDelete(false);
                    void runBatchDelete([...selectedIds]);
                  }}
                >
                  <Check size={11} />
                </button>
                <button
                  type="button"
                  className="p-1 rounded hover:bg-primary/15"
                  title={t('common.cancel')}
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmingBatchDelete(false);
                  }}
                >
                  <X size={11} />
                </button>
              </div>
            </>
          ) : (
            <>
              <span className="truncate">
                {t('aiChat.sessionSidebar.batchSelected', { count: selectedIds.size })}
              </span>
              <div className="flex items-center gap-1 shrink-0">
                <button
                  type="button"
                  disabled={selectedIds.size === 0}
                  className="flex items-center gap-0.5 px-1.5 py-0.5 rounded hover:bg-primary/10 disabled:opacity-40"
                  title={t('aiChat.sessionSidebar.batchArchive')}
                  onClick={(e) => {
                    e.stopPropagation();
                    const ids = [...selectedIds];
                    if (!ids.length) return;
                    for (const id of ids) setSessionArchived(agentId, id, true);
                    reloadMeta();
                    exitSelectMode();
                  }}
                >
                  <Archive size={11} />
                  <span>{t('aiChat.sessionSidebar.batchArchive')}</span>
                </button>
                <button
                  type="button"
                  disabled={selectedIds.size === 0}
                  className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-rose-500 hover:bg-rose-500/15 disabled:opacity-40"
                  title={t('common.delete')}
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmingBatchDelete(true);
                  }}
                >
                  <Trash2 size={11} />
                  <span>{t('common.delete')}</span>
                </button>
                <button
                  type="button"
                  className="hover:text-textMain"
                  onClick={exitSelectMode}
                >
                  {t('aiChat.sessionSidebar.batchExit')}
                </button>
              </div>
            </>
          )}
        </div>
      ) : null}

      <div
        ref={listRef}
        onScroll={handleListScroll}
        className="flex-1 min-h-0 overflow-y-auto os-depth-nest os-depth-nest--flush"
      >
        {!workspaceRootPath ? (
          <div className="px-3 py-4 text-[11px] text-textMuted/70">{t('aiChat.needWorkspaceFirst')}</div>
        ) : error ? (
          <div className="px-3 py-2 text-[11px] text-rose-500">{error}</div>
        ) : (
          <>
            <SidebarSection
              title={t('aiChat.sessionSidebar.pinned')}
              count={sections.pinned.length}
              open={sectionOpen.pinned}
              onToggle={() => setSectionOpen((s) => ({ ...s, pinned: !s.pinned }))}
            >
              {sections.pinned.length === 0 ? (
                <div className="px-3 py-1 text-[10px] text-textMuted/50">{t('aiChat.none')}</div>
              ) : (
                sections.pinned.map((s) => renderRow(s, `pinned:${s.id}`))
              )}
            </SidebarSection>
            <SidebarSection
              title={t('aiChat.sessionSidebar.comms')}
              count={sections.comms.length}
              open={sectionOpen.comms}
              onToggle={() => setSectionOpen((s) => ({ ...s, comms: !s.comms }))}
            >
              {sections.comms.length === 0 ? (
                <div className="px-3 py-1 text-[10px] text-textMuted/50">
                  {t('aiChat.sessionSidebar.noCommsSession')}
                </div>
              ) : (
                sections.comms.map((s) => renderRow(s, `comms:${s.id}`))
              )}
            </SidebarSection>
            <SidebarSection
              title={t('aiChat.recent')}
              count={sections.recent.length}
              open={sectionOpen.recent}
              onToggle={() => setSectionOpen((s) => ({ ...s, recent: !s.recent }))}
            >
              {sections.recent.length === 0 ? (
                <div className="px-3 py-1 text-[10px] text-textMuted/50">{t('aiChat.none')}</div>
              ) : (
                sections.recent.map((s) => renderRow(s, `recent:${s.id}`))
              )}
            </SidebarSection>
            <SidebarSection
              title={t('aiChat.archive')}
              count={sections.archive.length}
              open={sectionOpen.archive}
              onToggle={() => setSectionOpen((s) => ({ ...s, archive: !s.archive }))}
            >
              {sections.archive.length === 0 ? (
                <div className="px-3 py-1 text-[10px] text-textMuted/50">{t('aiChat.none')}</div>
              ) : (
                sections.archive.map((s) => renderRow(s, `archive:${s.id}`))
              )}
            </SidebarSection>
            {hasMore ? (
              <div className="px-3 py-2">
                <button
                  type="button"
                  disabled={loadingMore}
                  onClick={() => void loadMoreSessions()}
                  className="w-full rounded-lg px-2 py-1.5 text-[11px] text-textMuted hover:text-textMain hover:bg-black/5 dark:hover:bg-white/10 disabled:opacity-50"
                >
                  {loadingMore ? (
                    <span className="inline-flex items-center gap-1.5"><OpenSquadLoader size={16} /></span>
                  ) : (
                    t('aiChat.loadMoreSessions')
                  )}
                </button>
              </div>
            ) : null}
          </>
        )}
      </div>

      {(onOpenProfile || onOpenSettings) && (
        <AccountRailFooter
          currentUser={currentUser}
          onOpenProfile={() => onOpenProfile?.()}
          onOpenSettings={() => onOpenSettings?.()}
          shortcuts={<AgentNavShortcutAvatars />}
          actions={
            <>
              <button
                type="button"
                onClick={() => navigateAppView('chat')}
                className="rounded-lg p-1.5 text-textMuted hover:bg-primary/10 hover:text-textMain"
                title={t('nav.chats')}
                aria-label={t('nav.chats')}
              >
                <MessageCircle size={16} strokeWidth={1.75} />
              </button>
              <button
                type="button"
                onClick={() => navigateAppView('admin')}
                className="rounded-lg p-1.5 text-textMuted hover:bg-primary/10 hover:text-textMain"
                title={t('nav.agents')}
                aria-label={t('nav.agents')}
              >
                <LayoutGrid size={16} strokeWidth={1.75} />
              </button>
            </>
          }
        />
      )}
    </div>
    </div>
  );
};

export const SessionSidebar = React.memo(SessionSidebarInner);
