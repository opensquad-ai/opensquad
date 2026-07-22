/**
 * In-memory LRU cache for session timelines so tab switches paint instantly
 * when revisiting a recently viewed session.
 */
import type { TimelineEntry } from './aiChatTimeline';

const CACHE_MAX = 24;

/** Default page size for first-paint / prefetch session history. */
export const SESSION_HISTORY_PAGE_SIZE = 80;

export type SessionTimelineCacheMeta = {
  entries: TimelineEntry[];
  /** True when the full timeline is loaded (no more pages / full history). */
  complete: boolean;
  /** Number of live (non-archived) messages represented in the cache. */
  messageCount: number;
  /** Server total_messages when known (from paged API). */
  totalMessages?: number;
  at: number;
};

const cache = new Map<string, SessionTimelineCacheMeta>();

export function sessionTimelineKey(agentId: string, sessionId: string): string {
  return `${agentId}::${sessionId}`;
}

function touch(hit: SessionTimelineCacheMeta): void {
  hit.at = Date.now();
}

export function getCachedSessionTimeline(
  agentId: string,
  sessionId: string,
): TimelineEntry[] | null {
  const hit = cache.get(sessionTimelineKey(agentId, sessionId));
  if (!hit) return null;
  touch(hit);
  return hit.entries;
}

export function getCachedSessionTimelineMeta(
  agentId: string,
  sessionId: string,
): SessionTimelineCacheMeta | null {
  const hit = cache.get(sessionTimelineKey(agentId, sessionId));
  if (!hit) return null;
  touch(hit);
  return hit;
}

export type PutCachedSessionTimelineOpts = {
  complete?: boolean;
  messageCount?: number;
  totalMessages?: number;
};

export function putCachedSessionTimeline(
  agentId: string,
  sessionId: string,
  entries: TimelineEntry[],
  opts?: PutCachedSessionTimelineOpts,
): void {
  const key = sessionTimelineKey(agentId, sessionId);
  const prev = cache.get(key);
  const messageCount =
    opts?.messageCount ??
    entries.filter((e) => e.kind === 'message').length;
  const complete =
    opts?.complete ??
    (opts?.totalMessages != null
      ? messageCount >= opts.totalMessages
      : prev?.complete ?? false);
  cache.set(key, {
    entries,
    complete,
    messageCount,
    totalMessages: opts?.totalMessages ?? prev?.totalMessages,
    at: Date.now(),
  });
  if (cache.size <= CACHE_MAX) return;
  const oldest = [...cache.entries()].sort((a, b) => a[1].at - b[1].at);
  const drop = cache.size - CACHE_MAX;
  for (let i = 0; i < drop; i++) cache.delete(oldest[i][0]);
}

export function invalidateCachedSessionTimeline(
  agentId: string,
  sessionId?: string | null,
): void {
  if (sessionId) {
    cache.delete(sessionTimelineKey(agentId, sessionId));
    return;
  }
  const prefix = `${agentId}::`;
  for (const key of [...cache.keys()]) {
    if (key.startsWith(prefix)) cache.delete(key);
  }
}
