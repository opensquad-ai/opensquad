/**
 * Resolve a session's live timeline bucket without ever borrowing another
 * session's entries (e.g. focused / currentSessionId global timeline).
 *
 * Used by scheduled-task ExecWorkflowView via sessionBridge — missing bucket
 * must return null so the view hydrates strictly by exec.session_id.
 */
export function pickSessionLiveTimeline<T>(
  liveTimelinesBySession: Record<string, T[] | null | undefined>,
  sessionId: string,
): T[] | null {
  const sid = (sessionId || '').trim();
  if (!sid) return null;
  if (!Object.prototype.hasOwnProperty.call(liveTimelinesBySession, sid)) {
    return null;
  }
  const live = liveTimelinesBySession[sid];
  // An empty bucket was seeded before the first WS event — treat as missing so
  // ExecWorkflowView hydrates from disk instead of painting a blank pane.
  if (live != null && live.length === 0) return null;
  return live != null ? live : null;
}
