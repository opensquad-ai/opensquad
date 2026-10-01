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
import { FolderUp, Image as ImageIcon, Paperclip, Reply, Send, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { directMessageAPI, uploadAPI, type DirectMessageItem } from '../services/api';
import { getLocalAvatarFallback } from '../utils/image';
import { DM_QUOTE_MAX, encodeDmQuote, parseDmQuote, type DmQuote } from '../utils/dmQuote';
import { MessageBubble, type ChatMessage, type FileAttachment } from './ai-chat/MessageBubble';
import { CollabTaskCard, openCollabTaskWindow, parseCollabTask } from './CollabTaskCard';
import { WindowCard, parseWindowCard } from './WindowCard';
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
  /** 打开「详细」抽屉（文件 + 私信历史检索）。 */
  onOpenDetail?: () => void;
}

/** 一页私信条数；滚动到顶再往前取一页。 */
const PAGE_SIZE = 50;
/** 兜底轮询：正常情况靠 `websocket_message` 即时增量，这里只防丢事件。 */
const POLL_MS = 20000;

/** 私信附件在库里的形态（后端按 JSON 原样存取）。 */
type DmAttachment = { url: string; type: string; name: string; size: string };

const isImageAttachment = (a: DmAttachment): boolean =>
  a.type === 'image' || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(a.url || '');

