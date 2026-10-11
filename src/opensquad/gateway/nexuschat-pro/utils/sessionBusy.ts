/**
 * Is THIS session row running right now?
 *
 * Two per-session sources, both about *this* row — never an agent-wide flag:
 *
 *   1. the sessions the agent reports as running a turn (``busy_sessions``,
 *      broadcast on a ~5s loop) — the authority;
 *   2. sessions this client is streaming right now (``isStreamingBySession``) —
 *      covers the window before the snapshot lands.
 *
 * The bug this replaces (first round): the row used the backend's
 * ``session.current`` flag, which is the disk "current session" pointer, not
 * "this session is running", so whenever the agent was busy for ANY session a
 * long-finished session that happened to be the current pointer kept pulsing.
 *
 * The bug this replaces (second round, 2026-09-28): the stopgap for window (2)
 * was ``agentBusy && sessionId === currentSessionId`` — i.e. "whatever row the
 * user has selected, if the agent is busy somewhere".  Selecting a session in
 * the sidebar is not driving it, so clicking through the list repainted the
 * working animation onto every row the user touched (「点击哪个会话，哪个会话就
 * 任务流进度动画」), and a stale agent-wide flag made it stick there for good.
 * A row lights up only on evidence about that row.
 */
export function isSessionRowBusy(opts: {
  sessionId: string;
  /** Session ids the agent reports as running a turn */
  busySessionIds?: string[];
  /** Session ids this client is currently streaming */
  streamingSessionIds?: string[];
}): boolean {
  const { sessionId, busySessionIds, streamingSessionIds } = opts;
  if (!sessionId) return false;
  if ((busySessionIds || []).includes(sessionId)) return true;
  return (streamingSessionIds || []).includes(sessionId);
}

/**
 * Apply the agent's busy snapshot (sent with `connected`) to the client's own set.
 *
 * The live `busy_sessions` event only fires when the set *changes*, so a reload or a
 * reconnect could not learn that a turn was still running: the composer came back with a
 * Send arrow and the Stop button only reappeared at the next change (2026-10-10). The
 * snapshot is the authority, so it replaces the set — a turn that finished while we were
 * away must not stay red — but two things are unioned back in:
 *
 *   * sessions this client is *streaming* right now (fresh evidence beats a stale frame),
 *   * never a session the user just stopped (`stopped`), which is the same rule the live
 *     handler applies.
 */
export function mergeBusySnapshot(opts: {
  /** `busy_sessions` from the `connected` frame; null/undefined means "no snapshot" */
  snapshot?: unknown;
  /** Sessions this client is streaming right now */
  streamingSessionIds?: Iterable<string>;
  /** Sessions the user pressed Stop on — never resurrected */
  stopped?: Record<string, unknown> | null;
}): string[] | null {
  const { snapshot } = opts;
  if (!Array.isArray(snapshot)) return null;
  const stopped = opts.stopped || {};
  const ids = new Set<string>();
  for (const raw of snapshot) {
    const id = String(raw ?? '').trim();
    if (id && !stopped[id]) ids.add(id);
  }
  for (const id of opts.streamingSessionIds || []) {
    const clean = String(id ?? '').trim();
    if (clean && !stopped[clean]) ids.add(clean);
  }
  return Array.from(ids);
}
