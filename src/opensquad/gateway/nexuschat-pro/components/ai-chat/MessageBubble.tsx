/**
 * MessageBubble - renders a single chat message (user or assistant)
 *
 * User messages: right-aligned, primary-tinted background
 * Assistant messages: left-aligned, panel background, rendered as Markdown
 * Supports image attachments displayed inline.
 * Supports file attachments displayed as cards (structured or parsed from text).
 */
import React, { useMemo } from 'react';
import { User, Bot, Copy, Check, FileText } from 'lucide-react';
import { marked } from 'marked';
import { SERVER_BASE_URL } from '../../services/api';
import { useTranslation } from 'react-i18next';

/** Structured file attachment on a ChatMessage */
export interface FileAttachment {
  name: string;
  size: string;
  path?: string;
  url?: string;          // model output audio playback URL
  type?: 'file' | 'audio' | 'video';
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  type?: string;
  /** Stable backend id when available (preferred for merge/reconcile). */
  message_id?: string;
  timestamp?: string;
  images?: string[];
  /** Structured file attachments (set at send time) */
  attachments?: FileAttachment[];
  /** Model-generated audio outputs */
  output_audio?: Array<{ url: string; mime: string }>;
  /** Model-generated image outputs */
  output_images?: string[];
  /** Complex-task final report — UI folds prior agent process when set. */
  end_task?: boolean;
}

interface MessageBubbleProps {
  message: ChatMessage;
  isStreaming?: boolean;
  /** Display name shown above the bubble */
  senderName?: string;
  /** Avatar URL (absolute or relative). Falls back to icon when absent. */
  senderAvatar?: string | null;
  /** classic = chat bubbles; solo = document-stream (Codex / Cursor Agent style) */
  variant?: 'classic' | 'solo';
  /** DOM id for Solo user-message nav jump targets */
  anchorId?: string;
}

/** Resolve an avatar URL. Prefer same-origin relative paths for /uploads. */
function resolveAvatarUrl(avatar: string): string {
  if (avatar.startsWith('data:') || avatar.startsWith('blob:')) {
    return avatar;
  }
  if (avatar.startsWith('http://') || avatar.startsWith('https://')) {
    try {
      const u = new URL(avatar);
      if (u.pathname.startsWith('/uploads/')) {
        return `${u.pathname}${u.search}`;
      }
    } catch {
      // keep absolute
    }
    return avatar;
  }
  return avatar.startsWith('/') ? avatar : `/${avatar}`;
}


// Pattern to match:
// [File: filename (size) path=... type=audio|video|file]
// [File: filename (size) type=file](/uploads/xxx)
const FILE_PATTERN = /\[File:\s*(.+?)\s*\(([^)]+)\)(?:\s*path=([^\]\n]+))?(?:\s*type=(audio|video|voice|file))?\](?:\(([^)\n]+)\))?/g;
// Markdown file link: [name.ext](/uploads/xxx.ext)
const MARKDOWN_UPLOAD_LINK_PATTERN = /\[([^\]]+?)\]\((\/uploads\/[^)\s]+)\)/g;
// Pattern to match assistant plain text style:
// 1) "name.ext 文件 (/uploads/xxx.ext)"
// 2) "name.ext 文件" (without path/url) — must look like a real filename so
//    markdown table headers like "| 文件 | 大小 |" are NOT treated as attachments.
const ASSISTANT_UPLOAD_WITH_PATH_PATTERN = /([^\n|]+?\.[A-Za-z0-9]{1,16})\s+文件\s*\(([^\s)]+)\)/g;
const ASSISTANT_UPLOAD_NAME_ONLY_PATTERN = /(?:^|\n)\s*([A-Za-z0-9._\-()+\u4e00-\u9fff]+?\.[A-Za-z0-9]{1,16})\s+文件(?:\s|$|[。．.！!？?,，、])/gm;
// Two-line fallback often seen in pushed messages:
// line1: "filename.ext"
// line2: "(/uploads/xxxx.ext)"
const ASSISTANT_NAME_URL_TWO_LINE_PATTERN = /([^\n()|]+?\.[A-Za-z0-9]{1,16})\n+\((\/uploads\/[^\s)]+|https?:\/\/[^\s)]+)\)/g;
const UPLOAD_URL_IN_PARENS_PATTERN = /\((\/uploads\/[^\s)]+)\)/g;

