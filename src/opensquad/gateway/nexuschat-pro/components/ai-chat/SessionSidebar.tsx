/**
 * SessionSidebar — Agent session list grouped by project folder (last path segment).
 * Cursor-like: folder hierarchy, status dots, inline rename, new session beside folder.
 */
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Plus, Trash2, RefreshCw, ChevronLeft, Check, X,
  Folder, Pin, PinOff, ChevronDown, ChevronRight, Pencil, MessageSquarePlus,
} from 'lucide-react';
import { agentSessionAPI, AgentSession } from '../../services/api';
import {
  loadSessionProjectMeta,
  projectFolderName,
  setSessionPinned,
  SESSION_META_EVENT,
  SESSION_LIST_REFRESH_EVENT,
  type SessionProjectMeta,
} from '../../utils/sessionProjectMeta';
import { formatRelativeAge } from '../../utils/time';

/** Classic-mode workflow spinner — violet arc (same language as AgentWorkingIndicator). */
const OpensquadWorkingDot: React.FC<{ size?: number }> = ({ size = 12 }) => {
  const rawId = React.useId().replace(/:/g, '');
  const gradId = `osq-sb-arc-${rawId}`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="shrink-0"
      aria-hidden
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop stopColor="#818cf8" />
          <stop offset="0.6" stopColor="#a78bfa" stopOpacity="0.5" />
          <stop offset="1" stopColor="#818cf8" stopOpacity="0" />
        </linearGradient>
      </defs>
      <g style={{ transformOrigin: '16px 16px', animation: 'osqSpin 1.4s linear infinite' }}>
        <circle
          cx="16"
          cy="16"
          r="13.5"
          stroke={`url(#${gradId})`}
          strokeWidth="3.5"
          strokeLinecap="round"
          strokeDasharray="53 33"
        />
      </g>
      <style>{`@keyframes osqSpin { to { transform: rotate(360deg); } }`}</style>
    </svg>
  );
};

interface SessionSidebarProps {
  agentId: string;
  currentSessionId: string | null;
  onViewSession: (sessionId: string) => void;
  /** Optional projectPath binds the new session to that folder/cwd. */
  onNewSession: (projectPath?: string) => void;
  onSwitchAndReply: (sessionId: string) => void;
  isOpen: boolean;
  onClose: () => void;
  sessionTitleUpdate?: { id: string; title: string } | null;
  /** True when the agent's current session is actively working/thinking. */
  agentBusy?: boolean;
}

const SEE_MORE_LIMIT = 5;

