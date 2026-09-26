/**
 * MessageBubble - renders a single chat message (user or assistant)
 *
 * Classic: user = right-aligned bubble; assistant = document stream (no bubble).
 * Solo: document-stream layout for both (user still in a full-width bubble).
 * Supports image attachments displayed inline.
 * Supports file attachments displayed as cards (structured or parsed from text).
 */
import React, { useMemo } from 'react';
import { Copy, Check, FileText, Volume2, Square, Undo2, Ban } from 'lucide-react';
import { SERVER_BASE_URL, agentSessionAPI } from '../../services/api';
import { useTranslation } from 'react-i18next';
import { AI_MARKDOWN_CLASS, renderFencedMarkdown } from '../../utils/fencedMarkdown';
import { useMermaidHydration } from '../../hooks/useMermaidHydration';
import { useTableCopyButtons } from '../../hooks/useTableCopyButtons';
import { VoicePlayer } from './VoicePlayer';
import { MachineUserNotice } from './MachineUserNotice';
import { parseMachineUserMessage } from '../../utils/machineUserMessage';
import { OpenSquadLoader } from '../OpenSquadLoader';
import { HoverTooltip } from '../HoverTooltip';
import { formatDuration, formatFullTimestamp, formatTokenCount, formatTokenExact } from '../../utils/usageFormat';

/** Per-round billed token usage stamped on the round's final assistant message. */
export interface MessageUsage {
  /** Prompt tokens billed across the whole round (all tool rounds included). */
  input_tokens: number;
  /** Completion tokens billed across the whole round. */
  output_tokens: number;
  total_tokens: number;
  /** Wall-clock duration of the round (user message → final reply). */
  elapsed_ms: number;
  started_ms?: number;
  ended_ms?: number;
}

/** Structured file attachment on a ChatMessage */
export interface FileAttachment {
  name: string;
  size: string;
  path?: string;
  url?: string;          // model output audio playback URL
  type?: 'file' | 'audio' | 'video' | 'voice';
  /** Voice message duration in seconds */
  duration?: number;
}

export interface ChatMessage {
  // 'tool' is never user-facing dialogue; the type allows it so defensive
  // rendering can skip it (see MessageBubble render guard).
  role: 'user' | 'assistant' | 'system' | 'tool';
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
  /** Turn was cancelled by the user (Stop) — renders the styled 取消 badge. */
  stopped?: boolean;
  /** This round's total token cost (turn_usage event), rendered as the 消耗 badge. */
  usage?: MessageUsage;
}

export interface MessageBubbleProps {
  message: ChatMessage;
  isStreaming?: boolean;
  /** Display name shown above the message */
  senderName?: string;
  /**
   * Suppress the name line entirely (do not fall back to the generic "Agent").
   *
   * 紧跟工作流组的助手回复就是这种情形：名字已经显示在工作流组上方，回复上面
   * 再来一行只是重复。传 `senderName={undefined}` 是**不够的** —— 组件会退化成
   * 兜底文案 "Agent"，于是统计行和正文之间多出一行幽灵签名。
   */
  hideSenderLabel?: boolean;
  /** Kept for API compatibility (classic no longer shows avatars). */
  senderAvatar?: string | null;
  /** classic = user right-bubble + agent document; solo = document-stream */
  variant?: 'classic' | 'solo';
  /** DOM id for Solo user-message nav jump targets */
  anchorId?: string;
  /** Agent id for TTS (voice.tts_card). When set, speak button appears next to copy. */
  agentId?: string;
  /** Allow withdrawing this user turn (files + conversation). */
  canWithdraw?: boolean;
  onWithdraw?: () => void;
}

// Pattern to match:
// [File: filename (size) path=... type=audio|video|voice|file]
// [File: filename (size) type=voice](/uploads/xxx)
// path must not include whitespace so " type=audio" is not swallowed into path.
const FILE_PATTERN = /\[File:\s*(.+?)\s*\(([^)]+)\)(?:\s*path=([^\s\]]+))?(?:\s*type=(audio|video|voice|file))?\](?:\(([^)\n]+)\))?/g;
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

/**
 * Coerce a message payload to displayable text.
 *
 * Providers and plugins occasionally deliver `null`, or a multimodal content
 * array, instead of a string. Passing that through crashes every string call
 * site (`matchAll`, `startsWith`) and makes React refuse to render an object
 * child — which blanked the entire message list.
 */
