export {}

import type { CSSProperties, InputHTMLAttributes } from 'react';

declare module 'react' {
  interface CSSProperties {
    WebkitAppRegion?: 'drag' | 'no-drag';
  }
  interface InputHTMLAttributes<T> {
    // Folder picker (Chrome/Edge). Not in React's official types.
    webkitdirectory?: string;
    directory?: string;
  }
}


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
      checkForUpdates?: (channel?: 'stable' | 'beta') => Promise<{
        hasUpdate: boolean
        currentVersion: string
        latestVersion: string
        downloadUrl?: string
        fileName?: string
        releaseNotes?: string
        isBeta: boolean
        releaseUrl?: string
        error?: string
      }>
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
