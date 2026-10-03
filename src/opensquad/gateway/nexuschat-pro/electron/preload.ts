/**
 * Electron preload script
 */
import { contextBridge, ipcRenderer } from 'electron'

export type PreloadUpdateStatus = {
  phase: 'downloading' | 'downloaded' | 'preparing' | 'launching' | 'shutting-down'
  percent?: number
  transferred?: number
  total?: number
}

contextBridge.exposeInMainWorld('electronEnv', {
  isElectron: true,
  platform:   process.platform,
  arch:       process.arch,
  popupMenu:  (menuId: string) => ipcRenderer.invoke('electron:popup-menu', menuId),
  windowControl: (action: 'minimize' | 'maximize' | 'close') =>
    ipcRenderer.invoke('electron:window-control', action),
  isMaximized: () => ipcRenderer.invoke('electron:is-maximized') as Promise<boolean>,
  pickWorkspaceFolder: () =>
    ipcRenderer.invoke('electron:pick-workspace-folder') as Promise<string | null>,
  restartApp: () => ipcRenderer.invoke('electron:restart-app') as Promise<void>,
  /** Manual update check; defaults to stable (beta reserved for future UI). */
  checkForUpdates: (channel: 'stable' | 'beta' = 'stable') =>
    ipcRenderer.invoke('electron:check-for-updates', channel),
  downloadAndInstallUpdate: (payload: { url: string; fileName: string }) =>
    ipcRenderer.invoke('electron:download-and-install-update', payload) as Promise<
      { ok: true } | { ok: false; error: string }
    >,
  /** Background download only — no quit; progress arrives on `onUpdateStatus`. */
  downloadUpdate: (payload: { url: string; fileName: string; version?: string }) =>
    ipcRenderer.invoke('electron:download-update', payload) as Promise<
      { ok: true } | { ok: false; error: string }
    >,
  /** Relaunch the installer and quit (call after warning about active work). */
  installUpdate: () =>
    ipcRenderer.invoke('electron:install-update') as Promise<
      { ok: true } | { ok: false; error: string }
    >,
  /** Whether a downloaded installer is waiting to be installed. */
  hasPendingUpdate: () =>
    ipcRenderer.invoke('electron:has-pending-update') as Promise<{
      pending: boolean
      version?: string | null
    }>,
  /** Update preferences (auto background download / apply on quit / idle install). */
  getUpdatePrefs: () =>
    ipcRenderer.invoke('electron:get-update-prefs') as Promise<{
      autoDownload: boolean
      installOnQuit: boolean
      autoInstallWhenIdle: boolean
    }>,
  setUpdatePrefs: (prefs: {
    autoDownload?: boolean
    installOnQuit?: boolean
    autoInstallWhenIdle?: boolean
  }) =>
    ipcRenderer.invoke('electron:set-update-prefs', prefs) as Promise<{
      autoDownload: boolean
      installOnQuit: boolean
      autoInstallWhenIdle: boolean
    }>,
  onUpdateStatus: (callback: (status: PreloadUpdateStatus) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, status: PreloadUpdateStatus) => callback(status)
    ipcRenderer.on('electron:update-status', listener)
    return () => ipcRenderer.removeListener('electron:update-status', listener)
  },
  onUpdateAvailable: (callback: (info: {
    hasUpdate: boolean
    currentVersion: string
    latestVersion: string
    downloadUrl?: string
    fileName?: string
    releaseNotes?: string
    isBeta: boolean
    releaseUrl?: string
  }) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, info: {
      hasUpdate: boolean
      currentVersion: string
      latestVersion: string
      downloadUrl?: string
      fileName?: string
      releaseNotes?: string
      isBeta: boolean
      releaseUrl?: string
    }) => callback(info)
    ipcRenderer.on('electron:update-available', listener)
    return () => ipcRenderer.removeListener('electron:update-available', listener)
  },
  onMaximizedChanged: (callback: (maximized: boolean) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, maximized: boolean) => callback(maximized)
    ipcRenderer.on('electron:maximized-changed', listener)
    return () => ipcRenderer.removeListener('electron:maximized-changed', listener)
  },
})