function normalizeContent(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

const MessageBubbleInner: React.FC<MessageBubbleProps> = ({
  message,
  isStreaming,
  senderName,
  hideSenderLabel,
  senderAvatar: _senderAvatar,
  variant = 'classic',
  anchorId,
  agentId,
  canWithdraw,
  onWithdraw,
}) => {
  const { t, i18n } = useTranslation();
  const [copied, setCopied] = React.useState(false);
  const [ttsState, setTtsState] = React.useState<'idle' | 'loading' | 'playing'>('idle');
  const audioRef = React.useRef<HTMLAudioElement | null>(null);
  const isUser = message.role === 'user';
  const isSolo = variant === 'solo';
  void _senderAvatar;

  // Defense in depth: non-text payloads (null, or a multimodal array) must not
  // reach `.matchAll` / `.startsWith` / React children, any of which would
  // throw and blank the whole message list. Timeline normalizes upstream too.
  const safeContent = normalizeContent(message.content);

  // Legacy/local stop marker ("[Stopped]") is never user-facing text: strip it
  // from the body and remember the turn was cancelled so the styled badge
  // renders instead (disk history persists the marker inside content).
  const hadStoppedMarker = /\[Stopped\]/i.test(safeContent);
  const isStoppedTurn = !!message.stopped || hadStoppedMarker;
  const bodyContent = isStoppedTurn
    ? safeContent.replace(/\s*\[Stopped\]\s*/gi, '\n').trim()
    : safeContent;

  // Parse file attachments from message text (for historical messages loaded
  // from disk that don't have structured attachments).
  const { displayContent, fileAttachments } = useMemo(() => {
    const atts: FileAttachment[] = message.attachments ? [...message.attachments] : [];
    let content = bodyContent;

    // Extract [File: ...] patterns and convert to structured attachments
    // for BOTH user and assistant messages (important for session replay fallback).
    const normalizeUrl = (raw: string): string => {
      if (!raw) return '';
      // Drop trailing junk accidentally captured after the path (e.g. " type=audio").
      const cleaned = raw.trim().split(/\s+/)[0] || '';
      if (!cleaned) return '';
      if (cleaned.startsWith('http')) return cleaned;
      if (cleaned.startsWith('/')) return cleaned;
      const leaf = cleaned.split(/[/\\]/).pop() || cleaned;
      return `/uploads/${leaf}`;
    };

    const matches = [...content.matchAll(FILE_PATTERN)];
    if (matches.length > 0 && atts.length === 0) {
      for (const m of matches) {
        const name = (m[1] || '').trim();
        if (!isPlausibleFileAttachmentName(name)) continue;
        const size = (m[2] || '').trim();
        const rawPathOrUrl = (m[3] || m[5] || '').trim();
        const mdUrl = (m[5] || '').trim();
        let kind = (m[4] as 'audio' | 'video' | 'voice' | 'file' | undefined) || undefined;
        if (!kind) {
          const lower = name.toLowerCase();
          if (/^voice_/i.test(name) || /\.(mp3|wav|ogg|m4a|flac|aac|webm)$/i.test(lower)) kind = 'voice';
          else if (/\.(mp4|mov|avi|mkv)$/i.test(lower)) kind = 'video';
          else kind = 'file';
        }
        // Prefer markdown (/uploads/...) URL when present; fall back to path.
        const preferred = mdUrl || rawPathOrUrl;
        const normalizedUrl = preferred ? normalizeUrl(preferred) : undefined;
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
        const isAudio = /\.(mp3|wav|ogg|m4a|flac|aac|webm)$/.test(lower) || /^voice_/i.test(name);
        const isVideo = !isAudio && /\.(mp4|webm|mov|avi|mkv)$/.test(lower);
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
          const isAudio = /\.(mp3|wav|ogg|m4a|flac|aac|webm)$/.test(lower) || /^voice_/i.test(name);
          const isVideo = !isAudio && /\.(mp4|webm|mov|avi|mkv)$/.test(lower);
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

    // Deduplicate voice/file cards that arrive from both attachments[] and files[]
    // (or from content marker re-parse) so one bubble never shows two players.
    const dedupedAtts: FileAttachment[] = [];
    for (const a of cleanedAtts) {
      const urlKey = (a.url || a.path || '').replace(/\\/g, '/').split('/').pop()?.split(/\s+/)[0] || '';
      const dup = dedupedAtts.some((b) => {
        if (a.name && b.name && a.name === b.name) return true;
        const bKey = (b.url || b.path || '').replace(/\\/g, '/').split('/').pop()?.split(/\s+/)[0] || '';
        return !!urlKey && !!bKey && urlKey === bKey;
      });
      if (!dup) dedupedAtts.push(a);
    }

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

    return { displayContent: content, fileAttachments: dedupedAtts };
  }, [bodyContent, message.attachments]);

  const renderedHtml = useMemo(() => {
    if (isUser) return '';
    try {
      // Strip <title>...</title> tags used for session naming
      let content = displayContent.replace(/<title>.*?<\/title>/gs, '').trim();
      if (!content) return '';
      return renderFencedMarkdown(content);
    } catch {
      return displayContent;
    }
  }, [displayContent, isUser]);

  const mermaidRef = useMermaidHydration(renderedHtml, !isUser);
  useTableCopyButtons(mermaidRef, renderedHtml);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(bodyContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* ignore */ }
  };

  const stopTts = React.useCallback(() => {
    const a = audioRef.current;
    if (a) {
      a.pause();
      a.src = '';
      audioRef.current = null;
    }
    setTtsState('idle');
  }, []);

  React.useEffect(() => () => stopTts(), [stopTts]);

  const handleSpeak = async () => {
    if (!agentId || !bodyContent?.trim()) return;
    if (ttsState === 'playing' || ttsState === 'loading') {
      stopTts();
      return;
    }
    setTtsState('loading');
    try {
      const res = await agentSessionAPI.synthesize(agentId, bodyContent);
      const url = res.url?.startsWith('http')
        ? res.url
        : `${SERVER_BASE_URL}${res.url?.startsWith('/') ? res.url : `/${res.url}`}`;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => setTtsState('idle');
      audio.onerror = () => setTtsState('idle');
      setTtsState('playing');
      await audio.play();
    } catch (e) {
      setTtsState('idle');
      const msg = e instanceof Error ? e.message : String(e);
      window.alert(msg || 'TTS failed');
    }
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

  const resolveMediaUrl = (raw?: string): string => {
    if (!raw) return '';
    if (raw.startsWith('http')) return raw;
    if (raw.startsWith('/')) return `${SERVER_BASE_URL}${raw}`;
    return `${SERVER_BASE_URL}/uploads/${raw.split(/[/\\]/).pop()}`;
  };

  const isVoiceLike = (att: FileAttachment) =>
    att.type === 'voice' || att.type === 'audio'
    || /\.(mp3|wav|ogg|m4a|flac|aac|webm)$/i.test(att.name || '');

  const renderAttachment = (att: FileAttachment, key: string) => {
    const mediaUrl = resolveMediaUrl(att.url || att.path);
    if (att.type === 'video' && mediaUrl && !/\bvoice_/i.test(att.name || '')) {
      return (
        <div key={key} className="rounded-lg overflow-hidden max-w-[280px]">
          <video
            src={mediaUrl}
            controls
            className="max-w-full max-h-48 rounded-lg"
            preload="metadata"
          />
          <p className="text-xs text-textMuted truncate mt-0.5 px-0.5">{att.name}</p>
        </div>
      );
    }
    if (isVoiceLike(att) && mediaUrl) {
      return (
        <div key={key}>
          <VoicePlayer url={mediaUrl} duration={att.duration || 0} />
        </div>
      );
    }
    return renderFileCard(att, key);
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

  // Defensive: tool-role messages are LLM context, never user-facing dialogue.
  // buildTimelineFromSession now skips them; this guard covers legacy cached
  // timelines and any other path that might still surface them.
  if (message.role === 'tool') {
    return null;
  }

  if (message.role === 'system') {
    const isContextSummary = message.type === 'context_summary'
      || safeContent.startsWith('[Context Compression Summary]');
    if (isContextSummary) {
      const summaryHtml = (() => {
        try {
          return renderFencedMarkdown(safeContent);
        } catch {
          return safeContent;
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
          {safeContent}
        </div>
      </div>
    );
  }

  const label = senderName || (isUser ? 'You' : 'Agent');

  // 消耗 badge — reference-UI pill ("9m 34s · 3.0M"): this round's total billed
  // tokens. Hover/click pops the input / output split directly above the pill.
  //
  // Arrow direction is load-bearing: ↓ input (prompt tokens flowing INTO the
  // model) and ↑ output (completion tokens coming back OUT). It once shipped
  // reversed (↑ input / ↓ output — the upload/download reading), so the pairing
  // is pinned by `utils/turnUsageStamp.test.ts`.
  //
  // `strategy="anchor"` is load-bearing: the default `fixed` strategy computes
  // viewport coordinates from getBoundingClientRect(), but a transformed
  // ancestor re-anchors `position: fixed` — the bubble then rendered far below
  // the trigger instead of above it.
  const usageBadge =
    !isUser && message.usage && (message.usage.total_tokens > 0 || message.usage.elapsed_ms > 0) ? (
      <HoverTooltip
        toggleOnClick
        strategy="anchor"
        variant="plain"
        text={`↓ ${t('aiChat.usage.input')} ${formatTokenExact(message.usage.input_tokens)} · ↑ ${t(
          'aiChat.usage.output',
        )} ${formatTokenExact(message.usage.output_tokens)}`}
      >
        <span
          data-testid="msg-usage-badge"
          className="text-[11px] leading-none tabular-nums text-textMuted bg-textMuted/10 rounded-full px-2 py-0.5 ml-1 cursor-pointer hover:bg-textMuted/20"
        >
          {message.usage.elapsed_ms > 0 ? formatDuration(message.usage.elapsed_ms) : ''}
          {message.usage.elapsed_ms > 0 && message.usage.total_tokens > 0 ? ' · ' : ''}
          {message.usage.total_tokens > 0 ? formatTokenCount(message.usage.total_tokens) : ''}
        </span>
      </HoverTooltip>
    ) : null;

  // Full "2026年9月14日周一 14:24:04" popover for the footer time.
  // The explicit text-[11px] is load-bearing: this bubble used to inherit its
  // size (the classic action row sits next to a text-[15px] body wrapper), so
  // the footer time rendered far larger than the reference UI.
  const timeWithPopover = message.timestamp ? (
    <HoverTooltip
      toggleOnClick
      strategy="anchor"
      variant="plain"
      text={formatFullTimestamp(message.timestamp, i18n.language)}
    >
      <span className="text-[11px] leading-none tabular-nums text-textMuted/70 cursor-default hover:text-textMuted">
        {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </span>
    </HoverTooltip>
  ) : null;

  // Machine-generated user turn (form submission / reminder / inbound group
  // message): the wire text is what the model reads, but painting it verbatim
  // filled the bubble with marker + JSON. Split it at render time so a refresh
  // shows the same notice with no extra state.
  const machine = useMemo(
    () => (isUser ? parseMachineUserMessage(displayContent) : null),
    [isUser, displayContent],
  );
  const userText = machine ? machine.text : displayContent;
  const machineOnly = !!machine?.notice && !userText.trim();

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
          {userText && (
            <div className="whitespace-pre-wrap break-words">{userText}</div>
          )}
          {machine?.notice && (
            <MachineUserNotice
              notice={machine.notice}
              className={userText ? 'mt-2' : ''}
            />
          )}
          {fileAttachments.length > 0 && (
            <div className={`flex flex-wrap gap-2 ${userText || machine?.notice ? 'mt-2' : ''}`}>
              {fileAttachments.map((att, i) => renderAttachment(att, `u-att-${i}`))}
            </div>
          )}
        </>
      ) : (
        <>
          <div
            ref={mermaidRef}
            className={AI_MARKDOWN_CLASS}
            dangerouslySetInnerHTML={{ __html: renderedHtml }}
          />
          {isStoppedTurn && !isUser && (
            <div
              data-testid="msg-stopped-badge"
              className="mt-2 inline-flex select-none items-center gap-1.5 rounded-full border border-border/70 bg-bgLight/60 px-2.5 py-[3px] text-[11px] font-medium tracking-[0.18em] text-textMuted/90"
            >
              <Ban size={11} className="opacity-60" aria-hidden />
              {t('aiChat.turnCancelled')}
            </div>
          )}
          {fileAttachments.length > 0 && (
            <div className={`flex flex-wrap gap-2 ${displayContent ? 'mt-2' : ''}`}>
              {fileAttachments.map((att, i) => renderAttachment(att, `att-${i}`))}
            </div>
          )}
          {message.output_audio && message.output_audio.length > 0 && (
            <div className="mt-2 flex flex-col gap-2">
              {message.output_audio.map((a, i) => (
                <VoicePlayer
                  key={i}
                  url={`${SERVER_BASE_URL}${a.url}`}
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
          {!machineOnly && (
            <span className={`text-[11px] font-medium ${isUser ? 'text-primary' : 'text-textMuted'}`}>
              {label}
            </span>
          )}
          {!isStreaming && (bodyContent || (isUser && canWithdraw && onWithdraw)) && (
            <div className="flex items-center gap-0.5">
              {bodyContent ? (
              <button
                onClick={handleCopy}
                className="opacity-0 group-hover:opacity-100 transition-opacity text-textMuted hover:text-primary p-0.5"
                title="Copy"
              >
                {copied ? <Check size={12} /> : <Copy size={12} />}
              </button>
              ) : null}
              {isUser && canWithdraw && onWithdraw ? (
                <button
                  type="button"
                  onClick={onWithdraw}
                  className="opacity-0 group-hover:opacity-100 transition-opacity text-textMuted hover:text-rose-500 p-0.5"
                  title={t('aiChat.restoreCheckpoint.actionTitle')}
                >
                  <Undo2 size={12} />
                </button>
              ) : null}
              {agentId && bodyContent ? (
                <button
                  onClick={() => void handleSpeak()}
                  disabled={ttsState === 'loading'}
                  className={`transition-opacity p-0.5 ${
                    ttsState !== 'idle'
                      ? 'opacity-100 text-primary'
                      : 'opacity-0 group-hover:opacity-100 text-textMuted hover:text-primary'
                  }`}
                  title={ttsState === 'playing' ? 'Stop' : 'Speak'}
                >
                  {ttsState === 'loading' ? (
                    <OpenSquadLoader size={14} />
                  ) : ttsState === 'playing' ? (
                    <Square size={12} />
                  ) : (
                    <Volume2 size={12} />
                  )}
                </button>
              ) : null}
            </div>
          )}
        </div>
        {isUser && !machineOnly ? (
          <div
            className="w-full rounded-2xl bg-chatBubbleSelf border border-border/40 shadow-[0_1px_2px_rgba(0,0,0,0.04)] px-4 py-3 text-sm leading-relaxed text-textMain"
          >
            {mediaAndBody}
          </div>
        ) : (
          <div className="text-sm leading-relaxed text-textMain w-full min-w-0">
            {mediaAndBody}
          </div>
        )}
        {(usageBadge || message.timestamp) && (
          <div className="flex items-center mt-1">
            {usageBadge}
            {message.timestamp &&
              (timeWithPopover ?? (
                <span className="text-[11px] text-textMuted/70">
                  {new Date(message.timestamp).toLocaleTimeString()}
                </span>
              ))}
          </div>
        )}
      </div>
    );
  }

  // Classic: user = right bubble; agent = document stream (no bubble)
  const actionRow = !isStreaming && (bodyContent || (isUser && canWithdraw && onWithdraw)) ? (
    <div
      className={`flex items-center gap-0.5 mt-1.5 transition-opacity ${
        ttsState !== 'idle' ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
      } ${isUser ? 'justify-end' : 'justify-start'}`}
    >
      {bodyContent ? (
      <button
        onClick={handleCopy}
        className="text-textMuted hover:text-primary p-0.5 border-0 bg-transparent cursor-pointer"
        title="Copy"
      >
        {copied ? <Check size={13} /> : <Copy size={13} />}
      </button>
      ) : null}
      {isUser && canWithdraw && onWithdraw ? (
        <button
          type="button"
          onClick={onWithdraw}
          className="text-textMuted hover:text-rose-500 p-0.5 border-0 bg-transparent cursor-pointer"
          title={t('aiChat.restoreCheckpoint.actionTitle')}
        >
          <Undo2 size={13} />
        </button>
      ) : null}
      {agentId && (
        <button
          onClick={() => void handleSpeak()}
          disabled={ttsState === 'loading'}
          className={`p-0.5 border-0 bg-transparent cursor-pointer ${
            ttsState !== 'idle' ? 'text-primary' : 'text-textMuted hover:text-primary'
          }`}
          title={ttsState === 'playing' ? 'Stop' : 'Speak'}
        >
          {ttsState === 'loading' ? (
            <OpenSquadLoader size={14} />
          ) : ttsState === 'playing' ? (
            <Square size={13} />
          ) : (
            <Volume2 size={13} />
          )}
        </button>
      )}
      {usageBadge}
      {timeWithPopover}
    </div>
  ) : message.timestamp ? (
    <div className={`text-[11px] text-textMuted/55 mt-1.5 tabular-nums ${isUser ? 'text-right' : 'text-left'}`}>
      {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
    </div>
  ) : null;

  if (isUser && !machineOnly) {
    const domId = anchorId ? `solo-msg-${anchorId}` : undefined;
    return (
      <div
        id={domId}
        data-solo-msg-id={anchorId}
        className={`mb-5 w-full flex justify-end group scroll-mt-4 ${isStreaming ? 'ai-streaming' : ''}`}
      >
        <div className="max-w-[min(85%,36rem)] min-w-0">
          <div className="rounded-2xl rounded-br-md bg-bgLight border border-border/40 px-4 py-2.5 text-sm leading-relaxed text-textMain shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
            {mediaAndBody}
          </div>
          {actionRow}
        </div>
      </div>
    );
  }

  return (
    <div className={`mb-6 w-full group ${isStreaming ? 'ai-streaming' : ''}`}>
      {!hideSenderLabel && !machineOnly && (
        <div className="text-[11px] font-medium text-textMuted/70 mb-2">
          {senderName || label}
        </div>
      )}
      <div className="text-[15px] leading-7 text-textMain w-full min-w-0">
        {mediaAndBody}
      </div>
      {actionRow}
    </div>
  );
};

function sameFileAtts(a?: FileAttachment[], b?: FileAttachment[]): boolean {
  if (a === b) return true;
  if (!a?.length && !b?.length) return true;
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].name !== b[i].name || a[i].url !== b[i].url || a[i].type !== b[i].type) return false;
  }
  return true;
}

/**
 * Compact identity of a message's billed-usage stamp (the 消耗 badge data).
 * `undefined` and a stamped value must differ.
 */
export function usageMemoKey(u?: MessageUsage): string {
  return u ? `${u.input_tokens}|${u.output_tokens}|${u.total_tokens}|${u.elapsed_ms}` : '';
}

/**
 * Prop equality for the memoized bubble.
 *
 * This comparator is the ONLY gate for in-place timeline patches: a WS
 * `turn_usage` frame stamps `message.usage` onto a row that is already mounted,
 * and a session-history re-hydrate replaces the entry with a freshly built
 * object (new identity, identical text). Miss a rendered field here and the row
 * silently keeps its stale markup forever — which is exactly how the 消耗 badge
 * went missing after the first paint.
 */
export function areMessageBubblePropsEqual(prev: MessageBubbleProps, next: MessageBubbleProps): boolean {
  return (
    prev.isStreaming === next.isStreaming
    && prev.senderName === next.senderName
    && prev.hideSenderLabel === next.hideSenderLabel
    && prev.variant === next.variant
    && prev.anchorId === next.anchorId
    && prev.agentId === next.agentId
    && prev.canWithdraw === next.canWithdraw
    // onWithdraw is often an inline lambda — ignore identity.
    && prev.message.role === next.message.role
    && normalizeContent(prev.message.content) === normalizeContent(next.message.content)
    && prev.message.message_id === next.message.message_id
    && prev.message.type === next.message.type
    && prev.message.end_task === next.message.end_task
    // Stopped badge is patched in place on Stop — must re-render.
    && prev.message.stopped === next.message.stopped
    // Footer renders the exact timestamp (full-date popover) — must be compared.
    && prev.message.timestamp === next.message.timestamp
    // 消耗 badge: stamped in place by the turn_usage WS frame / history rebuild.
    && usageMemoKey(prev.message.usage) === usageMemoKey(next.message.usage)
    && sameFileAtts(prev.message.attachments, next.message.attachments)
    && (prev.message.images?.join('\0') || '') === (next.message.images?.join('\0') || '')
    // Model-generated media is appended in place (content stays identical), so
    // these must be compared or TTS output / generated images never re-render.
    && (prev.message.output_audio?.map((a) => a.url).join('\0') || '') === (next.message.output_audio?.map((a) => a.url).join('\0') || '')
    && (prev.message.output_images?.join('\0') || '') === (next.message.output_images?.join('\0') || '')
  );
}

/** Skip re-renders when parent chat ticks (token ring, timers) but message body is unchanged. */
export const MessageBubble = React.memo(MessageBubbleInner, areMessageBubblePropsEqual);
