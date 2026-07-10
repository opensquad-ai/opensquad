/**
 * Electron preload script
 */
import { contextBridge, ipcRenderer } from 'electron'

export type PreloadUpdateStatus = {
  phase: 'downloading' | 'preparing' | 'launching' | 'shutting-down'
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
  onUpdateStatus: (callback: (status: PreloadUpdateStatus) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, status: PreloadUpdateStatus) => callback(status)
    ipcRenderer.on('electron:update-status', listener)
    return () => ipcRenderer.removeListener('electron:update-status', listener)
  },
  onMaximizedChanged: (callback: (maximized: boolean) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, maximized: boolean) => callback(maximized)
    ipcRenderer.on('electron:maximized-changed', listener)
    return () => ipcRenderer.removeListener('electron:maximized-changed', listener)
  },
})
