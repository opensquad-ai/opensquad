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
  onMaximizedChanged: (callback: (maximized: boolean) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, maximized: boolean) => callback(maximized)
    ipcRenderer.on('electron:maximized-changed', listener)
    return () => ipcRenderer.removeListener('electron:maximized-changed', listener)
  },
})
