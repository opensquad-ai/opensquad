/**
 * Agent activity registry — a process-local count of work still in flight.
 *
 * Two surfaces know about running work and both feed this:
 *  - `AIChatPage` reports how many sessions are mid-turn (`busy_sessions`).
 *  - `TaskPanelPage` reports how many parallel tasks are non-terminal.
 *
 * The desktop-update flow reads it right before restarting to install, so the
 * user is warned that quitting will interrupt work (warn-only — the user may
 * still proceed). Kept deliberately tiny and store-less so it can be read from
 * any component without prop drilling.
 */

interface AgentActivity {
  /** Sessions the agent reports as running a turn. */
  busySessions: number;
  /** Parallel tasks that have not reached a terminal status. */
  runningTasks: number;
}

let activity: AgentActivity = { busySessions: 0, runningTasks: 0 };

export function setBusySessionCount(n: number): void {
  activity = { ...activity, busySessions: Math.max(0, Math.floor(n) || 0) };
}

export function setRunningTaskCount(n: number): void {
  activity = { ...activity, runningTasks: Math.max(0, Math.floor(n) || 0) };
}

export function getAgentActivity(): AgentActivity {
  return activity;
}

/** Total in-flight work items — the number the pre-restart warning quotes. */
export function getActiveAgentWorkCount(): number {
  return activity.busySessions + activity.runningTasks;
}
