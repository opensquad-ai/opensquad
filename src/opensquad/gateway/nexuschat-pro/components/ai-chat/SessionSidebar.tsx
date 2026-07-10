/**
 * SessionSidebar — Agent session list grouped by project folder (last path segment).
 * Supports pin-to-top. Project path metadata lives in localStorage (sessionProjectMeta).
 */
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Plus, Trash2, MessageSquare, History, RefreshCw, ChevronLeft, Check, X,
  Folder, Pin, PinOff, ChevronDown, ChevronRight,
} from 'lucide-react';
import { agentSessionAPI, AgentSession } from '../../services/api';
import {
  loadSessionProjectMeta,
  projectFolderName,
  setSessionPinned,
  SESSION_META_EVENT,
  type SessionProjectMeta,
} from '../../utils/sessionProjectMeta';

interface SessionSidebarProps {
  agentId: string;
  currentSessionId: string | null;
  onViewSession: (sessionId: string) => void;
  onNewSession: () => void;
  onSwitchAndReply: (sessionId: string) => void;
  isOpen: boolean;
  onClose: () => void;
  sessionTitleUpdate?: { id: string; title: string } | null;
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
}) => {
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [metaMap, setMetaMap] = useState<Record<string, SessionProjectMeta>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({});
  const [showAllFolders, setShowAllFolders] = useState<Record<string, boolean>>({});
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
    if (!isOpen) setConfirmingDeleteId(null);
  }, [isOpen]);

  // New session id assigned while sidebar is open — pull list once if missing.
  useEffect(() => {
    if (!isOpen || !currentSessionId) return;
    if (sessionsRef.current.some((s) => s.id === currentSessionId)) return;
    void loadSessions();
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
    window.addEventListener(SESSION_META_EVENT, onMeta);
    return () => {
      window.removeEventListener(SESSION_META_EVENT, onMeta);
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

  const renderSessionRow = (session: AgentSession) => {
    const isActive = session.id === currentSessionId;
    const isCurrent = session.current;
    const isConfirming = confirmingDeleteId === session.id;
    const meta = metaMap[session.id];
    const pinned = !!meta?.pinned;

    return (
      <div
        key={session.id}
        className={`pl-2 pr-2 py-1.5 cursor-pointer transition-colors group rounded-md mx-1 ${
          isActive ? 'bg-black/[0.06] dark:bg-white/[0.08]' : 'hover:bg-black/[0.03] dark:hover:bg-white/[0.04]'
        }`}
        onClick={() => !isConfirming && onViewSession(session.id)}
        onDoubleClick={() => !isConfirming && onSwitchAndReply(session.id)}
      >
        <div className="flex items-start gap-1.5">
          <div className="mt-0.5 flex-shrink-0">
            {isCurrent ? (
              <MessageSquare size={12} className="text-primary" />
            ) : (
              <History size={12} className="text-textMuted/60" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[12px] text-textMain truncate leading-snug">
              {session.title || session.id}
            </div>
            {session.preview && (
              <div className="text-[10px] text-textMuted/70 truncate mt-0.5">
                {session.preview}
              </div>
            )}
          </div>
          <div className="flex items-center gap-0.5 flex-shrink-0">
            {!isConfirming && (
              <button
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
            )}
            {!isCurrent && (
              isConfirming ? (
                <>
                  <button
                    onClick={(e) => handleDeleteConfirm(e, session.id)}
                    className="p-0.5 rounded bg-red-500/15 hover:bg-red-500/30 transition-colors"
                    title="Confirm delete"
                  >
                    <Check size={11} className="text-red-500" />
                  </button>
                  <button
                    onClick={handleDeleteCancel}
                    className="p-0.5 rounded hover:bg-bgLight transition-colors"
                    title="Cancel"
                  >
                    <X size={11} className="text-textMuted" />
                  </button>
                </>
              ) : (
                <button
                  onClick={(e) => handleDeleteClick(e, session.id)}
                  className="p-0.5 opacity-0 group-hover:opacity-100 hover:bg-red-500/10 rounded transition-all"
                  title="Delete session"
                >
                  <Trash2 size={11} className="text-red-500" />
                </button>
              )
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
            onClick={loadSessions}
            disabled={loading}
            className="p-1.5 hover:bg-primary/10 rounded-md transition-colors"
            title="Refresh"
          >
            <RefreshCw size={14} className={`text-textMuted ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={onNewSession}
            className="p-1.5 hover:bg-primary/10 rounded-md transition-colors"
            title="New Session"
          >
            <Plus size={14} className="text-primary" />
          </button>
          <button
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
            <div className="pb-1">{pinnedSessions.map(renderSessionRow)}</div>
          )}
        </div>

        {folderGroups.map((group) => {
          const open = expandedFolders[group.name] !== false;
          const showAll = !!showAllFolders[group.name];
          const visible = showAll
            ? group.sessions
            : group.sessions.slice(0, SEE_MORE_LIMIT);
          const hasMore = group.sessions.length > SEE_MORE_LIMIT;

          return (
            <div key={group.name} className="mb-1">
              <button
                type="button"
                onClick={() =>
                  setExpandedFolders((prev) => ({ ...prev, [group.name]: !open }))
                }
                className="w-full flex items-center gap-1.5 px-3 py-1.5 text-left border-0 bg-transparent cursor-pointer hover:bg-black/[0.03] dark:hover:bg-white/[0.04]"
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
              {open && (
                <div className="pb-1">
                  {visible.map(renderSessionRow)}
                  {hasMore && !showAll && (
                    <button
                      type="button"
                      onClick={() =>
                        setShowAllFolders((prev) => ({ ...prev, [group.name]: true }))
                      }
                      className="ml-7 px-2 py-1 text-[11px] text-textMuted hover:text-primary border-0 bg-transparent cursor-pointer"
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

      <div className="px-3 py-2 border-t border-border text-[9px] text-textMuted text-center">
        Click to view · double-click to switch · hover to pin
      </div>
    </div>
  );
};
