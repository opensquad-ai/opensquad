/** Shared folder pick + recent cwd helpers for Solo composer. */

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
function directoryPathFromFileList(files: FileList | null | undefined): string | null {
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

/** Open OS folder picker via hidden webkitdirectory input. */
function pickFolderPathViaInput(): Promise<string | null> {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    (input as HTMLInputElement & { webkitdirectory: boolean }).webkitdirectory = true;
    (input as any).directory = true;
    input.style.display = 'none';
    document.body.appendChild(input);

    let settled = false;
    const finish = (value: string | null) => {
      if (settled) return;
      settled = true;
      window.removeEventListener('focus', onWindowFocus);
      input.remove();
      resolve(value);
    };

    input.onchange = () => {
      const path = directoryPathFromFileList(input.files);
      finish(path?.trim() || null);
    };

    const onWindowFocus = () => {
      window.setTimeout(() => {
        if (!settled && (!input.files || input.files.length === 0)) {
          finish(null);
        }
      }, 400);
    };
    window.addEventListener('focus', onWindowFocus);
    input.click();
  });
}

/**
 * Native folder picker only (no path prompt):
 * 1) Electron dialog
 * 2) OS folder chooser via webkitdirectory input
 */
export async function pickFolderPath(): Promise<string | null> {
  try {
    if (typeof window !== 'undefined' && window.electronEnv?.pickWorkspaceFolder) {
      const picked = await window.electronEnv.pickWorkspaceFolder();
      return picked?.trim() || null;
    }
  } catch (err: any) {
    if (err?.name === 'AbortError') return null;
    console.warn('[pickFolderPath] electron picker failed, trying input fallback', err);
  }

  try {
    return await pickFolderPathViaInput();
  } catch (err: any) {
    if (err?.name === 'AbortError') return null;
    console.error('[pickFolderPath]', err);
    return null;
  }
}

export function folderLabel(path: string): string {
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
  return parts[parts.length - 1] || path;
}
