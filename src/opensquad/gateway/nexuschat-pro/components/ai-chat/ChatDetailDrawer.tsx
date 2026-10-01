/**
 * ChatDetailDrawer — 聊天模式的「详细」右栏。
 *
 * Opened from the header of the chat column: the conversation on screen is
 * with a contact, and this is the contact's card — who they are, the files in
 * the project they are working in, and a search across the history.
 *
 * The file half is the existing ProjectFilesPanel (tree only): it already owns
 * tree loading, expand state and the context menu, and duplicating it here is
 * how the two lists would drift apart.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FileText, History, RefreshCw, Search, X } from 'lucide-react';
import { directMessageAPI, groupAPI } from '../../services/api';
import { getLocalAvatarFallback } from '../../utils/image';
import { AGENT_STATUS_DOT, AGENT_STATUS_LABEL_KEY } from '../../utils/agentStatus';
import { ProjectFilesPanel } from './ProjectFilesPanel';
import { OpenSquadLoader } from '../OpenSquadLoader';

export interface ChatDetailDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Agent id for the session-search API. */
  agentId: string;
  /** Directory name for the file APIs (falls back to `agentId`). */
  fsAgentId: string;
  agentName: string;
  avatar: string | null;
  status: string;
  dirName?: string | null;
  rootPath: string;
  width: number;
  onWidthChange: (w: number) => void;
  onOpenFile: (relPath: string) => void;
}

interface SearchHit {
  id: string;
  title: string;
  matches: Array<{ role: 'user' | 'assistant'; snippet: string; timestamp?: string }>;
}

