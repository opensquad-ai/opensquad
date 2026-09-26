/**
 * Is THIS session row running right now?
 *
 * The agent reports the running sessions explicitly (``busy_sessions``, broadcast
 * on a ~5s loop) — that per-session list is the authority.  The agent-wide flag is
 * only a stopgap for the window before the first snapshot lands, and then only for
 * the session the user is driving.
 *
 * The bug this replaces: the row used the backend's ``session.current`` flag, which
 * is the disk "current session" pointer, not "this session is running" (the sidebar
 * already refuses to trust it for the *highlight*, see ``isCurrent``).  So whenever
 * the agent was busy for ANY session, a long-finished session that happened to be
 * the current pointer kept pulsing with the working animation.
 */
export function isSessionRowBusy(opts: {
  sessionId: string;
  /** Session the user has selected in the UI */
  currentSessionId?: string | null;
  /** Agent-wide "something is running" flag */
  agentBusy?: boolean;
  /** Session ids the agent reports as running a turn */
  busySessionIds?: string[];
}): boolean {
  const { sessionId, currentSessionId, agentBusy, busySessionIds } = opts;
  if (!sessionId) return false;
  const list = busySessionIds || [];
  if (list.includes(sessionId)) return true;
  return !!agentBusy && sessionId === currentSessionId && list.length === 0;
}
