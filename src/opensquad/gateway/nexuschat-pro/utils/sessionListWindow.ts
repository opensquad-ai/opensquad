/**
 * Sidebar session-list paging window.
 *
 * The sidebar renders a page of the agent's session list and loads older pages
 * as the user scrolls. Several independent triggers (30s interval, window
 * focus, visibilitychange, WS session_list / history_sync / meta events, and
 * the post-switch 200ms + 900ms timers) refresh that list. The refresh used to
 * re-read page 1 only and write it over the rendered rows, which broke two
 * invariants. Both are enforced here, in pure functions.
 *
 * 1. A refresh must never shorten the rendered window.
 *    Collapsing a scrolled list invalidates the scroll position and puts the
 *    bottom sentinel back in view, which immediately fires load-more; the next
 *    refresh collapses it again. The result is an endless load / refresh loop
 *    that reads as "the list is stuck" — exactly what pagination must not do.
 *
 * 2. The paging offset counts ROWS THE SERVER RETURNED, not rows we render.
 *    The sidebar drops hidden origins (`scheduled_task`) after the fetch. Using
 *    the filtered length as the next offset under-counts and re-requests rows
 *    that were already consumed.
 */

/** Rows requested per page while scrolling. */
export const SESSION_LIST_PAGE_SIZE = 100;

/**
 * Hard cap on `limit`, mirroring the gateway route's
 * `Query(100, ge=1, le=500)` (`ai_web/routes/_main.py`). A refresh asks for the
 * whole loaded window, so this is what bounds its cost.
 */
export const SESSION_LIST_MAX_LIMIT = 500;

export type SessionRow = { id: string };

export type ScrollMetrics = {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
};

/**
 * Rows to request for a refresh that re-reads everything already on screen.
 *
 * Asking for page 1 is what collapsed the list; asking for the loaded window
 * makes a refresh idempotent with respect to what the user can see.
 */
export function refreshLimit(loadedRows: number): number {
  const loaded = Number.isFinite(loadedRows) ? Math.max(0, Math.floor(loadedRows)) : 0;
  return Math.min(SESSION_LIST_MAX_LIMIT, Math.max(SESSION_LIST_PAGE_SIZE, loaded));
}

/**
 * Offset for the next page — the running count of server rows consumed.
 *
 * `fetchedRows` must be the RAW row count of the response, before the caller
 * drops hidden origins (see invariant 2).
 */
export function advanceLoadedRows(loadedRows: number, fetchedRows: number): number {
  const loaded = Number.isFinite(loadedRows) ? Math.max(0, Math.floor(loadedRows)) : 0;
  const fetched = Number.isFinite(fetchedRows) ? Math.max(0, Math.floor(fetchedRows)) : 0;
  return loaded + fetched;
}

/**
 * Fold a refreshed page-0 prefix into the rows currently rendered.
 *
 * The fresh prefix wins on content — titles, current/primary flags and the
 * newest-first ordering are exactly what the refresh is for. Rows the refresh
 * did not cover (older pages the user already scrolled into, plus anything past
 * `SESSION_LIST_MAX_LIMIT`) are kept after it in their existing order, so the
 * rendered window never gets shorter than it was.
 */
export function mergeRefreshedPrefix<T extends SessionRow>(
  rendered: T[],
  freshPrefix: T[],
): T[] {
  if (freshPrefix.length >= rendered.length) return freshPrefix;
  const freshIds = new Set(freshPrefix.map((row) => row.id));
  const carried = rendered.filter((row) => !freshIds.has(row.id));
  return carried.length ? [...freshPrefix, ...carried] : freshPrefix;
}

/** Append `page` to `rendered`, skipping ids already present. Returns `rendered` when nothing is new. */
export function appendSessionPage<T extends SessionRow>(rendered: T[], page: T[]): T[] {
  if (!page.length) return rendered;
  const seen = new Set(rendered.map((row) => row.id));
  const merged = [...rendered];
  for (const row of page) {
    if (seen.has(row.id)) continue;
    seen.add(row.id);
    merged.push(row);
  }
  return merged.length === rendered.length ? rendered : merged;
}

/** True when the container is close enough to the end to fetch the next page. */
export function isNearListEnd(metrics: ScrollMetrics, thresholdPx = 160): boolean {
  const { scrollTop, scrollHeight, clientHeight } = metrics;
  return scrollHeight - scrollTop - clientHeight < thresholdPx;
}
