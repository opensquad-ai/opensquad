/**
 * SessionSidebar - Agent session management panel.
 *
 * Lists all sessions (current + history) from the Gateway HTTP API.
 * Supports: view session, create new, delete, and switch+reply.
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Plus, Trash2, MessageSquare, History, RefreshCw, ChevronLeft, Check, X,
} from 'lucide-react';
import { agentSessionAPI, AgentSession } from '../../services/api';

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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // ID of the session pending delete confirmation (null = none)
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);

  const loadSessions = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await agentSessionAPI.getSessionList(agentId);
      setSessions(resp.sessions || []);
    } catch (err: any) {
      console.error('[SessionSidebar] Failed to load sessions:', err);
      setError(err.message || 'Failed to load sessions');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  // Load on mount and when agentId changes
  useEffect(() => {
    if (isOpen) {
      loadSessions();
    }
  }, [isOpen, loadSessions]);

  // Cancel pending confirmation when sidebar closes
  useEffect(() => {
    if (!isOpen) setConfirmingDeleteId(null);
  }, [isOpen]);

  // Optimistically update title when runner emits session_title
  useEffect(() => {
    if (!sessionTitleUpdate) return;
    setSessions(prev => prev.map(session => (
      session.id === sessionTitleUpdate.id
        ? { ...session, title: sessionTitleUpdate.title }
        : session
    )));
  }, [sessionTitleUpdate]);

  const handleDeleteClick = (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    setConfirmingDeleteId(sessionId);
  };

  const handleDeleteConfirm = async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    setConfirmingDeleteId(null);
    try {
      await agentSessionAPI.deleteSession(agentId, sessionId);
      setSessions(prev => prev.filter(s => s.id !== sessionId));
    } catch (err: any) {
      console.error('[SessionSidebar] Delete failed:', err);
      setError(err.message || 'Failed to delete session');
    }
  };

  const handleDeleteCancel = (e: React.MouseEvent) => {
    e.stopPropagation();
    setConfirmingDeleteId(null);
  };

  if (!isOpen) return null;

  return (
    <div className="w-64 h-full border-r border-border bg-panel flex flex-col flex-shrink-0">
      {/* Header */}
      <div className="p-3 border-b border-border flex items-center justify-between">
        <h3 className="text-sm font-medium text-textMain">Sessions</h3>
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

      {/* Error */}
      {error && (
        <div className="px-3 py-2 text-[11px] text-red-500 bg-red-500/10">
          {error}
        </div>
      )}

      {/* Session List */}
      <div className="flex-1 overflow-y-auto">
        {sessions.length === 0 && !loading && (
          <div className="px-3 py-6 text-center text-xs text-textMuted">
            No sessions
          </div>
        )}

        {sessions.map(session => {
          const isActive = session.id === currentSessionId;
          const isCurrent = session.current;
          const isConfirming = confirmingDeleteId === session.id;

          return (
            <div
              key={session.id}
              className={`px-3 py-2.5 cursor-pointer border-b border-border/50 transition-colors group ${
                isActive ? 'bg-primary/10 border-l-2 border-l-primary' : 'hover:bg-bgLight'
              }`}
              onClick={() => !isConfirming && onViewSession(session.id)}
              onDoubleClick={() => !isConfirming && onSwitchAndReply(session.id)}
            >
              <div className="flex items-start gap-2">
                <div className="mt-0.5 flex-shrink-0">
                  {isCurrent ? (
                    <MessageSquare size={13} className="text-primary" />
                  ) : (
                    <History size={13} className="text-textMuted" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-textMain truncate font-medium">
                    {session.title || session.id}
                  </div>
                  {session.preview && (
                    <div className="text-[10px] text-textMuted truncate mt-0.5">
                      {session.preview}
                    </div>
                  )}
                  <div className="text-[9px] text-textMuted mt-0.5 font-mono">
                    {session.id.length > 20 ? session.id.slice(0, 20) + '...' : session.id}
                  </div>
                </div>

                {/* Delete / inline confirm — only for non-current sessions */}
                {!isCurrent && (
                  <div className="flex items-center gap-0.5 flex-shrink-0">
                    {isConfirming ? (
                      <>
                        {/* Confirm delete */}
                        <button
                          onClick={(e) => handleDeleteConfirm(e, session.id)}
                          className="p-1 rounded bg-red-500/15 hover:bg-red-500/30 transition-colors"
                          title="Confirm delete"
                        >
                          <Check size={11} className="text-red-500" />
                        </button>
                        {/* Cancel */}
                        <button
                          onClick={handleDeleteCancel}
                          className="p-1 rounded hover:bg-bgLight transition-colors"
                          title="Cancel"
                        >
                          <X size={11} className="text-textMuted" />
                        </button>
                      </>
                    ) : (
                      <button
                        onClick={(e) => handleDeleteClick(e, session.id)}
                        className="p-1 opacity-0 group-hover:opacity-100 hover:bg-red-500/10 rounded transition-all"
                        title="Delete session"
                      >
                        <Trash2 size={12} className="text-red-500" />
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer hint */}
      <div className="px-3 py-2 border-t border-border text-[9px] text-textMuted text-center">
        Click to view, double-click to switch context
      </div>
    </div>
  );
};
