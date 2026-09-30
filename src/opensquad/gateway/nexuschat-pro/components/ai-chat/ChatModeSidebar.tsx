/**
 * ChatModeSidebar — the rail for 聊天 (user) mode: QQ/WeChat-style.
 *
 * Two tabs on top of the rail: 会话 (this agent's sessions, with the last
 * message as preview — clicking one switches the whole chat page) and 通讯录
 * (every agent + group chat as a contact, A–Z bucketed like a phone book).
 *
 * Deliberately separate from SessionSidebar: that rail is a project/session
 * manager (pinning, archiving, batch delete, skills/plugins entries).  Here
 * the user is chatting with a contact, so the rail carries none of it.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bot, MessageCircle, RefreshCw, Users } from 'lucide-react';
import { agentSessionAPI, type AgentSession } from '../../services/api';
import { SESSION_LIST_REFRESH_EVENT } from '../../utils/sessionProjectMeta';
import { SOFT_PRESENCE_MS, useSoftPresence } from '../../utils/useSoftPresence';
import { formatRelativeAge, parseTimestampMs } from '../../utils/time';
import { getLocalAvatarFallback } from '../../utils/image';
import { isSessionRowBusy } from '../../utils/sessionBusy';
import { agentAvatar, agentLabel, agentRowKey, agentStatusOf, AGENT_STATUS_DOT } from '../../utils/agentStatus';
import { useChatContacts } from '../../hooks/useChatContacts';
import { PulseDotsOrbit } from './PulseDotsStatus';
import { UiModeSwitch, type UiMode } from './UiModeSwitch';
import { AccountRailFooter, type AccountUser } from '../AccountRailFooter';

export interface ChatModeSidebarProps {
  agentId: string;
  currentSessionId: string | null;
  onViewSession: (sessionId: string) => void;
  uiMode: UiMode;
  onUiModeChange?: (mode: UiMode) => void;
  isOpen: boolean;
  busySessionIds?: string[];
  streamingSessionIds?: string[];
  unseenCompleteSessionIds?: string[];
  currentUser?: AccountUser;
  onOpenProfile?: () => void;
  onOpenSettings?: () => void;
  /** Open the detail drawer for the conversation on screen. */
  onOpenDetail?: () => void;
}

const RAIL_WIDTH = 264;
const SESSION_PAGE = 60;

/** A–Z bucket for the contact list; everything else lands in '#'. */
function bucketOf(label: string): string {
  const first = (label.trim()[0] || '').toUpperCase();
  return /[A-Z]/.test(first) ? first : '#';
}

