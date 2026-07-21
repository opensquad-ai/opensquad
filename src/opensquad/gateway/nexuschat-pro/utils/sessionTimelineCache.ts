/**
 * In-memory LRU cache for session timelines so tab switches paint instantly
 * when revisiting a recently viewed session.
 */
import type { TimelineEntry } from './aiChatTimeline';

const CACHE_MAX = 24;

type CacheEntry = {
  entries: TimelineEntry[];
  at: number;
};

const cache = new Map<string, CacheEntry>();

export function sessionTimelineKey(agentId: string, sessionId: string): string {
  return `${agentId}::${sessionId}`;
}

export function getCachedSessionTimeline(
  agentId: string,
  sessionId: string,
): TimelineEntry[] | null {
  const hit = cache.get(sessionTimelineKey(agentId, sessionId));
  if (!hit) return null;
  hit.at = Date.now();
  return hit.entries;
}

export function putCachedSessionTimeline(
  agentId: string,
  sessionId: string,
  entries: TimelineEntry[],
): void {
  const key = sessionTimelineKey(agentId, sessionId);
  cache.set(key, { entries, at: Date.now() });
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
