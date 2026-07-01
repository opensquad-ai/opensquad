import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('setupApi', {
  start: () => ipcRenderer.invoke('setup:start'),
  cancel: () => ipcRenderer.invoke('setup:cancel'),
  onInit: (cb: (payload: unknown) => void) => {
    ipcRenderer.on('setup:init', (_e, payload) => cb(payload))
  },
  onLog: (cb: (line: string) => void) => {
    ipcRenderer.on('setup:log', (_e, line) => cb(line))
  },
  onStepProgress: (cb: (payload: { stepId: string; pct: number }) => void) => {
    ipcRenderer.on('setup:step-progress', (_e, payload) => cb(payload))
  },
  onComplete: (cb: (payload: { ok: boolean; error?: string }) => void) => {
    ipcRenderer.on('setup:complete', (_e, payload) => cb(payload))
  },
})