export const ChatModeSidebar: React.FC<ChatModeSidebarProps> = ({
  agentId,
  currentSessionId,
  onViewSession,
  uiMode,
  onUiModeChange,
  isOpen,
  busySessionIds = [],
  streamingSessionIds = [],
  unseenCompleteSessionIds = [],
  currentUser = null,
  onOpenProfile,
  onOpenSettings,
  onOpenDetail,
}) => {
  const { t, i18n } = useTranslation();
  const ageLocale: 'zh' | 'en' = i18n.language?.startsWith('zh') ? 'zh' : 'en';
  const [tab, setTab] = useState<'sessions' | 'contacts'>('sessions');
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [loading, setLoading] = useState(false);
  const { agents, groups, loading: contactsLoading, reload: reloadContacts } = useChatContacts({ enabled: isOpen });

  /** Avatar of the agent this rail belongs to — every session row is a thread
   *  with the same contact, exactly like a messenger's message list. */
  const selfAgent = useMemo(
    () => agents.find((a) => a.agent_id === agentId) || null,
    [agents, agentId],
  );
  const selfLabel = selfAgent ? agentLabel(selfAgent) : agentId;
  const selfAvatar = selfAgent ? agentAvatar(selfAgent) : null;

  const { mounted: softMounted, visible: softVisible } = useSoftPresence(isOpen, SOFT_PRESENCE_MS);

  const loadSessions = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    try {
      const resp = await agentSessionAPI.getSessionList(agentId, 0, SESSION_PAGE);
      setSessions((resp.sessions || []).filter((s) => s.origin !== 'scheduled_task'));
    } catch {
      /* keep the previous list — the rail must not blank on a transient error */
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    if (!isOpen) return;
    void loadSessions();
    const timer = window.setInterval(() => void loadSessions(), 30000);
    const onPush = () => void loadSessions();
    window.addEventListener(SESSION_LIST_REFRESH_EVENT, onPush);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(SESSION_LIST_REFRESH_EVENT, onPush);
    };
  }, [isOpen, loadSessions]);

  /** Newest first — this is a messenger list, not a workspace tree. */
  const rows = useMemo(() => {
    const at = (s: AgentSession) => {
      const ms = parseTimestampMs(s.last_updated || s.created_at);
      return Number.isFinite(ms) ? ms : 0;
    };
    return [...sessions].sort((a, b) => at(b) - at(a));
  }, [sessions]);

  const buckets = useMemo(() => {
    const map = new Map<string, typeof agents>();
    for (const a of agents) {
      const key = bucketOf(agentLabel(a));
      const list = map.get(key);
      if (list) list.push(a);
      else map.set(key, [a]);
    }
    return [...map.entries()]
      .sort(([a], [b]) => (a === '#' ? 1 : b === '#' ? -1 : a.localeCompare(b)))
      .map(([letter, list]) => ({
        letter,
        list: [...list].sort((a, b) => agentLabel(a).localeCompare(agentLabel(b))),
      }));
  }, [agents]);

  const openAgent = useCallback((agentIdToOpen: string, ready: boolean) => {
    if (!ready) {
      window.alert(t('agentManager.agentStartingHint'));
      return;
    }
    window.dispatchEvent(new CustomEvent('openAgentChat', { detail: { agentId: agentIdToOpen } }));
    window.dispatchEvent(new CustomEvent('switchView', { detail: 'ai-chat' }));
  }, [t]);

  const openGroup = useCallback((groupId: string) => {
    window.dispatchEvent(new CustomEvent('opensquad-select-group', { detail: { groupId } }));
    window.dispatchEvent(new CustomEvent('switchView', { detail: 'chat' }));
  }, []);

  if (!softMounted) return null;

  return (
    <div
      className={`os-soft-rail ${softVisible ? 'is-open' : ''}`}
      style={{ width: softVisible ? RAIL_WIDTH : 0 }}
      aria-hidden={!softVisible}
    >
      <div
        className="relative h-full flex flex-col os-depth-card os-soft-rail-inner"
        style={{ width: RAIL_WIDTH }}
      >
        <div className="h-11 px-2 border-b border-border box-border flex items-center shrink-0">
          <UiModeSwitch uiMode={uiMode} onUiModeChange={onUiModeChange} />
        </div>

        <div className="px-2 py-1.5 flex items-center gap-1 border-b border-border/60 shrink-0">
          <div className="flex min-w-0 flex-1 items-center rounded-lg bg-black/[0.04] p-[2px] dark:bg-white/[0.06]">
            <button
              type="button"
              onClick={() => setTab('sessions')}
              className={`flex min-w-0 flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
                tab === 'sessions' ? 'bg-white text-textMain shadow-sm dark:bg-panel' : 'text-textMuted hover:text-textMain'
              }`}
            >
              <MessageCircle size={12} strokeWidth={1.75} className="shrink-0" />
              <span className="truncate">{t('aiChat.chat.sessions')}</span>
            </button>
            <button
              type="button"
              onClick={() => setTab('contacts')}
              className={`flex min-w-0 flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
                tab === 'contacts' ? 'bg-white text-textMain shadow-sm dark:bg-panel' : 'text-textMuted hover:text-textMain'
              }`}
            >
              <Users size={12} strokeWidth={1.75} className="shrink-0" />
              <span className="truncate">{t('aiChat.chat.contacts')}</span>
            </button>
          </div>
          <button
            type="button"
            onClick={() => {
              void loadSessions();
              void reloadContacts();
            }}
            className="p-1 rounded text-textMuted hover:bg-black/5 dark:hover:bg-white/10 shrink-0"
            title={t('aiChat.agentRefresh')}
            aria-label={t('aiChat.agentRefresh')}
          >
            <RefreshCw size={13} className={loading || contactsLoading ? 'animate-spin' : undefined} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto os-depth-nest os-depth-nest--flush">
          {tab === 'sessions' ? (
            rows.length === 0 ? (
              <div className="px-3 py-4 text-[11px] text-textMuted/70">
                {loading ? t('common.loading') : t('aiChat.chat.noSessions')}
              </div>
            ) : (
              rows.map((s) => {
                const isCurrent = !!currentSessionId && s.id === currentSessionId;
                const busy = isSessionRowBusy({
                  sessionId: s.id,
                  busySessionIds,
                  streamingSessionIds,
                });
                const unseen = !busy && !isCurrent && unseenCompleteSessionIds.includes(s.id);
                const label = s.title || s.id;
                return (
                  <div
                    key={`chat-session-${s.id}`}
                    data-testid="chat-mode-session-row"
                    onClick={() => onViewSession(s.id)}
                    className={`group flex items-center gap-2.5 mx-1.5 px-2 py-2 rounded-xl cursor-pointer transition-colors ${
                      isCurrent ? 'bg-primary/10' : 'hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'
                    }`}
                  >
                    <div className="relative shrink-0">
                      <img
                        src={selfAvatar || getLocalAvatarFallback(selfLabel, selfLabel)}
                        alt=""
                        className="h-9 w-9 rounded-full object-cover bg-border"
                        loading="lazy"
                        onError={(e) => {
                          const img = e.currentTarget;
                          if (img.dataset.fallbackApplied) return;
                          img.dataset.fallbackApplied = '1';
                          img.src = getLocalAvatarFallback(selfLabel, selfLabel);
                        }}
                      />
                      {unseen ? (
                        <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-textMuted border-2 border-panel" />
                      ) : null}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-1.5">
                        <span className="min-w-0 flex-1 truncate text-[13px] text-textMain">{label}</span>
                        <span className="shrink-0 text-[10px] text-textMuted/80">
                          {formatRelativeAge(s.last_updated || s.created_at, { locale: ageLocale })}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5 min-w-0">
                        {busy ? <PulseDotsOrbit size={12} className="shrink-0" /> : null}
                        <span className="min-w-0 flex-1 truncate text-[11px] text-textMuted">
                          {s.preview || t('aiChat.chat.noPreview')}
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })
            )
          ) : (
            <>
              <div className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-textMuted">
                {t('aiChat.chat.groups')}
              </div>
              {groups.length === 0 ? (
                <div className="px-3 py-1 text-[10px] text-textMuted/50">{t('aiChat.none')}</div>
              ) : (
                groups.map((g) => (
                  <div
                    key={`chat-group-${g.id}`}
                    data-testid="chat-mode-group-row"
                    onClick={() => openGroup(g.id)}
                    className="flex items-center gap-2.5 mx-1.5 px-2 py-2 rounded-xl cursor-pointer hover:bg-black/[0.04] dark:hover:bg-white/[0.06]"
                  >
                    <img
                      src={g.avatar || getLocalAvatarFallback(g.id, g.name)}
                      alt=""
                      className="h-9 w-9 rounded-full object-cover bg-border shrink-0"
                      loading="lazy"
                      onError={(e) => {
                        const img = e.currentTarget;
                        if (img.dataset.fallbackApplied) return;
                        img.dataset.fallbackApplied = '1';
                        img.src = getLocalAvatarFallback(g.id, g.name);
                      }}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] text-textMain">{g.name}</div>
                      <div className="mt-0.5 truncate text-[11px] text-textMuted">
                        {g.last_message?.content || t('aiChat.chat.noPreview')}
                      </div>
                    </div>
                  </div>
                ))
              )}

              <div className="px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wide text-textMuted">
                {t('aiChat.chat.agents')}
              </div>
              {agents.length === 0 ? (
                <div className="px-3 py-1 text-[10px] text-textMuted/50">
                  {contactsLoading ? t('common.loading') : t('aiChat.agentEmpty')}
                </div>
              ) : (
                buckets.map(({ letter, list }) => (
                  <div key={`chat-bucket-${letter}`}>
                    <div className="sticky top-0 z-10 bg-panel/90 px-3 py-0.5 text-[10px] font-semibold text-textMuted backdrop-blur">
                      {letter}
                    </div>
                    {list.map((a) => {
                      const st = agentStatusOf(a);
                      const label = agentLabel(a);
                      const isCurrent = a.agent_id === agentId;
                      return (
                        <div
                          key={`chat-contact-${a.agent_id || a.dir_name}`}
                          data-testid="chat-mode-agent-row"
                          onClick={() => openAgent(a.agent_id, !!a.ready)}
                          className={`flex items-center gap-2.5 mx-1.5 px-2 py-2 rounded-xl cursor-pointer transition-colors ${
                            isCurrent ? 'bg-primary/[0.07]' : 'hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'
                          }`}
                        >
                          <div className="relative shrink-0">
                            <img
                              src={agentAvatar(a) || getLocalAvatarFallback(agentRowKey(a), label)}
                              alt=""
                              className="h-9 w-9 rounded-full object-cover bg-border"
                              loading="lazy"
                              onError={(e) => {
                                const img = e.currentTarget;
                                if (img.dataset.fallbackApplied) return;
                                img.dataset.fallbackApplied = '1';
                                img.src = getLocalAvatarFallback(agentRowKey(a), label);
                              }}
                            />
                            <span
                              className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-panel ${
                                AGENT_STATUS_DOT[st] || 'bg-gray-400'
                              }`}
                            />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-1.5 min-w-0">
                              <span className="truncate text-[13px] text-textMain">{label}</span>
                              {isCurrent ? (
                                <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-[1px] text-[9px] text-primary">
                                  {t('aiChat.agentCurrent')}
                                </span>
                              ) : null}
                            </div>
                            <div className="mt-0.5 flex items-center gap-1 truncate text-[11px] text-textMuted">
                              {a.agent_type ? (
                                <span className="inline-flex items-center gap-0.5">
                                  <Bot size={10} strokeWidth={1.75} className="shrink-0" />
                                  {a.agent_type}
                                </span>
                              ) : null}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ))
              )}
            </>
          )}
        </div>

        {(onOpenProfile || onOpenSettings) && (
          <AccountRailFooter
            currentUser={currentUser}
            onOpenProfile={() => onOpenProfile?.()}
            onOpenSettings={() => onOpenSettings?.()}
            actions={
              onOpenDetail ? (
                <button
                  type="button"
                  onClick={onOpenDetail}
                  className="rounded-lg p-1.5 text-textMuted hover:bg-primary/10 hover:text-textMain"
                  title={t('aiChat.chat.detail')}
                  aria-label={t('aiChat.chat.detail')}
                >
                  <Bot size={16} strokeWidth={1.75} />
                </button>
              ) : undefined
            }
          />
        )}
      </div>
    </div>
  );
};

export default ChatModeSidebar;
