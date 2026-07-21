/**
 * SessionSidebar — sessions for the active workspace, grouped by 置顶 / 最近 / 归档.
 */
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Trash2,
  Check,
  X,
  Pin,
  PinOff,
  Pencil,
  MessageSquarePlus,
  Sparkles,
  Archive,
  ChevronDown,
  ChevronRight,
  BookOpen,
  Code2,
  MessageCircle,
  LayoutGrid,
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
import { pathsEqual } from '../../utils/workspaceStore';
import { SOFT_PRESENCE_MS, useSoftPresence } from '../../utils/useSoftPresence';
import { formatRelativeAge } from '../../utils/time';
import { PulseDotsOrbit } from './PulseDotsStatus';
import { AccountRailFooter, type AccountUser } from '../AccountRailFooter';
import { navigateAppView } from '../../utils/appNavItems';

interface SessionSidebarProps {
  agentId: string;
  currentSessionId: string | null;
  /** Active workspace absolute path — filters the list. */
  workspaceRootPath: string | null;
  workspaceId: string | null;
  onViewSession: (sessionId: string) => void;
  onNewSession: (projectPath?: string) => void;
  onSwitchAndReply: (sessionId: string) => void;
  onOpenSkills?: () => void;
  isOpen: boolean;
  sessionTitleUpdate?: { id: string; title: string } | null;
  agentBusy?: boolean;
  /** Session ids currently running a parallel turn */
  busySessionIds?: string[];
  primarySessionId?: string | null;
  onSetPrimarySession?: (sessionId: string) => void;
  /** Notify parent when the session list (titles) changes — used for L2 tab labels. */
  onSessionsChange?: (sessions: AgentSession[]) => void;
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

export const SessionSidebar: React.FC<SessionSidebarProps> = ({
  agentId,
  currentSessionId,
  workspaceRootPath,
  workspaceId,
  onViewSession,
  onNewSession,
  onSwitchAndReply,
  onOpenSkills,
  isOpen,
  sessionTitleUpdate,
  agentBusy = false,
  busySessionIds = [],
  primarySessionId = null,
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
  const [metaMap, setMetaMap] = useState<Record<string, SessionProjectMeta>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const [renaming, setRenaming] = useState(false);
  const [sectionOpen, setSectionOpen] = useState({ pinned: true, recent: true, archive: true });
  const [sidebarWidth, setSidebarWidth] = useState(loadSidebarWidth);
  const editInputRef = useRef<HTMLInputElement>(null);
  const sessionsRef = React.useRef(sessions);
  sessionsRef.current = sessions;
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const reloadMeta = useCallback(() => {
    setMetaMap(loadSessionProjectMeta(agentId));
  }, [agentId]);

  const loadSessions = useCallback(async (opts?: { silent?: boolean }): Promise<AgentSession[]> => {
    if (!agentId) return [];
    if (!opts?.silent) {
      setLoading(true);
      setError(null);
    }
    try {
      const resp = await agentSessionAPI.getSessionList(agentId);
      const list = resp.sessions || [];
      setSessions(list);
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

  useEffect(() => {
    if (isOpen) void loadSessions({ silent: false });
  }, [isOpen, loadSessions, workspaceRootPath, workspaceId]);

  // Silent keep-alive refresh — no manual button; reconnect/list changes stay fresh
  useEffect(() => {
    if (!isOpen || !agentId) return;
    const tick = () => void loadSessions({ silent: true });
    const id = window.setInterval(tick, 6000);
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
      setEditingId(null);
    }
  }, [isOpen]);

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
    onSessionsChange?.(sessions);
  }, [sessions, onSessionsChange]);

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

  const filtered = useMemo(() => {
    return sessions.filter((s) => belongsToWorkspace(metaMap[s.id], workspaceRootPath, workspaceId));
  }, [sessions, metaMap, workspaceRootPath, workspaceId]);

  const sections = useMemo(() => {
    // 置顶 / 最近 / 归档 are independent filters (not mutually exclusive):
    // - 置顶: pinned flag
    // - 最近: not archived (includes pinned sessions)
    // - 归档: archived flag (may also be pinned)
    const pinned: AgentSession[] = [];
    const recent: AgentSession[] = [];
    const archive: AgentSession[] = [];
    for (const s of filtered) {
      const m = metaMap[s.id];
      if (m?.pinned) pinned.push(s);
      if (!m?.archived) recent.push(s);
      if (m?.archived) archive.push(s);
    }
    const byUpdated = (a: AgentSession, b: AgentSession) =>
      String(b.last_updated || '').localeCompare(String(a.last_updated || ''));
    pinned.sort(byUpdated);
    recent.sort(byUpdated);
    archive.sort(byUpdated);
    return { pinned, recent, archive };
  }, [filtered, metaMap]);

  const handleDeleteConfirm = async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    setConfirmingDeleteId(null);
    try {
      await agentSessionAPI.deleteSession(agentId, sessionId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
    } catch (err: any) {
      setError(err.message || t('aiChat.sessionSidebar.deleteSessionFailed'));
    }
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
    const isCurrent = session.id === currentSessionId || !!session.current;
    const isPrimary = !!session.primary || session.id === primarySessionId;
    const meta = metaMap[session.id];
    const pinned = !!meta?.pinned;
    const archived = !!meta?.archived;
    const busy = (isCurrent && agentBusy) || busySessionIds.includes(session.id);
    const confirming = confirmingDeleteId === session.id;
    const editing = editingId === session.id;

    return (
      <div
        key={rowKey}
        className={`group os-interactive flex items-center gap-1 px-2.5 py-1.5 rounded-none cursor-pointer text-[12px] ${
          isCurrent
            ? 'is-active text-textMuted'
            : 'text-textMuted/75'
        }`}
        onClick={() => {
          if (editing || confirming) return;
          onViewSession(session.id);
        }}
        onDoubleClick={(e) => {
          e.stopPropagation();
          onSwitchAndReply(session.id);
        }}
      >
        {busy ? (
          <PulseDotsOrbit size={14} className="shrink-0" />
        ) : (
          <span
            className={`w-1.5 h-1.5 rounded-full shrink-0 ${
              isCurrent ? 'bg-emerald-500' : 'bg-transparent border border-border'
            }`}
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
              <div className="truncate font-medium flex items-center gap-1">
                <span className="truncate">{session.title || session.id}</span>
                {isPrimary ? (
                  <span
                    className="shrink-0 text-[9px] px-1 rounded bg-amber-500/20 text-amber-700 dark:text-amber-300"
                    title="外界消息（群聊/飞书/Telegram 等）默认接入此主会话"
                  >
                    主
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
                title="设为主会话（外界消息接入）"
                className="p-0.5 rounded hover:bg-primary/10"
                onClick={(e) => {
                  e.stopPropagation();
                  onSetPrimarySession(session.id);
                }}
              >
                <Sparkles size={12} />
              </button>
            ) : null}
            <button
              type="button"
              className="p-0.5 rounded hover:bg-primary/15"
              title={pinned ? '取消置顶' : '置顶'}
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
              title={archived ? '取消归档' : '归档'}
              onClick={(e) => {
                e.stopPropagation();
                setSessionArchived(agentId, session.id, !archived);
                reloadMeta();
              }}
            >
              <Archive size={11} />
            </button>
            <button
              type="button"
              className="p-0.5 rounded hover:bg-primary/15"
              title="重命名"
              onClick={(e) => startRename(e, session)}
            >
              <Pencil size={11} />
            </button>
            <button
              type="button"
              className="p-0.5 rounded hover:bg-rose-500/20 text-rose-500"
              title="删除"
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

  const Section: React.FC<{
    id: keyof typeof sectionOpen;
    title: string;
    count: number;
    children: React.ReactNode;
  }> = ({ id, title, count, children }) => (
    <div className="mb-1">
      <button
        type="button"
        className="w-full flex items-center gap-1 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-textMuted hover:text-textMain"
        onClick={() => setSectionOpen((s) => ({ ...s, [id]: !s[id] }))}
      >
        {sectionOpen[id] ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="flex-1 text-left">{title}</span>
        <span className="tabular-nums opacity-70">{count}</span>
      </button>
      {sectionOpen[id] ? children : null}
    </div>
  );

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
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[12px] font-medium text-textMuted os-interactive disabled:opacity-40"
        >
          <MessageSquarePlus size={14} className="text-sky-500" />
          新建对话
        </button>
        <button
          type="button"
          onClick={() => onOpenSkills?.()}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[12px] font-medium text-textMuted os-interactive"
        >
          <Sparkles size={14} className="text-textMuted/70" />
          Skill 库
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto os-depth-nest os-depth-nest--flush">
        {!workspaceRootPath ? (
          <div className="px-3 py-4 text-[11px] text-textMuted/70">请先打开或创建一个工作区</div>
        ) : error ? (
          <div className="px-3 py-2 text-[11px] text-rose-500">{error}</div>
        ) : (
          <>
            <Section id="pinned" title="置顶" count={sections.pinned.length}>
              {sections.pinned.length === 0 ? (
                <div className="px-3 py-1 text-[10px] text-textMuted/50">无</div>
              ) : (
                sections.pinned.map((s) => renderRow(s, `pinned:${s.id}`))
              )}
            </Section>
            <Section id="recent" title="最近" count={sections.recent.length}>
              {sections.recent.length === 0 ? (
                <div className="px-3 py-1 text-[10px] text-textMuted/50">无</div>
              ) : (
                sections.recent.map((s) => renderRow(s, `recent:${s.id}`))
              )}
            </Section>
            <Section id="archive" title="归档" count={sections.archive.length}>
              {sections.archive.length === 0 ? (
                <div className="px-3 py-1 text-[10px] text-textMuted/50">无</div>
              ) : (
                sections.archive.map((s) => renderRow(s, `archive:${s.id}`))
              )}
            </Section>
          </>
        )}
      </div>

      {(onOpenProfile || onOpenSettings) && (
        <AccountRailFooter
          currentUser={currentUser}
          onOpenProfile={() => onOpenProfile?.()}
          onOpenSettings={() => onOpenSettings?.()}
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
