/**
 * Update preferences — the renderer's mirror of the main-process
 * ``update-prefs.json``.
 *
 * The main process owns the values: it reads them before the renderer mounts
 * (to auto-download a found release, and to apply it on quit). This store is
 * the UI's view — the settings toggles and the idle auto-install watcher read
 * it, and changes are written back through ``setUpdatePrefs``.
 *
 * Defaults mirror the main process so a dev/non-Electron renderer still shows
 * sensible toggle states.
 */
export interface UpdatePrefs {
  autoDownload: boolean;
  installOnQuit: boolean;
  autoInstallWhenIdle: boolean;
}

const DEFAULT_PREFS: UpdatePrefs = {
  autoDownload: true,
  installOnQuit: true,
  autoInstallWhenIdle: false,
};

let prefs: UpdatePrefs = { ...DEFAULT_PREFS };
let loaded = false;
const listeners = new Set<(next: UpdatePrefs) => void>();

function emit(): void {
  listeners.forEach((fn) => fn(prefs));
}

export function getUpdatePrefs(): UpdatePrefs {
  return prefs;
}

export function isUpdatePrefsLoaded(): boolean {
  return loaded;
}

export function subscribeUpdatePrefs(listener: (next: UpdatePrefs) => void): () => void {
  listeners.add(listener);
  listener(prefs);
  return () => listeners.delete(listener);
}

/** Pull the current values from the main process. Safe to call more than once. */
export async function loadUpdatePrefs(): Promise<UpdatePrefs> {
  const env = window.electronEnv;
  if (!env?.getUpdatePrefs) {
    loaded = true;
    return prefs;
  }
  try {
    prefs = { ...DEFAULT_PREFS, ...(await env.getUpdatePrefs()) };
    loaded = true;
    emit();
  } catch {
    // Keep the last known / default values.
  }
  return prefs;
}

/** Apply a change locally and persist it; the main process returns the stored set. */
export async function saveUpdatePrefs(next: Partial<UpdatePrefs>): Promise<UpdatePrefs> {
  prefs = { ...prefs, ...next };
  loaded = true;
  emit();

  const env = window.electronEnv;
  if (env?.setUpdatePrefs) {
    try {
      prefs = { ...DEFAULT_PREFS, ...(await env.setUpdatePrefs(next)) };
      emit();
    } catch {
      // Keep the optimistic local value.
    }
  }
  return prefs;
}

/** Test helper: restore the module to its initial state. */
export function resetUpdatePrefsForTest(): void {
  prefs = { ...DEFAULT_PREFS };
  loaded = false;
  listeners.clear();
}
