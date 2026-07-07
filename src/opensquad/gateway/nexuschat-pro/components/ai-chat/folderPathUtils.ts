/**
 * Resolve a directory path from a native folder-picker selection (webkitdirectory / Electron).
 */

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
