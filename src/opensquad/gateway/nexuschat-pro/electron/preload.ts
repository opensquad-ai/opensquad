/**
 * Electron preload script
 */
import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronEnv', {
  isElectron: true,
  platform:   process.platform,
  popupMenu:  (menuId: string) => ipcRenderer.invoke('electron:popup-menu', menuId),
  windowControl: (action: 'minimize' | 'maximize' | 'close') =>
    ipcRenderer.invoke('electron:window-control', action),
  isMaximized: () => ipcRenderer.invoke('electron:is-maximized') as Promise<boolean>,
  pickWorkspaceFolder: () =>
    ipcRenderer.invoke('electron:pick-workspace-folder') as Promise<string | null>,
  restartApp: () => ipcRenderer.invoke('electron:restart-app') as Promise<void>,
  downloadAndInstallUpdate: (payload: { url: string; fileName: string }) =>
    ipcRenderer.invoke('electron:download-and-install-update', payload) as Promise<
      { ok: true } | { ok: false; error: string }
    >,
  onUpdateDownloadProgress: (
    callback: (progress: { percent: number; transferred: number; total: number }) => void,
  ) => {
    const listener = (
      _event: Electron.IpcRendererEvent,
      progress: { percent: number; transferred: number; total: number },
    ) => callback(progress)
    ipcRenderer.on('electron:update-download-progress', listener)
    return () => ipcRenderer.removeListener('electron:update-download-progress', listener)
  },
  onUpdateInstalling: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on('electron:update-installing', listener)
    return () => ipcRenderer.removeListener('electron:update-installing', listener)
  },
  onMaximizedChanged: (callback: (maximized: boolean) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, maximized: boolean) => callback(maximized)
    ipcRenderer.on('electron:maximized-changed', listener)
    return () => ipcRenderer.removeListener('electron:maximized-changed', listener)
  },
})
