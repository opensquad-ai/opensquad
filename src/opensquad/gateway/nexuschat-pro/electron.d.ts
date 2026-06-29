export {}

declare global {
  interface Window {
    electronEnv?: {
      isElectron: boolean
      platform: NodeJS.Platform
      popupMenu?: (menuId: string) => Promise<void>
      windowControl?: (action: 'minimize' | 'maximize' | 'close') => Promise<void>
      isMaximized?: () => Promise<boolean>
      onMaximizedChanged?: (callback: (maximized: boolean) => void) => () => void
    }
  }
}