/** Reject markdown-table junk / empty labels falsely parsed as file cards. */
function isPlausibleFileAttachmentName(name: string): boolean {
  const n = (name || '').trim();
  if (!n || n.length > 240) return false;
  if (/^[|\-`:.\s]+$/.test(n)) return false;
  if (n === '文件' || n === 'file' || n === 'FILE') return false;
  // Table cells often leave a lone pipe or "文件" fragment.
  if (n.includes('|')) return false;
  return true;
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({
  message,
  isStreaming,
  senderName,
  senderAvatar,
  variant = 'classic',
  anchorId,
}) => {
  const { t } = useTranslation();
  const [copied, setCopied] = React.useState(false);
  const [avatarError, setAvatarError] = React.useState(false);
  const isUser = message.role === 'user';
  const isSolo = variant === 'solo';

  // Parse file attachments from message text (for historical messages loaded
  // from disk that don't have structured attachments).
  const { displayContent, fileAttachments } = useMemo(() => {
    const atts: FileAttachment[] = message.attachments ? [...message.attachments] : [];
    let content = message.content;

    // Extract [File: ...] patterns and convert to structured attachments
    // for BOTH user and assistant messages (important for session replay fallback).
    const normalizeUrl = (raw: string): string => {
      if (!raw) return '';
      if (raw.startsWith('http')) return raw;
      return raw.startsWith('/') ? raw : `/uploads/${raw.split(/[/\\]/).pop()}`;
    };

    const matches = [...content.matchAll(FILE_PATTERN)];
    if (matches.length > 0 && atts.length === 0) {
      for (const m of matches) {
        const name = (m[1] || '').trim();
        if (!isPlausibleFileAttachmentName(name)) continue;
        const size = (m[2] || '').trim();
        const rawPathOrUrl = (m[3] || m[5] || '').trim();
        const kind = (m[4] as 'audio' | 'video' | 'file' | undefined) || 'file';
        const normalizedUrl = rawPathOrUrl ? normalizeUrl(rawPathOrUrl) : undefined;
        atts.push({ name, size, type: kind, url: normalizedUrl, path: rawPathOrUrl || undefined });
      }
    }

    // Markdown link fallback: [file.py](/uploads/xxx.py)
    const mdLinks = [...content.matchAll(MARKDOWN_UPLOAD_LINK_PATTERN)];
    for (const m of mdLinks) {
      const name = (m[1] || '').trim();
      const url = normalizeUrl((m[2] || '').trim());
      if (!name || !url || !isPlausibleFileAttachmentName(name)) continue;
      const exists = atts.some(a => (a.url && a.url === url) || a.name === name);
      if (!exists) {
        const lower = name.toLowerCase();
        const isVideo = /\.(mp4|webm|mov|avi|mkv)$/.test(lower);
        const isAudio = /\.(mp3|wav|ogg|m4a|flac|aac)$/.test(lower);
        atts.push({ name, size: '', url, type: isVideo ? 'video' : isAudio ? 'audio' : 'file' });
      }
    }

    // Also parse assistant plain text fallback like:
    // "modify_ports.py 文件 (/uploads/f69f99c9.py)" or "modify_ports.py 文件"
    const uploadMatches = [...content.matchAll(ASSISTANT_UPLOAD_WITH_PATH_PATTERN)];
    if (uploadMatches.length > 0) {
      for (const m of uploadMatches) {
        const name = (m[1] || '').trim();
        const rawUrl = (m[2] || '').trim();
        if (!name || !isPlausibleFileAttachmentName(name)) continue;
        const url = rawUrl ? normalizeUrl(rawUrl) : '';
        const exists = atts.some(a => (url && a.url && a.url === url) || a.name === name);
        if (!exists) {
          const lower = name.toLowerCase();
          const isVideo = /\.(mp4|webm|mov|avi|mkv)$/.test(lower);
          const isAudio = /\.(mp3|wav|ogg|m4a|flac|aac)$/.test(lower);
          atts.push({
            name,
            size: '',
            url: url || undefined,
            type: isVideo ? 'video' : isAudio ? 'audio' : 'file',
          });
        }
      }
    }

    // Two-line fallback parse: "name" + "(/uploads/...)"
    const twoLineMatches = [...content.matchAll(ASSISTANT_NAME_URL_TWO_LINE_PATTERN)];
    if (twoLineMatches.length > 0) {
      for (const m of twoLineMatches) {
        const name = (m[1] || '').trim();
        const rawUrl = (m[2] || '').trim();
        if (!name || !rawUrl || !isPlausibleFileAttachmentName(name)) continue;
        const url = rawUrl.startsWith('http') ? rawUrl : rawUrl.startsWith('/') ? rawUrl : `/uploads/${rawUrl.split(/[/\\]/).pop()}`;
        const exists = atts.some(a => (a.url && a.url === url) || a.name === name);
        if (!exists) {
          const lower = name.toLowerCase();
          const isVideo = /\.(mp4|webm|mov|avi|mkv)$/.test(lower);
          const isAudio = /\.(mp3|wav|ogg|m4a|flac|aac)$/.test(lower);
          atts.push({
            name,
            size: '',
            url,
            type: isVideo ? 'video' : isAudio ? 'audio' : 'file',
          });
        }
      }
    }

    // Fallback parse: any (/uploads/xxx) line can backfill URL for same-name attachment
    const firstUploadUrl = content.match(UPLOAD_URL_IN_PARENS_PATTERN)?.[0]?.replace(/^\(|\)$/g, '') || '';
    if (firstUploadUrl) {
      for (let i = 0; i < atts.length; i++) {
        if (!atts[i].url) {
          atts[i] = { ...atts[i], url: firstUploadUrl };
          break;
        }
      }
    }

    // Fallback parse: name-only lines (no path) -> still render as file card (non-downloadable)
    const nameOnlyMatches = [...content.matchAll(ASSISTANT_UPLOAD_NAME_ONLY_PATTERN)];
    if (nameOnlyMatches.length > 0) {
      for (const m of nameOnlyMatches) {
        const name = (m[1] || '').trim();
        if (!name || !isPlausibleFileAttachmentName(name)) continue;
        const exists = atts.some(a => a.name === name);
        if (!exists) {
          const lower = name.toLowerCase();
          const isVideo = /\.(mp4|webm|mov|avi|mkv)$/.test(lower);
          const isAudio = /\.(mp3|wav|ogg|m4a|flac|aac)$/.test(lower);
          atts.push({
            name,
            size: '',
            type: isVideo ? 'video' : isAudio ? 'audio' : 'file',
          });
        }
      }
    }

    // Drop nameless / table-junk attachments (e.g. name="|" from "| 文件 |" headers).
    const cleanedAtts = atts.filter((a) => isPlausibleFileAttachmentName(a.name));

    // Remove parsed file lines from display text
    content = content
      .replace(FILE_PATTERN, '')
      .replace(MARKDOWN_UPLOAD_LINK_PATTERN, '')
      .replace(ASSISTANT_UPLOAD_WITH_PATH_PATTERN, '')
      .replace(ASSISTANT_NAME_URL_TWO_LINE_PATTERN, '')
      .replace(ASSISTANT_UPLOAD_NAME_ONLY_PATTERN, '')
      .replace(UPLOAD_URL_IN_PARENS_PATTERN, '')
      .replace(/\n{2,}$/g, '')
      .trim();

    return { displayContent: content, fileAttachments: cleanedAtts };
  }, [message.content, message.attachments]);

  const renderedHtml = useMemo(() => {
    if (isUser) return '';
    try {
      // Strip <title>...</title> tags used for session naming
      let content = displayContent.replace(/<title>.*?<\/title>/gs, '').trim();
      if (!content) return '';
      return marked.parse(content, { breaks: true }) as string;
    } catch {
      return displayContent;
    }
  }, [displayContent, isUser]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* ignore */ }
  };

  const handleCopyPath = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch { /* ignore */ }
  };

  const handleDownload = async (fileUrl: string, fileName: string) => {
    try {
      // Route cross-origin URLs through the same-origin download API so the
      // Blob fetch always succeeds (avoids CORS + cross-origin a.download limitation).
      let fetchUrl = fileUrl;
      if (fileUrl.startsWith('http') && !fileUrl.startsWith(window.location.origin)) {
        const urlObj = new URL(fileUrl);
        fetchUrl = `/api/ai-web/download-file?path=${encodeURIComponent(urlObj.pathname)}`;
      }
      const resp = await fetch(fetchUrl, { credentials: 'include' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = fileName || 'download';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch {
      // Pure fallback — same-origin a.download still works here.
      const a = document.createElement('a');
      // For cross-origin URLs, force navigation through download endpoint
      const fallbackUrl = (fileUrl.startsWith('http') && !fileUrl.startsWith(window.location.origin))
        ? `/api/ai-web/download-file?path=${encodeURIComponent(new URL(fileUrl).pathname)}`
        : fileUrl;
      a.href = fallbackUrl;
      a.download = fileName || 'download';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
  };

  const renderFileCard = (att: FileAttachment, key: string) => {
    const fileUrl = att.url
      ? (att.url.startsWith('http') ? att.url : `${SERVER_BASE_URL}${att.url.startsWith('/') ? att.url : `/${att.url}`}`)
      : undefined;
    const copyTarget = att.path || att.url || att.name;
    const canDownload = !!fileUrl;
    const meta = [att.type ? att.type.toUpperCase() : '', att.size].filter(Boolean).join(' · ');

    return (
      <button
        key={key}
        type="button"
        onClick={() => {
          if (canDownload && fileUrl) void handleDownload(fileUrl, att.name);
          else if (copyTarget) void handleCopyPath(copyTarget);
        }}
        title={canDownload ? t('chat.downloadFile') : (copyTarget ? `Copy path: ${copyTarget}` : att.name)}
        className={`flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-bgLight max-w-[260px] text-left transition-colors ${
          canDownload || copyTarget
            ? 'hover:bg-bgPanel cursor-pointer'
            : 'cursor-default opacity-70'
        }`}
      >
        <FileText size={16} className="text-textMuted flex-shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-xs text-textMain truncate">{att.name}</p>
          {meta ? (
            <p className="text-[10px] text-textMuted">{meta}</p>
          ) : !canDownload ? (
            <p className="text-[10px] text-textMuted">Click to copy path</p>
          ) : null}
        </div>
      </button>
    );
  };

  if (message.role === 'system') {
    const isContextSummary = message.type === 'context_summary'
      || message.content.startsWith('[Context Compression Summary]');
    if (isContextSummary) {
      const summaryHtml = (() => {
        try {
          return marked.parse(message.content, { breaks: true }) as string;
        } catch {
          return message.content;
        }
      })();
      return (
        <div className="flex justify-start my-3">
          <div className="max-w-[85%] sm:max-w-[80%]">
            <div className="text-[11px] font-medium text-textMuted mb-1">Context Summary</div>
            <div className="rounded-xl px-3 py-2 sm:px-3.5 sm:py-2.5 text-sm leading-relaxed overflow-hidden bg-amber-50/70 text-amber-900 border border-amber-200">
              <div dangerouslySetInnerHTML={{ __html: summaryHtml }} />
            </div>
          </div>
        </div>
      );
    }
    return (
      <div className="flex justify-center my-2">
        <div className="text-xs text-textMuted bg-bgLight px-3 py-1 rounded-full">
          {message.content}
        </div>
      </div>
    );
  }

  // Resolved avatar URL (null when absent or broken)
  const resolvedAvatar =
    senderAvatar && !avatarError ? resolveAvatarUrl(senderAvatar) : null;

  const label = senderName || (isUser ? 'You' : 'Agent');

  const mediaAndBody = (
    <>
      {message.images && message.images.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-2">
          {message.images.map((img, i) => {
            const src = img.startsWith('http')
              ? img
              : img.startsWith('/')
                ? `${SERVER_BASE_URL}${img}`
                : `${SERVER_BASE_URL}/uploads/${img.split(/[/\\]/).pop()}`;
            return (
              <img
                key={i}
                src={src}
                alt=""
                className="max-w-[200px] max-h-[200px] rounded-lg object-cover cursor-pointer"
                onClick={() => window.open(src, '_blank')}
              />
            );
          })}
        </div>
      )}

      {isUser ? (
        <>
          {displayContent && (
            <div className="whitespace-pre-wrap break-words">{displayContent}</div>
          )}
          {fileAttachments.length > 0 && (
            <div className={`flex flex-wrap gap-2 ${displayContent ? 'mt-2' : ''}`}>
              {fileAttachments.map((att, i) => renderFileCard(att, `u-att-${i}`))}
            </div>
          )}
        </>
      ) : (
        <>
          <div
            className="prose prose-sm prose-invert max-w-none break-words overflow-x-auto ai-markdown"
            dangerouslySetInnerHTML={{ __html: renderedHtml }}
          />
          {fileAttachments.length > 0 && (
            <div className={`flex flex-wrap gap-2 ${displayContent ? 'mt-2' : ''}`}>
              {fileAttachments.map((att, i) => {
                if (att.type === 'video' && att.url) {
                  const videoSrc = att.url.startsWith('http') ? att.url : att.url.startsWith('/') ? att.url : `/uploads/${att.url.split(/[/\\]/).pop()}`;
                  return (
                    <div key={`att-${i}`} className="rounded-lg overflow-hidden max-w-[280px]">
                      <video
                        src={`${SERVER_BASE_URL}${videoSrc.startsWith('/') ? videoSrc : `/${videoSrc}`}`}
                        controls
                        className="max-w-full max-h-48 rounded-lg"
                        preload="metadata"
                      />
                      <p className="text-xs text-textMuted truncate mt-0.5 px-0.5">{att.name}</p>
                    </div>
                  );
                }
                if ((att.type === 'audio' || att.type === 'voice') && att.url) {
                  const audioSrc = att.url.startsWith('http') ? att.url : `${SERVER_BASE_URL}${att.url.startsWith('/') ? att.url : `/${att.url}`}`;
                  return (
                    <div key={`att-${i}`} className="rounded-lg overflow-hidden max-w-[260px] border border-border bg-bgLight">
                      <audio
                        src={audioSrc}
                        controls
                        className="w-full h-9"
                        preload="metadata"
                      />
                      <p className="text-xs text-textMuted truncate px-2 pb-1.5">{att.name}</p>
                    </div>
                  );
                }
                return renderFileCard(att, `att-${i}`);
              })}
            </div>
          )}
          {message.output_audio && message.output_audio.length > 0 && (
            <div className="mt-2 flex flex-col gap-2">
              {message.output_audio.map((a, i) => (
                <audio
                  key={i}
                  controls
                  src={`${SERVER_BASE_URL}${a.url}`}
                  className="w-full max-w-xs h-8"
                />
              ))}
            </div>
          )}
          {message.output_images && message.output_images.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-2">
              {message.output_images.map((url, i) => (
                <img
                  key={i}
                  src={`${SERVER_BASE_URL}${url}`}
                  alt=""
                  className="max-w-[200px] max-h-[200px] rounded-lg object-cover cursor-pointer"
                  onClick={() => window.open(`${SERVER_BASE_URL}${url}`, '_blank')}
                />
              ))}
            </div>
          )}
        </>
      )}

      {isStreaming && (
        <span className="inline-block w-1.5 h-4 bg-primary/60 animate-pulse ml-0.5 align-middle" />
      )}
    </>
  );

  if (isSolo) {
    const domId = anchorId ? `solo-msg-${anchorId}` : undefined;
    return (
      <div
        id={domId}
        data-solo-msg-id={anchorId}
        className={`mb-5 w-full group relative scroll-mt-4 ${isStreaming ? 'ai-streaming' : ''}`}
      >
        <div className="flex items-center gap-2 mb-1.5">
          <span className={`text-[11px] font-medium ${isUser ? 'text-primary' : 'text-textMuted'}`}>
            {label}
          </span>
          {!isStreaming && message.content && (
            <button
              onClick={handleCopy}
              className="opacity-0 group-hover:opacity-100 transition-opacity text-textMuted hover:text-primary p-0.5"
              title="Copy"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
            </button>
          )}
        </div>
        {isUser ? (
          <div
            className="w-full rounded-2xl bg-chatBubbleSelf border border-border/60 shadow-[0_1px_2px_rgba(0,0,0,0.04)] px-4 py-3 text-sm leading-relaxed text-textMain"
          >
            {mediaAndBody}
          </div>
        ) : (
          <div className="text-sm leading-relaxed text-textMain w-full min-w-0">
            {mediaAndBody}
          </div>
        )}
        {message.timestamp && (
          <div className="text-[10px] text-textMuted mt-1 opacity-60">
            {new Date(message.timestamp).toLocaleTimeString()}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={`flex gap-2 mb-4 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* Avatar */}
      <div className={`w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 overflow-hidden ${
        isUser ? 'bg-primary/20' : 'bg-emerald-500/20'
      }`}>
        {resolvedAvatar ? (
          <img
            src={resolvedAvatar}
            alt={senderName || (isUser ? 'User' : 'Agent')}
            className="w-full h-full object-cover"
            onError={() => setAvatarError(true)}
          />
        ) : isUser ? (
          <User size={14} className="text-primary" />
        ) : (
          <Bot size={14} className="text-emerald-500" />
        )}
      </div>

      {/* Bubble */}
      <div className={`max-w-[85%] sm:max-w-[80%] min-w-0 group relative ${isUser ? 'items-end' : 'items-start'}`}>
        {senderName && (
          <div className={`text-[11px] font-medium text-textMuted mb-0.5 ${isUser ? 'text-right' : 'text-left'}`}>
            {senderName}
          </div>
        )}

        <div className={`rounded-xl px-3 py-2 sm:px-3.5 sm:py-2.5 text-sm leading-relaxed overflow-hidden ${
          isUser
            ? 'bg-chatBubbleSelf text-textMain rounded-tr-sm border border-border/60 shadow-sm'
            : 'bg-chatBubbleOther text-textMain rounded-tl-sm border border-border'
        } ${isStreaming ? 'ai-streaming' : ''}`}>
          {mediaAndBody}
        </div>

        {!isStreaming && message.content && (
          <button
            onClick={handleCopy}
            className={`absolute -bottom-5 opacity-0 group-hover:opacity-100 transition-opacity text-textMuted hover:text-primary p-0.5 ${isUser ? 'left-1' : 'right-1'}`}
            title="Copy"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        )}

        {message.timestamp && (
          <div className={`text-[10px] text-textMuted mt-0.5 ${isUser ? 'text-right' : 'text-left'}`}>
            {new Date(message.timestamp).toLocaleTimeString()}
          </div>
        )}
      </div>
    </div>
  );
};
