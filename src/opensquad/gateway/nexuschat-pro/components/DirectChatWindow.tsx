/**
 * DirectChatWindow — 与某个 agent 的**一对一私信**窗口（聊天版面）。
 *
 * 它属于群聊通讯那一块，只是从多对多变成一对一：数据走 direct_messages
 * （`contact_name` 线程 + `new_direct_message` 增量），不是 agent-web 的
 * session/timeline。所以这里没有会话、工具流、深度思考、追问 —— 只有对话。
 *
 * 联系人的私信地址由调用方解析后传入（agent 的 IM 用户 `name`，来自群成员表；
 * 绝不是 dir_name 推导出来的），见 App 的 `resolveDmContact`。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Send } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { directMessageAPI, type DirectMessageItem } from '../services/api';
import { getLocalAvatarFallback } from '../utils/image';
import { MessageBubble, type ChatMessage } from './ai-chat/MessageBubble';
import { OpenSquadLoader } from './OpenSquadLoader';

export interface DirectChatWindowProps {
  /** 私信地址：对方 User.name（由调用方解析，可能是 dir_name 之外的显示名）。 */
  contactName: string;
  /** 界面上的名字与头像（与通讯录一致）。 */
  contactLabel: string;
  contactAvatar?: string | null;
  /** 当前用户，用于气泡方向与头像。 */
  currentUser?: { id: string; name: string; avatar?: string | null } | null;
  onBack?: () => void;
}

const POLL_MS = 5000;

export const DirectChatWindow: React.FC<DirectChatWindowProps> = ({
  contactName,
  contactLabel,
  contactAvatar,
  currentUser = null,
}) => {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<DirectMessageItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const seqRef = useRef(0);

  const load = useCallback(
    async (silent = false) => {
      if (!contactName) return;
      const seq = (seqRef.current += 1);
      if (!silent) setLoading(true);
      try {
        const list = await directMessageAPI.listThread(contactName);
        if (seq !== seqRef.current) return;
        // 后端按时间倒序返回；气泡从上到下要正序。
        setMessages([...list].sort((a, b) => a.timestamp - b.timestamp));
        setError(false);
      } catch {
        if (seq === seqRef.current) setError(true);
      } finally {
        if (seq === seqRef.current && !silent) setLoading(false);
      }
    },
    [contactName],
  );

  useEffect(() => {
    setMessages([]);
    void load();
  }, [load]);

  // 私信没有挂在 agent-web 的 WS 上，靠轻轮询拿对方的新消息。
  useEffect(() => {
    if (!contactName) return;
    const timer = window.setInterval(() => void load(true), POLL_MS);
    return () => window.clearInterval(timer);
  }, [contactName, load]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [messages.length]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await directMessageAPI.sendDirectMessage(contactName, '', text);
      setDraft('');
      await load(true);
    } catch {
      setError(true);
    } finally {
      setSending(false);
    }
  }, [contactName, draft, load, sending]);

  const bubbles = useMemo(
    () =>
      messages.map((m) => {
        const mine = m.is_sender;
        const message: ChatMessage = {
          role: mine ? 'user' : 'assistant',
          content: m.content,
          timestamp: new Date(m.timestamp).toISOString(),
          message_id: m.id,
        };
        return { id: m.id, mine, message };
      }),
    [messages],
  );

  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col os-depth-card overflow-hidden">
      <div className="h-11 px-3 border-b border-border box-border flex items-center gap-2 shrink-0">
        <img
          src={contactAvatar || getLocalAvatarFallback(contactName, contactLabel)}
          alt=""
          className="h-7 w-7 shrink-0 rounded-full object-cover bg-border"
          loading="lazy"
          onError={(e) => {
            const img = e.currentTarget;
            if (img.dataset.fallbackApplied) return;
            img.dataset.fallbackApplied = '1';
            img.src = getLocalAvatarFallback(contactName, contactLabel);
          }}
        />
        <span className="min-w-0 flex-1 truncate text-sm font-bold text-textMain">{contactLabel}</span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-3 sm:px-4 py-3">
        {loading && messages.length === 0 ? (
          <div className="h-40 flex items-center justify-center">
            <OpenSquadLoader size={28} />
          </div>
        ) : error && messages.length === 0 ? (
          <div className="px-2 py-3 text-[12px] text-rose-500">{t('aiChat.chat.dmLoadFailed')}</div>
        ) : messages.length === 0 ? (
          <div className="px-2 py-3 text-[12px] text-textMuted">{t('aiChat.chat.dmEmpty')}</div>
        ) : (
          bubbles.map((b) => (
            <MessageBubble
              key={b.id}
              message={b.message}
              variant="messenger"
              senderName={b.mine ? (currentUser?.name || undefined) : contactLabel}
              senderAvatar={b.mine ? (currentUser?.avatar ?? null) : (contactAvatar ?? null)}
              agentId={contactName}
            />
          ))
        )}
        <div ref={endRef} />
      </div>

      <div className="shrink-0 border-t border-border px-3 py-2 flex items-end gap-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          rows={1}
          placeholder={t('aiChat.chat.dmPlaceholder')}
          className="min-w-0 flex-1 resize-none rounded-xl border border-border bg-bgLight px-3 py-2 text-[13px] text-textMain outline-none focus:border-primary/40"
        />
        <button
          type="button"
          onClick={() => void send()}
          disabled={sending || !draft.trim()}
          className="h-9 w-9 shrink-0 rounded-full bg-primary text-white flex items-center justify-center disabled:opacity-40"
          title={t('aiChat.chat.send')}
          aria-label={t('aiChat.chat.send')}
        >
          {sending ? <OpenSquadLoader size={14} /> : <Send size={15} />}
        </button>
      </div>
    </div>
  );
};

export default DirectChatWindow;
