export {}

declare global {
  interface Window {
    electronEnv?: {
      isElectron: boolean
      platform: NodeJS.Platform
      arch: string
      popupMenu?: (menuId: string) => Promise<void>
      windowControl?: (action: 'minimize' | 'maximize' | 'close') => Promise<void>
      isMaximized?: () => Promise<boolean>
      pickWorkspaceFolder?: () => Promise<string | null>
      restartApp?: () => Promise<void>
      downloadAndInstallUpdate?: (payload: {
        url: string
        fileName: string
      }) => Promise<{ ok: true } | { ok: false; error: string }>
      onUpdateStatus?: (callback: (status: {
        phase: 'downloading' | 'preparing' | 'launching' | 'shutting-down'
        percent?: number
        transferred?: number
        total?: number
      }) => void) => () => void
      onMaximizedChanged?: (callback: (maximized: boolean) => void) => () => void
    }
  }
}
