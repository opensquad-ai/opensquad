/**
 * Agent process status → dot colour + i18n label key, and the display label.
 *
 * Shared by the chat-mode contact list and the detail drawer.  The Agent
 * Switcher dialog keeps its own copy on purpose: `conversationRenderParity`
 * locks those exact literals to that file's source.
 */
import type { AdminAgent } from '../services/api';
import { resolveChatAvatar, resolveChatName } from './image';

export const AGENT_STATUS_DOT: Record<string, string> = {
  running: 'bg-green-500',
  stopped: 'bg-gray-400',
  crashed: 'bg-red-500',
  starting: 'bg-yellow-400',
  external: 'bg-blue-400',
};

export const AGENT_STATUS_LABEL_KEY: Record<string, string> = {
  running: 'agentManager.statusRunning',
  stopped: 'agentManager.statusStopped',
  crashed: 'agentManager.statusCrashed',
  starting: 'agentManager.statusStarting',
  external: 'agentManager.statusExternal',
};

/** Process is up but has not registered with the gateway yet → "starting". */
export function agentStatusOf(a: AdminAgent): string {
  if (a.process_status === 'running' && !a.ready) return 'starting';
  return a.process_status || 'stopped';
}

/** Stable key for an agent row (dir name first — it is what the APIs take). */
export function agentRowKey(a: AdminAgent): string {
  return a.dir_name || a.agent_id || a.agent_name;
}

/** Human label: configured chat name wins over the folder / id. */
export function agentLabel(a: AdminAgent): string {
  return a.agent_name || resolveChatName(a.chat_profile) || a.dir_name || a.agent_id;
}

/** Avatar URL for an agent (chat profile → local generated fallback). */
export function agentAvatar(a: AdminAgent): string | null {
  return resolveChatAvatar(a.chat_profile);
}
