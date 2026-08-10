/**
 * SessionHistoryPreview — read-only session history for non-focused split panes.
 * Composer is provided by WorkspacePaneShell (AgentWebComposer) so every pane matches.
 */
import React, { useEffect, useRef, useState } from 'react';
import { OpenSquadLoader } from '../OpenSquadLoader';
import { agentSessionAPI } from '../../services/api';

interface SessionHistoryPreviewProps {
  agentId: string;
  sessionId: string;
  onActivate?: () => void;
}

export const SessionHistoryPreview: React.FC<SessionHistoryPreviewProps> = ({
  agentId,
  sessionId,
  onActivate,
}) => {
  const [loading, setLoading] = useState(true);
  const [title, setTitle] = useState('');
  const [lines, setLines] = useState<Array<{ role: string; text: string }>>([]);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const resp = await agentSessionAPI.getSessionHistoryPaged(agentId, sessionId, 0, 60);
        if (cancelled) return;
        const session = resp.session as
          | { title?: string; messages?: Array<{ role?: string; content?: string }> }
          | undefined;
        setTitle(session?.title || sessionId.slice(0, 12));
        const msgs = (session?.messages || []) as Array<{ role?: string; content?: string }>;
        setLines(
          msgs
            .filter((m) => m.role === 'user' || m.role === 'assistant')
            .slice(-30)
            .map((m) => ({
              role: m.role || 'assistant',
              text: String(m.content || '').trim(),
            })),
        );
      } catch (err: any) {
        if (!cancelled) setError(err?.message || '无法加载会话');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, sessionId]);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [lines.length, loading]);

  return (
    <div className="flex-1 min-h-0 flex flex-col bg-panel">
      <div
        className="px-3 py-1.5 border-b border-border/60 text-[11px] text-textMuted shrink-0 cursor-pointer"
        onClick={onActivate}
        title="点击激活此窗格"
      >
        {title || sessionId.slice(0, 8)} · 点击激活以查看完整对话
      </div>
      <div
        ref={listRef}
        className="flex-1 min-h-0 overflow-y-auto px-3 py-2 space-y-2.5 cursor-pointer"
        onClick={onActivate}
      >
        {loading ? (
          <div className="flex items-center justify-center text-textMuted text-xs gap-2 py-8">
            <OpenSquadLoader size={14} /> 加载中…
          </div>
        ) : error ? (
          <div className="px-1 py-4 text-[12px] text-rose-400">{error}</div>
        ) : lines.length === 0 ? (
          <div className="flex items-center justify-center text-textMuted text-[13px] px-4 text-center py-10">
            空会话 — 在下方输入开始对话
          </div>
        ) : (
          lines.map((l, i) => (
            <div
              key={`${i}-${l.role}`}
              className={`text-[12px] leading-relaxed whitespace-pre-wrap break-words ${
                l.role === 'user'
                  ? 'text-textMain bg-primary/8 border border-primary/15 rounded-lg px-2.5 py-1.5'
                  : 'text-textMain/90'
              }`}
            >
              <div className="text-[10px] uppercase tracking-wide text-textMuted mb-0.5">
                {l.role === 'user' ? '你' : 'Agent'}
              </div>
              {l.text.slice(0, 2000)}
              {l.text.length > 2000 ? '…' : ''}
            </div>
          ))
        )}
      </div>
    </div>
  );
};
