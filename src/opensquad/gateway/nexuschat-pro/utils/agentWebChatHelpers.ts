import type { AIWSMessage } from '../services/aiWebSocket';
import type { ChatMessage, FileAttachment } from '../components/ai-chat/MessageBubble';
import {
  genTimelineUID,
  rebaseTimelineUids,
  timelineHasToolEvent,
  workflowToolEventKey,
  type TimelineEntry,
} from './aiChatTimeline';

export interface UploadedFile {
  path: string;
  filename: string;
  original_name: string;
  url: string;
  size: number;
  content_type: string;
  is_image: boolean;
  is_audio?: boolean;
  is_video?: boolean;
  type?: string;
  duration?: number;
}

/** Expand any legacy archived_section folds into a flat timeline. */
export function flattenArchivedSections(entries: TimelineEntry[]): TimelineEntry[] {
  const out: TimelineEntry[] = [];
  for (const e of entries) {
    if (e.kind === 'archived_section') {
      out.push(...flattenArchivedSections(e.data.entries));
    } else {
      out.push(e);
    }
  }
  return out;
}

const MEDIA_DEBUG = false;

export function logMediaDebug(stage: string, payload: unknown) {
  if (!MEDIA_DEBUG) return;
  try {
    console.log(`[AIChatPage][media-debug] ${stage}`, payload);
    console.log(`[AIChatPage][media-debug-json] ${stage} ${JSON.stringify(payload)}`);
  } catch {
    /* ignore */
  }
}

/** Remove storage/replay-only media markers from visible bubble text. */
export function cleanDisplayContent(input: string): string {
  if (typeof input !== 'string' || !input) return input || '';
  let s = input;
  s = s.replace(/\n?\s*<image>[\s\S]*?<\/image>/gi, '');
  s = s.replace(/\n?\s*\[File:\s*.*?\]\(.*?\)/g, '');
  s = s.replace(/\n{3,}/g, '\n\n').trim();
  return s;
}

/** Extract text content from a WS message (handles various nested formats). */
export function extractWsContent(msg: AIWSMessage): string {
  const raw = msg.content ?? msg.data;
  if (typeof raw === 'string') return raw;
  if (typeof raw === 'object' && raw !== null) {
    if ('data' in raw && typeof (raw as { data?: unknown }).data === 'string') {
      return (raw as { data: string }).data;
    }
    if ('content' in raw && typeof (raw as { content?: unknown }).content === 'string') {
      return (raw as { content: string }).content;
    }
    if ('text' in raw && typeof (raw as { text?: unknown }).text === 'string') {
      return (raw as { text: string }).text;
    }
  }
  return '';
}

/** Strong identity key. Only message_id is trusted across snapshots/sessions. */
export function messageIdentityKey(msg: Partial<ChatMessage>): string {
  if (msg.message_id && String(msg.message_id).trim()) {
    return `mid:${String(msg.message_id).trim()}`;
  }
  const role = msg.role || '';
  const rawContent = typeof msg.content === 'string' ? msg.content : '';
  const normalized = rawContent
    .replace(/\[File:.*?\]\([^)]*\)/g, '')
    .replace(/<image>.*?<\/image>/gis, '')
    .trim()
    .slice(0, 200);
  if (role && normalized) {
    return `rc:${role}:${normalized}`;
  }
  return '';
}

/**
 * Soft reconnect / history_sync hydrate: keep React keys stable and skip
 * no-op replaces so mobile WS flaps do not remount the whole chat tree.
 */
export function stabilizeHydratedTimeline(prev: TimelineEntry[], next: TimelineEntry[]): TimelineEntry[] {
  const rebased = rebaseTimelineUids(prev, next);
  if (
    prev.length === rebased.length
    && prev.every((p, i) => {
      const n = rebased[i];
      if (!n || p.kind !== n.kind || p._uid !== n._uid) return false;
      if (p.kind === 'message' && n.kind === 'message') {
        return p.data.content === n.data.content && p.data.role === n.data.role;
      }
      if (p.kind === 'workflow' && n.kind === 'workflow') {
        return (
          p.data.completed === n.data.completed
          && p.data.status === n.data.status
          && (p.data.events?.length || 0) === (n.data.events?.length || 0)
          && (p.data.elapsed_ms || 0) === (n.data.elapsed_ms || 0)
        );
      }
      return true;
    })
  ) {
    return prev;
  }
  return rebased;
}

