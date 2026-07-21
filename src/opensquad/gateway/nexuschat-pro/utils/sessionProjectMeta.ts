/**
 * Per-agent session project metadata (project path + pin).
 * Persisted in localStorage until backend session JSON supports these fields.
 */
import { folderLabel } from './cwdRecents';

export interface SessionProjectMeta {
  projectPath: string;
  pinned?: boolean;
  /** Soft-archive in left sidebar (not deleted). */
  archived?: boolean;
  /** Linked workspace id from workspaceStore. */
  workspaceId?: string;
}

type AgentMetaMap = Record<string, SessionProjectMeta>;

function storageKey(agentId: string): string {
  return `solo_session_project_meta:${agentId}`;
}

export function loadSessionProjectMeta(agentId: string): AgentMetaMap {
  if (!agentId) return {};
  try {
    const raw = localStorage.getItem(storageKey(agentId));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return {};
    return parsed as AgentMetaMap;
  } catch {
    return {};
  }
}

export function saveSessionProjectMeta(
  agentId: string,
  map: AgentMetaMap,
  changedSessionId?: string,
): void {
  try {
    localStorage.setItem(storageKey(agentId), JSON.stringify(map));
  } catch {}
  try {
    window.dispatchEvent(
      new CustomEvent('solo-session-meta-changed', {
        detail: { agentId, sessionId: changedSessionId || null },
      }),
    );
  } catch {}
}

export function getSessionMeta(agentId: string, sessionId: string): SessionProjectMeta | null {
  const map = loadSessionProjectMeta(agentId);
  return map[sessionId] || null;
}

export function setSessionProjectPath(
  agentId: string,
  sessionId: string,
  projectPath: string,
): SessionProjectMeta {
  const map = loadSessionProjectMeta(agentId);
  const next: SessionProjectMeta = {
    ...(map[sessionId] || {}),
    projectPath: projectPath.trim(),
  };
  map[sessionId] = next;
  saveSessionProjectMeta(agentId, map, sessionId);
  return next;
}

export function setSessionPinned(
  agentId: string,
  sessionId: string,
  pinned: boolean,
): SessionProjectMeta {
  const map = loadSessionProjectMeta(agentId);
  const prev = map[sessionId] || { projectPath: '' };
  const next: SessionProjectMeta = { ...prev, pinned };
  map[sessionId] = next;
  saveSessionProjectMeta(agentId, map, sessionId);
  return next;
}

export function setSessionArchived(
  agentId: string,
  sessionId: string,
  archived: boolean,
): SessionProjectMeta {
  const map = loadSessionProjectMeta(agentId);
  const prev = map[sessionId] || { projectPath: '' };
  const next: SessionProjectMeta = { ...prev, archived };
  map[sessionId] = next;
  saveSessionProjectMeta(agentId, map, sessionId);
  return next;
}

export function setSessionWorkspaceId(
  agentId: string,
  sessionId: string,
  workspaceId: string,
  projectPath?: string,
): SessionProjectMeta {
  const map = loadSessionProjectMeta(agentId);
  const prev = map[sessionId] || { projectPath: '' };
  const next: SessionProjectMeta = {
    ...prev,
    workspaceId,
    projectPath: (projectPath ?? prev.projectPath) || '',
  };
  map[sessionId] = next;
  saveSessionProjectMeta(agentId, map, sessionId);
  return next;
}

export function projectFolderName(path: string | null | undefined): string {
  if (!path || !path.trim()) return 'Default';
  return folderLabel(path.trim()) || 'Default';
}

export const SESSION_META_EVENT = 'solo-session-meta-changed';
/** Bump / notify SessionSidebar to re-fetch the HTTP session list. */
export const SESSION_LIST_REFRESH_EVENT = 'solo-session-list-refresh';

export function requestSessionListRefresh(agentId: string, sessionId?: string | null): void {
  try {
    window.dispatchEvent(
      new CustomEvent(SESSION_LIST_REFRESH_EVENT, {
        detail: { agentId, sessionId: sessionId || null },
      }),
    );
  } catch {
    /* ignore */
  }
}
