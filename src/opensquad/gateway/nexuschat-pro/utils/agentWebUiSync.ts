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
  mergeWorkspaceSnapshots,
  isEmptyWorkspaceSnapshot,
  reconcileWorkspaceExistence,
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

/**
 * Does `root` exist as a directory on the agent host?
 *
 * `true` / `false` when the host answered, `null` when we could not find out —
 * and unknown must never flag a workspace, or a single 502 from the launcher
 * would retire every project at once. The check reuses the project listing
 * (the launcher answers 404 `Root not found` for a path that is not a
 * directory) so it needs no new backend route.
 */
export async function probeProjectRoot(
  serverAgentName: string,
  root: string,
): Promise<boolean | null> {
  const path = (root || '').trim();
  if (!serverAgentName || !path) return null;
  try {
    await adminAPI.listProjectDir(serverAgentName, '', path);
    return true;
  } catch (err: any) {
    if (Number(err?.status) === 404 && /Root not found/i.test(String(err?.message || ''))) {
      return false;
    }
    console.warn('[agentWebUiSync] project root probe inconclusive', path, err);
    return null;
  }
}

/**
 * Probe every registered workspace root and flag the ones that are gone.
 *
 * Returns true when flags or chrome changed; the caller's debounced push then
 * carries that correction to the other origins (the registry only ever unions,
 * so without a push the flag would stay local).
 */
async function reconcileHostWorkspaceExistence(
  storageAgentId: string,
  aliases: string[],
): Promise<boolean> {
  const t = target;
  if (!t) return false;
  const snap = loadWorkspaceStoreResolved(storageAgentId, aliases);
  const paths = Array.from(
    new Set(snap.workspaces.map((w) => (w.rootPath || '').trim()).filter(Boolean)),
  );
  if (paths.length === 0) return false;
  const probed = await Promise.all(
    paths.map(async (path) => [path, await probeProjectRoot(t.serverAgentName, path)] as const),
  );
  const exists: Record<string, boolean> = {};
  for (const [path, ok] of probed) {
    if (ok !== null) exists[path] = ok;
  }
  return reconcileWorkspaceExistence(storageAgentId, aliases, exists);
}

async function applyServerState(
  storageAgentId: string,
  aliases: string[],
  remote: {
    savedAt?: number;
    workspaces?: WorkspaceStoreSnapshot | null;
    session_project_meta?: Record<string, any>;
  },
): Promise<boolean> {
  const remoteAt = Number(remote.savedAt) || 0;
  const localAt = localSavedAt(storageAgentId, aliases);
  const remoteWs = remote.workspaces;
  const hasRemoteWs =
    remoteWs &&
    typeof remoteWs === 'object' &&
    Array.isArray((remoteWs as WorkspaceStoreSnapshot).workspaces);

  const localSnap = loadWorkspaceStoreResolved(storageAgentId, aliases);
  // Always merge when the host has workspaces: a freshly seeded origin must
  // not replace host chrome, and extra local folders should still be kept.
  const shouldApplyWs = !!hasRemoteWs;

  const remoteMeta =
    remote.session_project_meta && typeof remote.session_project_meta === 'object'
      ? remote.session_project_meta
      : {};
  const localMeta = loadSessionProjectMeta(storageAgentId);
  const localMetaEmpty = Object.keys(localMeta).length === 0;
  const remoteMetaCount = Object.keys(remoteMeta).length;
  const shouldApplyMeta =
    remoteMetaCount > 0 && (localMetaEmpty || remoteAt > localAt);

  if (!shouldApplyWs && !shouldApplyMeta) {
    // Nothing to merge — but a stale entry that only ever lived here still has
    // to be flagged, so probe regardless of whether the host had anything.
    return reconcileHostWorkspaceExistence(storageAgentId, aliases);
  }

  applyingServer = true;
  try {
    if (shouldApplyWs && hasRemoteWs) {
      const merged = mergeWorkspaceSnapshots(localSnap, remoteWs as WorkspaceStoreSnapshot);
      saveWorkspaceStore(storageAgentId, {
        ...merged,
        savedAt: Math.max(remoteAt, Number(merged.savedAt) || 0, Date.now()),
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
  // Runs outside the `applyingServer` guard: it saves a corrected chrome, and
  // that write must be allowed to schedule the push which propagates the flags.
  await reconcileHostWorkspaceExistence(storageAgentId, aliases);
  return true;
}

/** Pull server state into localStorage. Returns true if local state changed. */
export async function pullAgentWebUiState(): Promise<boolean> {
  if (!target || pullInFlight) return false;
  pullInFlight = true;
  try {
    const res = await adminAPI.getWebUiState(target.serverAgentName);
    return await applyServerState(target.storageAgentId, target.aliases, res);
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
    if (isEmptyWorkspaceSnapshot(snap)) {
      // Never push an empty origin over host chrome (dev vs packaged ports).
      return;
    }
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