export const ChatDetailDrawer: React.FC<ChatDetailDrawerProps> = ({
  open,
  onClose,
  agentId,
  fsAgentId,
  agentName,
  avatar,
  status,
  dirName,
  rootPath,
  width,
  onWidthChange,
  onOpenFile,
}) => {
  const { t } = useTranslation();
  const [tab, setTab] = useState<'files' | 'history'>('files');
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(false);
  const seqRef = useRef(0);
  /** agent_id → IM 显示名（默认协作群成员），只在首次搜索时取一次。 */
  const imNamesRef = useRef<Record<string, string> | null>(null);

  /** 私信地址 = 该 agent 注册的 IM User.name，来自群成员表；不可按目录名推导。 */
  const resolveDmName = useCallback(async (): Promise<string | null> => {
    if (!imNamesRef.current) {
      try {
        const g = await groupAPI.getGroup('g-default');
        const map: Record<string, string> = {};
        for (const m of g.members || []) {
          if (m.is_agent && m.agent_id) map[m.agent_id] = m.name;
        }
        imNamesRef.current = map;
      } catch {
        return dirName || null;
      }
    }
    return imNamesRef.current[agentId] || imNamesRef.current[fsAgentId] || null;
  }, [agentId, fsAgentId, dirName]);

  useEffect(() => {
    if (!open) {
      setQuery('');
      setHits([]);
      setSearchError(false);
    }
  }, [open]);

  const runSearch = useCallback(
    async (q: string) => {
      const text = q.trim();
      if (!text) {
        setHits([]);
        setSearchError(false);
        return;
      }
      const contact = await resolveDmName();
      if (!contact) {
        setHits([]);
        setSearchError(true);
        return;
      }
      const seq = (seqRef.current += 1);
      setSearching(true);
      setSearchError(false);
      try {
        // 聊天版面的一对一会话就是私信记录 —— 历史检索搜 DM，不搜 agent-web 会话。
        const rows = await directMessageAPI.listThread(contact, text);
        if (seq !== seqRef.current) return;
        setHits(
          rows.map((m) => ({
            id: m.id,
            title: m.sender,
            matches: [
              {
                role: m.is_sender ? ('user' as const) : ('assistant' as const),
                snippet: m.content,
              },
            ],
          })),
        );
      } catch {
        if (seq !== seqRef.current) return;
        setHits([]);
        setSearchError(true);
      } finally {
        if (seq === seqRef.current) setSearching(false);
      }
    },
    [resolveDmName],
  );

  // Debounce: the search hits every session on disk.
  useEffect(() => {
    if (!open || tab !== 'history') return;
    const handle = window.setTimeout(() => void runSearch(query), 300);
    return () => window.clearTimeout(handle);
  }, [open, tab, query, runSearch]);

  const statusDot = useMemo(() => AGENT_STATUS_DOT[status] || 'bg-gray-400', [status]);

  if (!open) return null;

  return (
    <div className="relative z-0 flex-shrink-0 h-full flex" data-testid="chat-detail-drawer">
      <div className="h-full flex flex-col os-depth-card" style={{ width }}>
        <div className="h-11 px-2.5 border-b border-border box-border flex items-center gap-2 flex-shrink-0">
          <span className="flex-1 min-w-0 truncate text-[13px] font-medium text-textMuted">
            {t('aiChat.chat.detail')}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-md hover:bg-primary/10 shrink-0"
            title={t('common.close')}
            aria-label={t('common.close')}
          >
            <X size={14} className="text-textMuted" />
          </button>
        </div>

        <div className="px-3 py-3 border-b border-border/60 flex items-center gap-2.5 flex-shrink-0">
          <img
            src={avatar || getLocalAvatarFallback(fsAgentId || agentId, agentName)}
            alt=""
            className="h-10 w-10 rounded-full object-cover bg-border shrink-0"
            loading="lazy"
            onError={(e) => {
              const img = e.currentTarget;
              if (img.dataset.fallbackApplied) return;
              img.dataset.fallbackApplied = '1';
              img.src = getLocalAvatarFallback(fsAgentId || agentId, agentName);
            }}
          />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium text-textMain">{agentName}</div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-textMuted">
              <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${statusDot}`} />
              <span className="shrink-0">{t(AGENT_STATUS_LABEL_KEY[status] || 'agentManager.statusStopped')}</span>
              {dirName ? <span className="truncate font-mono text-textMuted/60">{dirName}</span> : null}
            </div>
          </div>
        </div>

        <div className="px-2 py-1.5 flex items-center gap-1 border-b border-border/60 flex-shrink-0">
          <button
            type="button"
            onClick={() => setTab('files')}
            className={`flex min-w-0 flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
              tab === 'files' ? 'bg-black/[0.06] text-textMain dark:bg-white/10' : 'text-textMuted hover:text-textMain'
            }`}
          >
            <FileText size={12} strokeWidth={1.75} className="shrink-0" />
            <span className="truncate">{t('aiChat.chat.files')}</span>
          </button>
          <button
            type="button"
            onClick={() => setTab('history')}
            className={`flex min-w-0 flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
              tab === 'history' ? 'bg-black/[0.06] text-textMain dark:bg-white/10' : 'text-textMuted hover:text-textMain'
            }`}
          >
            <History size={12} strokeWidth={1.75} className="shrink-0" />
            <span className="truncate">{t('aiChat.chat.history')}</span>
          </button>
        </div>

        {tab === 'files' ? (
          <ProjectFilesPanel
            isOpen={open}
            onClose={onClose}
            agentId={fsAgentId}
            rootPath={rootPath}
            width={width}
            onWidthChange={onWidthChange}
            treeOnly
            hideAllFiles
            onOpenFile={onOpenFile}
            uiMode="classic"
          />
        ) : (
          <div className="flex-1 min-h-0 flex flex-col">
            <div className="px-3 py-2 flex-shrink-0">
              <div className="flex items-center gap-1.5 rounded-lg bg-black/[0.04] px-2 py-1.5 dark:bg-white/[0.06]">
                <Search size={13} className="text-textMuted shrink-0" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={t('aiChat.chat.searchHistoryPlaceholder')}
                  className="min-w-0 flex-1 bg-transparent text-[12px] text-textMain outline-none placeholder:text-textMuted/70"
                />
                {searching ? <OpenSquadLoader size={12} /> : null}
                {query ? (
                  <button
                    type="button"
                    onClick={() => setQuery('')}
                    className="p-0.5 rounded text-textMuted hover:text-textMain shrink-0"
                    aria-label={t('common.cancel')}
                  >
                    <X size={12} />
                  </button>
                ) : null}
              </div>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto os-depth-nest os-depth-nest--flush">
              {searchError ? (
                <div className="px-3 py-3 text-[11px] text-rose-500">{t('aiChat.chat.searchFailed')}</div>
              ) : !query.trim() ? (
                <div className="px-3 py-3 text-[11px] text-textMuted/70">
                  {t('aiChat.chat.searchHistoryHint')}
                </div>
              ) : !searching && hits.length === 0 ? (
                <div className="px-3 py-3 text-[11px] text-textMuted/70">{t('aiChat.chat.searchEmpty')}</div>
              ) : (
                hits.map((hit) => (
                  <div
                    key={`hit-${hit.id}`}
                    data-testid="chat-detail-search-hit"
                    className="w-full text-left px-3 py-2 border-b border-border/40"
                  >
                    <div className="truncate text-[12px] text-textMain">{hit.title || hit.id}</div>
                    {(hit.matches || []).slice(0, 3).map((m, i) => (
                      <div key={`hit-${hit.id}-m${i}`} className="mt-0.5 flex gap-1.5 text-[11px] text-textMuted">
                        <span className="shrink-0 opacity-70">
                          {m.role === 'user' ? t('aiChat.chat.hitYou') : t('aiChat.chat.hitAgent')}
                        </span>
                        <span className="min-w-0 flex-1 truncate">{m.snippet}</span>
                      </div>
                    ))}
                  </div>
                ))
              )}
            </div>
            <div className="px-3 py-1.5 border-t border-border/60 flex items-center justify-end flex-shrink-0">
              <button
                type="button"
                onClick={() => void runSearch(query)}
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-textMuted hover:bg-primary/10 hover:text-textMain"
              >
                <RefreshCw size={11} />
                {t('aiChat.agentRefresh')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ChatDetailDrawer;