/** Merge two messages with the same identity, preferring richer media payload. */
export function mergeChatMessage(base: ChatMessage, incoming: ChatMessage): ChatMessage {
  const uniq = (arr?: string[]) => Array.from(new Set((arr || []).filter(Boolean)));
  const mergedImages = uniq([...(base.images || []), ...(incoming.images || [])]);
  const mergedOutputImages = uniq([...(base.output_images || []), ...(incoming.output_images || [])]);

  const mergeAttachments = (a?: FileAttachment[], b?: FileAttachment[]) => {
    const out: FileAttachment[] = [];
    const seen = new Set<string>();
    for (const item of [...(a || []), ...(b || [])]) {
      const k = `${item?.url || ''}|${item?.path || ''}|${item?.name || ''}`;
      if (!k || seen.has(k)) continue;
      seen.add(k);
      out.push(item);
    }
    return out;
  };

  const mergeAudio = (a?: Array<{ url: string; mime: string }>, b?: Array<{ url: string; mime: string }>) => {
    const out: Array<{ url: string; mime: string }> = [];
    const seen = new Set<string>();
    for (const item of [...(a || []), ...(b || [])]) {
      const k = `${item?.url || ''}|${item?.mime || ''}`;
      if (!item?.url || seen.has(k)) continue;
      seen.add(k);
      out.push(item);
    }
    return out;
  };

  const mergedAttachments = mergeAttachments(base.attachments, incoming.attachments);
  const mergedAudio = mergeAudio(base.output_audio, incoming.output_audio);

  return {
    ...base,
    ...incoming,
    content: (incoming.content && incoming.content.trim().length > 0) ? incoming.content : base.content,
    images: mergedImages.length > 0 ? mergedImages : undefined,
    attachments: mergedAttachments.length > 0 ? mergedAttachments : undefined,
    output_images: mergedOutputImages.length > 0 ? mergedOutputImages : undefined,
    output_audio: mergedAudio.length > 0 ? mergedAudio : undefined,
    message_id: incoming.message_id || base.message_id,
    type: incoming.type || base.type,
    timestamp: incoming.timestamp || base.timestamp,
  };
}

/**
 * Merge existing timeline into a fresh snapshot WITHOUT importing unmatched old messages.
 */
export function mergeTimelineByMessageIdentity(prev: TimelineEntry[], snapshot: TimelineEntry[]): TimelineEntry[] {
  const next = [...snapshot];
  const msgIndex = new Map<string, number>();

  next.forEach((e, i) => {
    if (e.kind === 'message') {
      msgIndex.set(messageIdentityKey(e.data as ChatMessage), i);
    }
  });

  if (msgIndex.size === 0) {
    return next;
  }

  for (const e of prev) {
    if (e.kind !== 'message') continue;
    const key = messageIdentityKey(e.data as ChatMessage);
    if (!key) continue;
    const idx = msgIndex.get(key);
    if (idx === undefined) continue;
    const merged = mergeChatMessage(next[idx].data as ChatMessage, e.data as ChatMessage);
    next[idx] = { ...next[idx], data: merged } as TimelineEntry;
  }

  return next;
}

/**
 * After context compression, the disk snapshot is authoritative.
 * Keep only in-flight / optimistic live entries that are not yet on disk.
 */
export function mergeCompressionHydration(
  prev: TimelineEntry[],
  snapshot: TimelineEntry[],
): TimelineEntry[] {
  const snapFlat = flattenArchivedSections(snapshot);
  const prevFlat = flattenArchivedSections(prev);

  const collectMessageKeys = (entries: TimelineEntry[], into: Set<string>) => {
    for (const e of entries) {
      if (e.kind !== 'message') continue;
      const k = messageIdentityKey(e.data as ChatMessage);
      if (k) into.add(k);
    }
  };

  const snapLiveKeys = new Set<string>();
  collectMessageKeys(snapFlat, snapLiveKeys);

  let next = [...snapFlat];

  for (const e of prevFlat) {
    if (e.kind === 'message') {
      const k = messageIdentityKey(e.data as ChatMessage);
      if (!k) continue;
      if (snapLiveKeys.has(k)) continue;
      next.push(e);
      snapLiveKeys.add(k);
      continue;
    }
    if (e.kind === 'workflow') {
      const wf = e.data;
      if (wf.completed) continue;
      const hasNew = wf.events.some((evt) => {
        const tk = workflowToolEventKey(evt);
        if (!tk) return evt.type === 'summary_stream';
        return !timelineHasToolEvent(next, evt);
      });
      if (!hasNew) continue;
      const filteredEvents = wf.events.filter((evt) => {
        const tk = workflowToolEventKey(evt);
        if (!tk) return evt.type === 'summary_stream';
        return !timelineHasToolEvent(next, evt);
      });
      if (filteredEvents.length === 0) continue;
      next.push({
        kind: 'workflow',
        data: { ...wf, events: filteredEvents },
        _uid: e._uid || genTimelineUID(),
      });
    }
  }

  return next;
}