export const SessionSidebar: React.FC<SessionSidebarProps> = ({
  agentId,
  currentSessionId,
  onViewSession,
  onNewSession,
  onSwitchAndReply,
  isOpen,
  onClose,
  sessionTitleUpdate,
  agentBusy = false,
}) => {
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [metaMap, setMetaMap] = useState<Record<string, SessionProjectMeta>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({});
  const [showAllFolders, setShowAllFolders] = useState<Record<string, boolean>>({});
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const [renaming, setRenaming] = useState(false);
  const editInputRef = useRef<HTMLInputElement>(null);
  const sessionsRef = React.useRef(sessions);
  sessionsRef.current = sessions;

  const reloadMeta = useCallback(() => {
    setMetaMap(loadSessionProjectMeta(agentId));
  }, [agentId]);

  const loadSessions = useCallback(async (): Promise<AgentSession[]> => {
    if (!agentId) return [];
    setLoading(true);
    setError(null);
    try {
      const resp = await agentSessionAPI.getSessionList(agentId);
      const list = resp.sessions || [];
      setSessions(list);
      reloadMeta();
      return list;
    } catch (err: any) {
      console.error('[SessionSidebar] Failed to load sessions:', err);
      setError(err.message || 'Failed to load sessions');
      return sessionsRef.current;
    } finally {
      setLoading(false);
    }
  }, [agentId, reloadMeta]);

  useEffect(() => {
    if (isOpen) loadSessions();
  }, [isOpen, loadSessions]);

  useEffect(() => {
    if (!isOpen) {
      setConfirmingDeleteId(null);
      setEditingId(null);
    }
  }, [isOpen]);

  useEffect(() => {
    if (editingId && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingId]);

  // New / switched session id — always re-fetch so the list stays in sync
  // without requiring a manual Refresh click.
  useEffect(() => {
    if (!isOpen || !currentSessionId) return;
    // Optimistic row so the user sees the new session immediately.
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
    const t = window.setTimeout(() => {
      void loadSessions();
    }, 200);
    const t2 = window.setTimeout(() => {
      void loadSessions();
    }, 900);
    return () => {
      window.clearTimeout(t);
      window.clearTimeout(t2);
    };
  }, [isOpen, currentSessionId, loadSessions]);

  useEffect(() => {
    if (!sessionTitleUpdate) return;
    setSessions((prev) => {
      const exists = prev.some((session) => session.id === sessionTitleUpdate.id);
      if (!exists) {
        void loadSessions();
        return prev;
      }
      return prev.map((session) =>
        session.id === sessionTitleUpdate.id
          ? { ...session, title: sessionTitleUpdate.title }
          : session,
      );
    });
  }, [sessionTitleUpdate, loadSessions]);

  useEffect(() => {
    let retryTimer: number | undefined;
    const onMeta = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.agentId !== agentId) return;
      const sid = typeof detail?.sessionId === 'string' ? detail.sessionId : null;
      void (async () => {
        const list = await loadSessions();
        // First message may race the backend list write — retry once if still missing.
        if (sid && !list.some((s) => s.id === sid)) {
          retryTimer = window.setTimeout(() => {
            void loadSessions();
          }, 700);
        }
      })();
    };
    const onListRefresh = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.agentId && detail.agentId !== agentId) return;
      void loadSessions();
    };
    window.addEventListener(SESSION_META_EVENT, onMeta);
    window.addEventListener(SESSION_LIST_REFRESH_EVENT, onListRefresh);
    return () => {
      window.removeEventListener(SESSION_META_EVENT, onMeta);
      window.removeEventListener(SESSION_LIST_REFRESH_EVENT, onListRefresh);
      if (retryTimer) window.clearTimeout(retryTimer);
    };
  }, [agentId, loadSessions]);

  const handleDeleteClick = (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    setConfirmingDeleteId(sessionId);
  };

  const handleDeleteConfirm = async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    setConfirmingDeleteId(null);
    try {
      await agentSessionAPI.deleteSession(agentId, sessionId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
    } catch (err: any) {
      console.error('[SessionSidebar] Delete failed:', err);
      setError(err.message || 'Failed to delete session');
    }
  };

  const handleDeleteCancel = (e: React.MouseEvent) => {
    e.stopPropagation();
    setConfirmingDeleteId(null);
  };

  const togglePin = (e: React.MouseEvent, sessionId: string, pinned: boolean) => {
    e.stopPropagation();
    setSessionPinned(agentId, sessionId, pinned);
  };

  const startRename = (e: React.MouseEvent, session: AgentSession) => {
    e.stopPropagation();
    setConfirmingDeleteId(null);
    setEditingId(session.id);
    setEditingTitle(session.title || session.id);
  };

  const cancelRename = (e?: React.SyntheticEvent) => {
    e?.stopPropagation();
    setEditingId(null);
    setEditingTitle('');
  };

  const commitRename = async (e?: React.SyntheticEvent) => {
    e?.stopPropagation();
    if (!editingId || renaming) return;
    const next = editingTitle.trim();
    const prev = sessionsRef.current.find((s) => s.id === editingId);
    if (!next || !prev || next === (prev.title || prev.id)) {
      cancelRename();
      return;
    }
    setRenaming(true);
    const sid = editingId;
    setSessions((list) => list.map((s) => (s.id === sid ? { ...s, title: next } : s)));
    setEditingId(null);
    try {
      await agentSessionAPI.renameSession(agentId, sid, next);
    } catch (err: any) {
      console.error('[SessionSidebar] Rename failed:', err);
      setError(err.message || 'Failed to rename session');
      setSessions((list) =>
        list.map((s) => (s.id === sid ? { ...s, title: prev.title || prev.id } : s)),
      );
    } finally {
      setRenaming(false);
      setEditingTitle('');
    }
  };

  const { pinnedSessions, folderGroups } = useMemo(() => {
    const pinned: AgentSession[] = [];
    const groups = new Map<string, { path: string; sessions: AgentSession[] }>();

    for (const session of sessions) {
      const meta = metaMap[session.id];
      if (meta?.pinned) pinned.push(session);

      // Always keep every session under its project folder (even when pinned).
      const path = meta?.projectPath || '';
      const name = projectFolderName(path || null);
      const existing = groups.get(name);
      if (existing) existing.sessions.push(session);
      else groups.set(name, { path, sessions: [session] });
    }

    const folderGroups = Array.from(groups.entries())
      .map(([name, g]) => ({ name, path: g.path, sessions: g.sessions }))
      .sort((a, b) => a.name.localeCompare(b.name));

    return { pinnedSessions: pinned, folderGroups };
  }, [sessions, metaMap]);

  useEffect(() => {
    setExpandedFolders((prev) => {
      const next = { ...prev };
      for (const g of folderGroups) {
        if (next[g.name] === undefined) next[g.name] = true;
      }
      return next;
    });
  }, [folderGroups]);

  if (!isOpen) return null;

  const renderSessionRow = (session: AgentSession, nested = false) => {
    const isActive = session.id === currentSessionId;
    const isCurrent = session.current;
    const isConfirming = confirmingDeleteId === session.id;
    const isEditing = editingId === session.id;
    const meta = metaMap[session.id];
    const pinned = !!meta?.pinned;
    const ageLabel = formatRelativeAge(session.last_updated || session.created_at, {
      locale: 'zh',
    });

    return (
      <div
        key={session.id}
        className={`${nested ? 'pl-7' : 'pl-2'} pr-2 py-1.5 cursor-pointer transition-colors group rounded-md mx-1 ${
          isActive ? 'bg-black/[0.06] dark:bg-white/[0.08]' : 'hover:bg-black/[0.03] dark:hover:bg-white/[0.04]'
        }`}
        onClick={() => !isConfirming && !isEditing && onViewSession(session.id)}
        onDoubleClick={() => !isConfirming && !isEditing && onSwitchAndReply(session.id)}
        title={session.created_at || session.last_updated || undefined}
      >
        <div className="flex items-start gap-1.5">
          <div className="mt-1.5 flex-shrink-0 w-3 flex items-center justify-center">
            {isCurrent && agentBusy ? (
              <OpensquadWorkingDot size={12} />
            ) : (
              <span
                className={`block w-1.5 h-1.5 rounded-full ${
                  isCurrent ? 'bg-violet-400/80' : 'bg-textMuted/45'
                }`}
                aria-hidden
              />
            )}
          </div>
          <div className="flex-1 min-w-0">
            {isEditing ? (
              <input
                ref={editInputRef}
                value={editingTitle}
                onChange={(e) => setEditingTitle(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    void commitRename(e);
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    cancelRename(e);
                  }
                }}
                onBlur={() => void commitRename()}
                maxLength={200}
                className="w-full text-[12px] leading-snug px-1.5 py-0.5 rounded border border-primary/40 bg-panel text-textMain outline-none"
                disabled={renaming}
              />
            ) : (
              <div className="text-[12px] text-textMain truncate leading-snug">
                {session.title || session.id}
              </div>
            )}
            {!isEditing && session.preview && (
              <div className="text-[10px] text-textMuted/70 truncate mt-0.5">
                {session.preview}
              </div>
            )}
          </div>
          <div className="flex items-center gap-0.5 flex-shrink-0 pt-0.5">
            {isEditing ? (
              <>
                <button
                  type="button"
                  onClick={(e) => void commitRename(e)}
                  className="p-0.5 rounded bg-primary/15 hover:bg-primary/25 transition-colors"
                  title="Save"
                >
                  <Check size={11} className="text-primary" />
                </button>
                <button
                  type="button"
                  onClick={cancelRename}
                  className="p-0.5 rounded hover:bg-bgLight transition-colors"
                  title="Cancel"
                >
                  <X size={11} className="text-textMuted" />
                </button>
              </>
            ) : (
              <>
                {/* Cursor-style relative age — always visible beside hover actions */}
                {ageLabel && !isConfirming && (
                  <span className="text-[10px] text-textMuted/55 tabular-nums px-0.5 select-none">
                    {ageLabel}
                  </span>
                )}
                {!isConfirming && (
                  <>
                    <button
                      type="button"
                      onClick={(e) => startRename(e, session)}
                      className="p-0.5 opacity-0 group-hover:opacity-100 hover:bg-primary/10 rounded transition-all"
                      title="重命名"
                    >
                      <Pencil size={11} className="text-textMuted" />
                    </button>
                    <button
                      type="button"
                      onClick={(e) => togglePin(e, session.id, !pinned)}
                      className="p-0.5 opacity-0 group-hover:opacity-100 hover:bg-primary/10 rounded transition-all"
                      title={pinned ? 'Unpin' : 'Pin'}
                    >
                      {pinned ? (
                        <PinOff size={11} className="text-primary" />
                      ) : (
                        <Pin size={11} className="text-textMuted" />
                      )}
                    </button>
                  </>
                )}
                {!isCurrent && (
                  isConfirming ? (
                    <>
                      <button
                        type="button"
                        onClick={(e) => handleDeleteConfirm(e, session.id)}
                        className="p-0.5 rounded bg-red-500/15 hover:bg-red-500/30 transition-colors"
                        title="Confirm delete"
                      >
                        <Check size={11} className="text-red-500" />
                      </button>
                      <button
                        type="button"
                        onClick={handleDeleteCancel}
                        className="p-0.5 rounded hover:bg-bgLight transition-colors"
                        title="Cancel"
                      >
                        <X size={11} className="text-textMuted" />
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={(e) => handleDeleteClick(e, session.id)}
                      className="p-0.5 opacity-0 group-hover:opacity-100 hover:bg-red-500/10 rounded transition-all"
                      title="Delete session"
                    >
                      <Trash2 size={11} className="text-red-500" />
                    </button>
                  )
                )}
              </>
            )}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="w-64 h-full border-r border-border bg-panel flex flex-col flex-shrink-0">
      <div className="p-3 border-b border-border flex items-center justify-between">
        <h3 className="text-sm font-medium text-textMain">Repositories</h3>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={loadSessions}
            disabled={loading}
            className="p-1.5 hover:bg-primary/10 rounded-md transition-colors"
            title="Refresh"
          >
            <RefreshCw size={14} className={`text-textMuted ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            type="button"
            onClick={() => onNewSession()}
            className="p-1.5 hover:bg-primary/10 rounded-md transition-colors"
            title="New Session"
          >
            <Plus size={14} className="text-primary" />
          </button>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 hover:bg-primary/10 rounded-md transition-colors"
            title="Close"
          >
            <ChevronLeft size={14} className="text-textMuted" />
          </button>
        </div>
      </div>

      {error && (
        <div className="px-3 py-2 text-[11px] text-red-500 bg-red-500/10">{error}</div>
      )}

      <div className="flex-1 overflow-y-auto py-1">
        {sessions.length === 0 && !loading && (
          <div className="px-3 py-6 text-center text-xs text-textMuted">No sessions</div>
        )}

        <div className="mb-1">
          <div className="w-full flex items-center gap-1.5 px-3 py-1.5">
            <Pin size={13} className="text-textMuted/70 shrink-0" />
            <span className="text-[12px] font-medium text-textMain truncate">Pinned</span>
            <span className="text-[10px] text-textMuted/45 ml-auto shrink-0">
              {pinnedSessions.length}
            </span>
          </div>
          {pinnedSessions.length === 0 ? (
            <div className="px-3 pb-2 ml-1 text-[11px] text-textMuted/45">No pinned sessions</div>
          ) : (
            <div className="pb-1">{pinnedSessions.map((s) => renderSessionRow(s, false))}</div>
          )}
        </div>

        {folderGroups.map((group) => {
          const open = expandedFolders[group.name] !== false;
          const showAll = !!showAllFolders[group.name];
          const visible = showAll
            ? group.sessions
            : group.sessions.slice(0, SEE_MORE_LIMIT);
          const hasMore = group.sessions.length > SEE_MORE_LIMIT;
          const canNewInFolder = !!(group.path && group.path.trim());

          return (
            <div key={group.name} className="mb-1">
              <div className="group/folder w-full flex items-center gap-0.5 pl-0.5 pr-1 py-1 hover:bg-black/[0.03] dark:hover:bg-white/[0.04] rounded-md mx-0.5">
                <button
                  type="button"
                  onClick={() =>
                    setExpandedFolders((prev) => ({ ...prev, [group.name]: !open }))
                  }
                  className="flex-1 flex items-center gap-1 text-left border-0 bg-transparent cursor-pointer min-w-0 py-0.5 px-0"
                  title={group.path || group.name}
                >
                  {open ? (
                    <ChevronDown size={12} className="text-textMuted/50 shrink-0" />
                  ) : (
                    <ChevronRight size={12} className="text-textMuted/50 shrink-0" />
                  )}
                  <Folder size={13} className="text-textMuted/70 shrink-0" />
                  <span className="text-[12px] font-medium text-textMain truncate">
                    {group.name}
                  </span>
                  <span className="text-[10px] text-textMuted/45 ml-auto shrink-0">
                    {group.sessions.length}
                  </span>
                </button>
                {canNewInFolder && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onNewSession(group.path);
                    }}
                    className="p-1 opacity-0 group-hover/folder:opacity-100 hover:bg-primary/10 rounded transition-all shrink-0"
                    title={`在此目录新建会话\n${group.path}`}
                  >
                    <MessageSquarePlus size={13} className="text-primary" />
                  </button>
                )}
              </div>
              {open && (
                <div className="pb-1">
                  {visible.map((s) => renderSessionRow(s, true))}
                  {hasMore && !showAll && (
                    <button
                      type="button"
                      onClick={() =>
                        setShowAllFolders((prev) => ({ ...prev, [group.name]: true }))
                      }
                      className="ml-10 px-2 py-1 text-[11px] text-textMuted hover:text-primary border-0 bg-transparent cursor-pointer"
                    >
                      See more
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
