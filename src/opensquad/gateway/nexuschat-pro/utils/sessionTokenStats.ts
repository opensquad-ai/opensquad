/**
 * Per-session context usage: unwrap WS token_stats and lift `used` with
 * conversation tokens estimated from that session's timeline.
 *
 * Agent broadcasts are often the current working session's window (or
 * tool_defs + system only). Integer percent of a 128k/1M window then stays
 * stuck at ~10% for every tab. Mixing in this session's messages makes the
 * ring move when switching chats.
 */
import type { SoloTokenStats } from '../components/ai-chat/SoloContextFooter';
import type { TimelineEntry } from './aiChatTimeline';

export function unwrapTokenStatsPayload(msg: {
  content?: unknown;
  data?: unknown;
  sid?: string;
}): Record<string, unknown> | null {
  let data: any = msg.content ?? msg.data;
  if (!data || typeof data !== 'object') return null;
  if (
    'data' in data
    && data.data
    && typeof data.data === 'object'
    && ('used' in data.data || 'max' in data.data)
  ) {
    data = data.data;
  }
  if (!('used' in data) && !('max' in data)) return null;
  return data as Record<string, unknown>;
}

export function tokenStatsSid(
  msg: { sid?: string; content?: unknown; data?: unknown },
  data?: Record<string, unknown> | null,
): string {
  const c = msg.content;
  const d = msg.data;
  return String(
    msg.sid
    || data?.session_id
    || (typeof c === 'object' && c && ((c as any).session_id || (c as any).sid))
    || (typeof d === 'object' && d && ((d as any).session_id || (d as any).sid
      || (typeof (d as any).data === 'object' && (d as any).data?.session_id)))
    || '',
  ).trim();
}

function payloadLen(value: unknown): number {
  if (value == null) return 0;
  if (typeof value === 'string') return value.length;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value).length;
  try {
    return JSON.stringify(value).length;
  } catch {
    return 0;
  }
}

function walkTimelineChars(entries: TimelineEntry[] | undefined, acc: { n: number }): void {
  for (const e of entries || []) {
    if (e.kind === 'message') {
      acc.n += String(e.data.content || '').length;
      continue;
    }
    if (e.kind === 'workflow') {
      for (const ev of e.data.events || []) {
        acc.n += payloadLen(ev.content);
        acc.n += payloadLen(ev.result);
      }
      continue;
    }
    if (e.kind === 'archived_section' || e.kind === 'task_fold') {
      walkTimelineChars(e.data.entries, acc);
    }
  }
}

/** Rough tiktoken stand-in: UTF-16 length / 4. */
export function estimateConversationTokens(entries: TimelineEntry[] | null | undefined): number {
  if (!entries?.length) return 0;
  const acc = { n: 0 };
  walkTimelineChars(entries, acc);
  return Math.max(0, Math.round(acc.n / 4));
}

export function mergeSessionTokenStats(
  ws: SoloTokenStats | null | undefined,
  entries: TimelineEntry[] | null | undefined,
): SoloTokenStats | null {
  const conv = estimateConversationTokens(entries);
  const used = Number(ws?.used) || 0;
  const max = Number(ws?.max) || 0;
  const bd = ws?.breakdown;
  const staticPart = bd
    ? (Number(bd.system) || 0) + (Number(bd.tool_defs) || 0)
    : 0;
  const lifted = Math.max(used, staticPart + conv);
  if (!ws) {
    if (conv <= 0) return null;
    return { used: lifted, max };
  }
  if (lifted === used) return ws;
  return { ...ws, used: lifted };
}
