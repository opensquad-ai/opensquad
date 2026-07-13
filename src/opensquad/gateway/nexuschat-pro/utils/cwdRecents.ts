/** Shared folder pick + recent cwd helpers for Solo composer. */

import { adminAPI } from '../services/api';

const RECENTS_KEY = 'solo_cwd_recents';
const MAX_RECENTS = 8;

export function loadCwdRecents(): string[] {
  try {
    const raw = localStorage.getItem(RECENTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((p): p is string => typeof p === 'string' && p.trim().length > 0);
  } catch {
    return [];
  }
}

export function pushCwdRecent(path: string): string[] {
  const normalized = path.trim();
  if (!normalized) return loadCwdRecents();
  const prev = loadCwdRecents().filter(
    (p) => p.replace(/\\/g, '/').toLowerCase() !== normalized.replace(/\\/g, '/').toLowerCase(),
  );
  const next = [normalized, ...prev].slice(0, MAX_RECENTS);
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
  } catch {}
  return next;
}

/** Extract absolute directory path from files selected via webkitdirectory. */
export function directoryPathFromFileList(files: FileList | null | undefined): string | null {
  if (!files?.length) return null;
  const file = files[0];
  const fullPath = (file as File & { path?: string }).path;
  if (fullPath && typeof fullPath === 'string') {
    const sep = fullPath.includes('\\') ? '\\' : '/';
    const rel = file.webkitRelativePath || '';
    if (rel) {
      const relParts = rel.replace(/\\/g, '/').split('/');
      if (relParts.length > 1) {
        const fileName = relParts[relParts.length - 1];
        const suffix = sep === '\\' ? `\\${fileName}` : `/${fileName}`;
        if (fullPath.endsWith(suffix)) {
          return fullPath.slice(0, -suffix.length);
        }
      }
      const rootName = relParts[0];
      const rootSuffix = sep === '\\' ? `${rootName}\\` : `${rootName}/`;
      const idx = fullPath.indexOf(rootSuffix);
      if (idx >= 0) {
        return fullPath.slice(0, idx + rootName.length);
      }
    }
    const lastSep = Math.max(fullPath.lastIndexOf('\\'), fullPath.lastIndexOf('/'));
    if (lastSep > 0) return fullPath.slice(0, lastSep);
  }
  return null;
}

/** Folder name hint when absolute path is unavailable (browser security). */
export function folderNameHintFromFileList(files: FileList | null | undefined): string | null {
  if (!files?.length) return null;
  const rel = files[0].webkitRelativePath;
  if (!rel) return null;
  return rel.replace(/\\/g, '/').split('/')[0] || null;
}

export type FolderPickResult = {
  /** Absolute path when available (Electron / Launcher native dialog). */
  path: string | null;
  /** Folder basename hint (legacy webkitdirectory fallback). */
  folderNameHint: string | null;
  /** True when the user dismissed the picker without selecting. */
  cancelled: boolean;
  /** Error message when the native picker could not be opened. */
  error?: string | null;
};

/**
 * Suggest an absolute path when the browser only exposes a folder name.
 * Prefer sibling of current cwd: C:/ai_test/t + "ds" → C:/ai_test/ds
 */
export function suggestPathFromFolderHint(
  currentCwd: string | null | undefined,
  folderName: string | null | undefined,
): string {
  const name = (folderName || '').trim();
  const cwd = (currentCwd || '').trim().replace(/[/\\]+$/, '');
  if (!name) return cwd;
  if (!cwd) return name;

  const sep = cwd.includes('\\') ? '\\' : '/';
  const lastSep = Math.max(cwd.lastIndexOf('/'), cwd.lastIndexOf('\\'));
  if (lastSep <= 0) return name;
  const parent = cwd.slice(0, lastSep);
  return `${parent}${sep}${name}`;
}

async function pickFolderViaLauncher(initialDir?: string | null): Promise<FolderPickResult> {
  try {
    const res = await adminAPI.pickDirectory(initialDir);
    if (res?.cancelled) {
      return { path: null, folderNameHint: null, cancelled: true };
    }
    const path = typeof res?.path === 'string' ? res.path.trim() : '';
    if (path) {
      return { path, folderNameHint: folderLabel(path), cancelled: false };
    }
    if (res?.error) {
      return { path: null, folderNameHint: null, cancelled: false, error: res.error };
    }
    return { path: null, folderNameHint: null, cancelled: true };
  } catch (err: any) {
    console.warn('[pickFolder] launcher native picker failed', err);
    return {
      path: null,
      folderNameHint: null,
      cancelled: false,
      error: err?.message || String(err),
    };
  }
}

/**
 * Native folder picker that returns an absolute path:
 * 1) Electron dialog
 * 2) Launcher-hosted OS dialog (Agent Web in browser)
 */
export async function pickFolder(initialDir?: string | null): Promise<FolderPickResult> {
  try {
    if (typeof window !== 'undefined' && window.electronEnv?.pickWorkspaceFolder) {
      const picked = await window.electronEnv.pickWorkspaceFolder();
      const path = picked?.trim() || null;
      if (!path) return { path: null, folderNameHint: null, cancelled: true };
      return { path, folderNameHint: folderLabel(path), cancelled: false };
    }
  } catch (err: any) {
    if (err?.name === 'AbortError') {
      return { path: null, folderNameHint: null, cancelled: true };
    }
    console.warn('[pickFolder] electron picker failed, trying launcher', err);
  }

  return pickFolderViaLauncher(initialDir);
}

/** @deprecated Prefer pickFolder(); kept for callers that only need an absolute path. */
export async function pickFolderPath(initialDir?: string | null): Promise<string | null> {
  const result = await pickFolder(initialDir);
  return result.path;
}

export function folderLabel(path: string): string {
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
  return parts[parts.length - 1] || path;
}
