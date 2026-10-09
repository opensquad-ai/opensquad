/**
 * Which desktop-update entry points the Electron shell under this renderer really implements.
 *
 * The renderer and the shell are not necessarily the same build — a source/dev frontend can be
 * served into an installed app — and shells before 0.9.0 have no `electron:download-update`.
 * They still emit `electron:update-available`, so the newer UI offered "download in background"
 * and the click did nothing at all. Gate the action and its copy on these flags, not on hope.
 */
export interface DesktopUpdateCapabilities {
  /** Background download with a separate install step (0.9.0+). */
  backgroundDownload: boolean;
  /** Install an installer that is already on disk (0.9.0+). */
  installDownloaded: boolean;
  /** Pre-0.9.0 shells: one combined action that downloads, installs and quits. */
  downloadAndInstall: boolean;
}

export function desktopUpdateCapabilities(): DesktopUpdateCapabilities {
  const env = typeof window === 'undefined' ? undefined : window.electronEnv;
  return {
    backgroundDownload: typeof env?.downloadUpdate === 'function',
    installDownloaded: typeof env?.installUpdate === 'function',
    downloadAndInstall: typeof env?.downloadAndInstallUpdate === 'function',
  };
}