export const DirectChatWindow: React.FC<DirectChatWindowProps> = ({
  contactName,
  contactLabel,
  contactAvatar,
  currentUser = null,
  onOpenDetail,
}) => {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<DirectMessageItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  // 待发送的附件：文件/文件夹走 FileAttachment 卡片，图片走 images。
  const [pendingFiles, setPendingFiles] = useState<FileAttachment[]>([]);
  const [pendingImages, setPendingImages] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  /** Message being quoted (引用) — encoded into the content on send. */
  const [replyTo, setReplyTo] = useState<DmQuote | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const seqRef = useRef(0);
  const messagesRef = useRef<DirectMessageItem[]>([]);
  messagesRef.current = messages;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(
    async (silent = false) => {
      if (!contactName) return;
      const seq = (seqRef.current += 1);
      if (!silent) setLoading(true);
      try {
        // 只取最新一页；更早的按需往前翻（长线程不再一次性拉全）。
        const list = await directMessageAPI.listThread(contactName, undefined, { limit: PAGE_SIZE });
        if (seq !== seqRef.current) return;
        // 后端按时间倒序返回；气泡从上到下要正序。
        setMessages([...list].sort((a, b) => a.timestamp - b.timestamp));
        setHasMore(list.length >= PAGE_SIZE);
        setError(false);
      } catch {
        if (seq === seqRef.current) setError(true);
      } finally {
        if (seq === seqRef.current && !silent) setLoading(false);
      }
    },
    [contactName],
  );

  /** 往前取一页更早的私信，并把滚动位置固定在原处（不跳）。 */
  const loadOlder = useCallback(async () => {
    if (!contactName || loadingOlder || !hasMore) return;
    setLoadingOlder(true);
    const el = scrollerRef.current;
    const prevHeight = el?.scrollHeight ?? 0;
    try {
      const older = await directMessageAPI.listThread(contactName, undefined, {
        limit: PAGE_SIZE,
        offset: messagesRef.current.length,
      });
      const seen = new Set(messagesRef.current.map((m) => m.id));
      const add = older.filter((m) => !seen.has(m.id));
      setHasMore(older.length >= PAGE_SIZE);
      if (add.length) setMessages((prev) => [...add].sort((a, b) => a.timestamp - b.timestamp).concat(prev));
      requestAnimationFrame(() => {
        if (el) el.scrollTop = el.scrollHeight - prevHeight;
      });
    } catch {
      /* 保留已渲染的内容 */
    } finally {
      setLoadingOlder(false);
    }
  }, [contactName, hasMore, loadingOlder]);

  useEffect(() => {
    setMessages([]);
    setPendingFiles([]);
    setPendingImages([]);
    void load();
  }, [load]);

  // 私信本来就不走 agent-web 的 WS，但网关会把新私信作为 `websocket_message`
  // 广播出来（ChatList 也在听同一个事件）—— 用它做即时增量，轮询只当兜底。
  useEffect(() => {
    if (!contactName) return;
    const onWs = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (detail?.type !== 'new_direct_message') return;
      const data = detail.data || {};
      // 只关心这条线程：对方发来的（自己的回声由发送路径处理）。
      if (data.sender_name && data.sender_name !== contactName) return;
      void load(true);
      if (data.id) void directMessageAPI.markAsRead(String(data.id)).catch(() => undefined);
    };
    window.addEventListener('websocket_message', onWs);
    return () => window.removeEventListener('websocket_message', onWs);
  }, [contactName, load]);

  // 兜底轮询：漏掉 WS 事件时仍能把会话补上。
  useEffect(() => {
    if (!contactName) return;
    const timer = window.setInterval(() => void load(true), POLL_MS);
    return () => window.clearInterval(timer);
  }, [contactName, load]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [messages.length]);

  /** 上传选中的文件/图片；文件夹走一次性打包上传。 */
  const uploadSelection = useCallback(
    async (files: File[], kind: 'file' | 'image' | 'folder') => {
      if (!files.length) return;
      setUploading(true);
      try {
        if (kind === 'folder') {
          const res = await uploadAPI.uploadFolder(files as unknown as FileList);
          const url = String((res as { url?: string })?.url || '');
          if (url) {
            setPendingFiles((prev) => [
              ...prev,
              {
                name: String((res as { name?: string })?.name || files[0]?.webkitRelativePath || 'folder'),
                size: String((res as { size?: string })?.size || ''),
                url,
                type: 'file',
              },
            ]);
          }
          return;
        }
        const nextFiles: FileAttachment[] = [];
        const nextImages: string[] = [];
        for (const f of files) {
          const res = await uploadAPI.uploadFile(f);
          const url = String((res as { url?: string })?.url || '');
          if (!url) continue;
          const name = String((res as { name?: string })?.name || f.name);
          const size = String((res as { size?: string })?.size || '');
          if (kind === 'image' || f.type.startsWith('image/')) nextImages.push(url);
          else nextFiles.push({ name, size, url, type: 'file' });
        }
        if (nextFiles.length) setPendingFiles((prev) => [...prev, ...nextFiles]);
        if (nextImages.length) setPendingImages((prev) => [...prev, ...nextImages]);
      } catch {
        setError(true);
      } finally {
        setUploading(false);
      }
    },
    [],
  );

  const send = useCallback(async () => {
    const text = draft.trim();
    const attachments: DmAttachment[] = [
      ...pendingFiles.map((f) => ({
        url: f.url || '',
        type: f.type === 'voice' || f.type === 'audio' ? f.type : 'file',
        name: f.name,
        size: f.size,
      })),
      ...pendingImages.map((url) => ({ url, type: 'image', name: '', size: '' })),
    ];
    // A quote rides in the message content as a marker: direct_messages has no
    // reply column, so there is nothing to migrate and the agent reads the
    // quoted text as ordinary context.
    const content = replyTo ? `${encodeDmQuote(replyTo)}${text}` : text;
    if ((!content && attachments.length === 0) || sending || uploading) return;
    setSending(true);
    try {
      await directMessageAPI.sendDirectMessage(
        contactName,
        '',
        content,
        attachments.length ? attachments : undefined,
      );
      setDraft('');
      setReplyTo(null);
      setPendingFiles([]);
      setPendingImages([]);
      await load(true);
    } catch {
      setError(true);
    } finally {
      setSending(false);
    }
  }, [contactName, draft, load, pendingFiles, pendingImages, replyTo, sending, uploading]);

  /** Start quoting a message (the reply button on a bubble calls this). */
  const handleReplyStart = useCallback(
    (m: ChatMessage) => {
      setReplyTo({
        id: m.message_id,
        name: m.role === 'user' ? currentUser?.name || contactLabel : contactLabel,
        text: (m.content || '').slice(0, DM_QUOTE_MAX),
      });
    },
    [contactLabel, currentUser],
  );

  const bubbles = useMemo(
    () =>
      messages.map((m) => {
        const mine = m.is_sender;
        const { quote, body } = parseDmQuote(m.content);
        const raw = Array.isArray(m.attachments) ? (m.attachments as DmAttachment[]) : [];
        const images = raw.filter(isImageAttachment).map((a) => a.url);
        const files: FileAttachment[] = raw
          .filter((a) => !isImageAttachment(a))
          .map((a) => ({ name: a.name || a.url, size: a.size || '', url: a.url, type: 'file' as const }));
        const message: ChatMessage = {
          role: mine ? 'user' : 'assistant',
          content: body,
          timestamp: new Date(m.timestamp).toISOString(),
          message_id: m.id,
          ...(quote ? { quote } : {}),
          ...(images.length ? { images } : {}),
          ...(files.length ? { attachments: files } : {}),
        };
        return { id: m.id, mine, message };
      }),
    [messages],
  );

  const pendingCount = pendingFiles.length + pendingImages.length;

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
        {onOpenDetail ? (
          <button
            type="button"
            onClick={onOpenDetail}
            className="shrink-0 rounded-lg px-2 py-1 text-[12px] text-textMuted hover:bg-primary/10 hover:text-textMain"
            title={t('aiChat.chat.detailHint')}
          >
            {t('aiChat.chat.detail')}
          </button>
        ) : null}
      </div>

      <div
        ref={scrollerRef}
        onScroll={() => {
          const el = scrollerRef.current;
          if (el && el.scrollTop <= 40) void loadOlder();
        }}
        className="flex-1 min-h-0 overflow-y-auto px-3 sm:px-4 py-3"
      >
        {hasMore || loadingOlder ? (
          <div className="pb-2 text-center text-[11px] text-textMuted">
            {loadingOlder ? t('common.loading') : t('aiChat.chat.loadEarlier')}
          </div>
        ) : null}
        {loading && messages.length === 0 ? (
          <div className="h-40 flex items-center justify-center">
            <OpenSquadLoader size={28} />
          </div>
        ) : error && messages.length === 0 ? (
          <div className="px-2 py-3 text-[12px] text-rose-500">{t('aiChat.chat.dmLoadFailed')}</div>
        ) : messages.length === 0 ? (
          <div className="px-2 py-3 text-[12px] text-textMuted">{t('aiChat.chat.dmEmpty')}</div>
        ) : (
          bubbles.map((b) => {
            // Agent-sent window cards arrive over the DM channel too.
            const windowCard = parseWindowCard(b.message.content);
            if (windowCard) {
              return (
                <div key={b.id} className={`mb-2 flex ${b.mine ? 'justify-end' : 'justify-start'}`}>
                  <div className="max-w-[85%]">
                    <WindowCard payload={windowCard} />
                  </div>
                </div>
              );
            }
            // Collaboration cards arrive over the DM channel too — render the
            // same clickable card instead of a plain bubble.
            const collabTask = parseCollabTask(b.message.content);
            if (collabTask) {
              return (
                <div key={b.id} className={`mb-2 flex ${b.mine ? 'justify-end' : 'justify-start'}`}>
                  <div className="max-w-[85%]">
                    <CollabTaskCard payload={collabTask} onOpen={openCollabTaskWindow} />
                  </div>
                </div>
              );
            }
            return (
              <MessageBubble
                key={b.id}
                message={b.message}
                variant="messenger"
                senderName={b.mine ? (currentUser?.name || undefined) : contactLabel}
                senderAvatar={b.mine ? (currentUser?.avatar ?? null) : (contactAvatar ?? null)}
                agentId={contactName}
                onReply={handleReplyStart}
              />
            );
          })
        )}
        <div ref={endRef} />
      </div>

      {pendingCount > 0 || uploading ? (
        <div className="shrink-0 px-3 pt-2 flex flex-wrap items-center gap-1.5" data-testid="dm-pending-attachments">
          {pendingFiles.map((f) => (
            <span
              key={`pf-${f.url}`}
              className="inline-flex max-w-[220px] items-center gap-1 rounded-lg border border-border bg-bgLight px-2 py-1 text-[11px] text-textMain"
            >
              <Paperclip size={11} className="shrink-0 text-textMuted" />
              <span className="min-w-0 truncate">{f.name}</span>
              <button
                type="button"
                className="shrink-0 p-0 border-0 bg-transparent cursor-pointer text-textMuted hover:text-rose-500"
                onClick={() => setPendingFiles((prev) => prev.filter((x) => x.url !== f.url))}
                aria-label={t('common.delete')}
              >
                <X size={11} />
              </button>
            </span>
          ))}
          {pendingImages.map((url) => (
            <span key={`pi-${url}`} className="relative inline-block">
              <img src={url} alt="" className="h-12 w-12 rounded-lg object-cover border border-border" />
              <button
                type="button"
                className="absolute -top-1.5 -right-1.5 rounded-full bg-black/60 p-0.5 text-white cursor-pointer"
                onClick={() => setPendingImages((prev) => prev.filter((x) => x !== url))}
                aria-label={t('common.delete')}
              >
                <X size={10} />
              </button>
            </span>
          ))}
          {uploading ? <OpenSquadLoader size={14} /> : null}
        </div>
      ) : null}

      {replyTo ? (
        <div
          className="shrink-0 bg-bgLight px-3 py-1.5 flex items-center gap-2"
          data-testid="dm-reply-banner"
        >
          <Reply size={12} className="shrink-0 text-textMuted" />
          <span className="min-w-0 flex-1 truncate text-[11px] text-textMuted">
            {t('chat.replyingTo', { name: replyTo.name })} · {replyTo.text}
          </span>
          <button
            type="button"
            onClick={() => setReplyTo(null)}
            className="shrink-0 text-textMuted hover:text-textMain"
            aria-label={t('common.cancel')}
          >
            <X size={12} />
          </button>
        </div>
      ) : null}

      <div className="shrink-0 border-t border-border px-3 py-2 flex items-end gap-2">
        <div className="relative shrink-0">
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            disabled={uploading}
            className="h-9 w-9 rounded-full flex items-center justify-center text-textMuted hover:text-textMain hover:bg-primary/10 disabled:opacity-40"
            title={t('aiChat.attach.trigger')}
            aria-label={t('aiChat.attach.trigger')}
          >
            <Paperclip size={17} strokeWidth={1.75} />
          </button>
          {menuOpen ? (
            <div
              className="absolute bottom-11 left-0 z-30 min-w-[148px] rounded-xl border border-border bg-panel py-1 shadow-lg text-[12px] text-textMain"
              data-testid="dm-attach-menu"
            >
              <button
                type="button"
                className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-primary/10"
                onClick={() => {
                  setMenuOpen(false);
                  fileInputRef.current?.click();
                }}
              >
                <Paperclip size={13} className="text-textMuted" />
                {t('aiChat.attach.uploadFiles')}
              </button>
              <button
                type="button"
                className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-primary/10"
                onClick={() => {
                  setMenuOpen(false);
                  folderInputRef.current?.click();
                }}
              >
                <FolderUp size={13} className="text-textMuted" />
                {t('aiChat.attach.uploadFolder')}
              </button>
              <button
                type="button"
                className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-primary/10"
                onClick={() => {
                  setMenuOpen(false);
                  imageInputRef.current?.click();
                }}
              >
                <ImageIcon size={13} className="text-textMuted" />
                {t('aiChat.attach.uploadImages')}
              </button>
            </div>
          ) : null}
        </div>

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
          disabled={sending || uploading || (!draft.trim() && pendingCount === 0)}
          className="h-9 w-9 shrink-0 rounded-full bg-primary text-white flex items-center justify-center disabled:opacity-40"
          title={t('aiChat.chat.send')}
          aria-label={t('aiChat.chat.send')}
        >
          {sending ? <OpenSquadLoader size={14} /> : <Send size={15} />}
        </button>
      </div>

      {/* 三个隐藏 input 承载 + 菜单的三个入口（文件 / 文件夹 / 图片）。 */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          e.target.value = '';
          void uploadSelection(files, 'file');
        }}
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        className="hidden"
        {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          e.target.value = '';
          void uploadSelection(files, 'folder');
        }}
      />
      <input
        ref={imageInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          e.target.value = '';
          void uploadSelection(files, 'image');
        }}
      />
    </div>
  );
};

export default DirectChatWindow;
