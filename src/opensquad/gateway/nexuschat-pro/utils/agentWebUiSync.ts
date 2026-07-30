/**
 * Sync Agent Web workspace chrome + session↔project meta to the agent host.
 *
 * Browser localStorage is origin-scoped: `http://localhost:5173` and
 * `http://192.168.x.x:5173` do not share workspaces / session bindings.
 * Persisting on the Launcher agent directory fixes LAN access.
 */
import { adminAPI } from '../services/api';
import {
  loadSessionProjectMeta,
  saveSessionProjectMeta,
  SESSION_META_EVENT,
} from './sessionProjectMeta';
import {
  loadWorkspaceStoreResolved,
  saveWorkspaceStore,
  WORKSPACES_CHANGED_EVENT,
  type WorkspaceStoreSnapshot,
} from './workspaceStore';

type SyncTarget = {
  storageAgentId: string;
  serverAgentName: string;
  aliases: string[];
};

let target: SyncTarget | null = null;
let pushTimer: ReturnType<typeof setTimeout> | null = null;
let pushInFlight = false;
let pullInFlight = false;
/** Suppress push while applying a server pull (avoid echo). */
let applyingServer = false;

export function setAgentWebUiSyncTarget(
  storageAgentId: string,
  serverAgentName: string,
  aliases: Array<string | null | undefined> = [],
): void {
  const storage = (storageAgentId || '').trim();
  const server = (serverAgentName || storage).trim();
  if (!storage || !server) {
    target = null;
    return;
  }
  target = {
    storageAgentId: storage,
    serverAgentName: server,
    aliases: aliases.map((a) => (a || '').trim()).filter(Boolean),
  };
}

function localSavedAt(storageAgentId: string, aliases: string[]): number {
  const snap = loadWorkspaceStoreResolved(storageAgentId, aliases);
  const wsAt = Number(snap.savedAt) || 0;
  // Session meta has no dedicated timestamp; treat presence as weak signal only.
  return wsAt;
}

function applyServerState(
  storageAgentId: string,
  aliases: string[],
  remote: {
    savedAt?: number;
    workspaces?: WorkspaceStoreSnapshot | null;
    session_project_meta?: Record<string, any>;
  },
): boolean {
  const remoteAt = Number(remote.savedAt) || 0;
  const localAt = localSavedAt(storageAgentId, aliases);
  const remoteWs = remote.workspaces;
  const hasRemoteWs =
    remoteWs &&
    typeof remoteWs === 'object' &&
    Array.isArray((remoteWs as WorkspaceStoreSnapshot).workspaces);

  const localSnap = loadWorkspaceStoreResolved(storageAgentId, aliases);
  const localEmpty = !localSnap.workspaces?.length;
  const shouldApplyWs = hasRemoteWs && (remoteAt > localAt || localEmpty);

  const remoteMeta =
    remote.session_project_meta && typeof remote.session_project_meta === 'object'
      ? remote.session_project_meta
      : {};
  const localMeta = loadSessionProjectMeta(storageAgentId);
  const localMetaEmpty = Object.keys(localMeta).length === 0;
  const remoteMetaCount = Object.keys(remoteMeta).length;
  const shouldApplyMeta =
    remoteMetaCount > 0 && (localMetaEmpty || remoteAt > localAt);

  if (!shouldApplyWs && !shouldApplyMeta) return false;

  applyingServer = true;
  try {
    if (shouldApplyWs && hasRemoteWs) {
      const snap = remoteWs as WorkspaceStoreSnapshot;
      saveWorkspaceStore(storageAgentId, {
        ...snap,
        savedAt: remoteAt || Date.now(),
      });
    }
    if (shouldApplyMeta) {
      // Prefer server when local empty or server newer; merge keys so we don't
      // drop local-only entries written in the same millisecond.
      const merged =
        localMetaEmpty || remoteAt > localAt
          ? { ...localMeta, ...remoteMeta }
          : { ...remoteMeta, ...localMeta };
      saveSessionProjectMeta(storageAgentId, merged as any);
    }
  } finally {
    applyingServer = false;
  }
  return true;
}

/** Pull server state into localStorage. Returns true if local state changed. */
export async function pullAgentWebUiState(): Promise<boolean> {
  if (!target || pullInFlight) return false;
  pullInFlight = true;
  try {
    const res = await adminAPI.getWebUiState(target.serverAgentName);
    return applyServerState(target.storageAgentId, target.aliases, res);
  } catch (err) {
    console.warn('[agentWebUiSync] pull failed', err);
    return false;
  } finally {
    pullInFlight = false;
  }
}

async function pushNow(): Promise<void> {
  if (!target || applyingServer || pushInFlight) return;
  pushInFlight = true;
  try {
    let snap = loadWorkspaceStoreResolved(target.storageAgentId, target.aliases);
    const meta = loadSessionProjectMeta(target.storageAgentId);
    const savedAt = Number(snap.savedAt) || Date.now();
    if (!snap.savedAt) {
      applyingServer = true;
      try {
        saveWorkspaceStore(target.storageAgentId, { ...snap, savedAt });
        snap = loadWorkspaceStoreResolved(target.storageAgentId, target.aliases);
      } finally {
        applyingServer = false;
      }
    }
    await adminAPI.putWebUiState(target.serverAgentName, {
      savedAt: Number(snap.savedAt) || savedAt,
      workspaces: snap,
      session_project_meta: meta,
    });
  } catch (err) {
    console.warn('[agentWebUiSync] push failed', err);
  } finally {
    pushInFlight = false;
  }
}

/** Debounced push after local chrome / session-meta changes. */
export function schedulePushAgentWebUiState(delayMs = 400): void {
  if (!target || applyingServer) return;
  if (pushTimer) clearTimeout(pushTimer);
  pushTimer = setTimeout(() => {
    pushTimer = null;
    void pushNow();
  }, delayMs);
}

/** Wire window events → debounced push (call once per Agent Web mount). */
export function bindAgentWebUiSyncPush(): () => void {
  const onChange = (ev: Event) => {
    const detail = (ev as CustomEvent)?.detail;
    const id = detail?.agentId;
    if (target && id && id !== target.storageAgentId && !target.aliases.includes(id)) {
      return;
    }
    schedulePushAgentWebUiState();
  };
  window.addEventListener(WORKSPACES_CHANGED_EVENT, onChange);
  window.addEventListener(SESSION_META_EVENT, onChange);
  return () => {
    window.removeEventListener(WORKSPACES_CHANGED_EVENT, onChange);
    window.removeEventListener(SESSION_META_EVENT, onChange);
    if (pushTimer) {
      clearTimeout(pushTimer);
      pushTimer = null;
    }
  };
}
