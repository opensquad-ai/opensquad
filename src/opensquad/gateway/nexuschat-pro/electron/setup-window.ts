import { BrowserWindow, ipcMain, app } from 'electron'
import path from 'path'
import fs from 'fs'
import {
  SETUP_STEPS,
  installAgentRuntime,
  type SetupStepId,
} from './agent-runtime'

export interface SetupWizardResult {
  ok: boolean
  cancelled?: boolean
}

function resolveSetupHtmlPath(): string {
  const devPath = path.join(__dirname, '..', 'assets', 'setup', 'setup.html')
  if (!app.isPackaged) return devPath
  const packaged = path.join(process.resourcesPath, 'assets', 'setup', 'setup.html')
  return fs.existsSync(packaged) ? packaged : devPath
}

export function runSetupWizard(options: { setupOnly?: boolean } = {}): Promise<SetupWizardResult> {
  return new Promise((resolve) => {
    let cancelled = false
    let finished = false

    const win = new BrowserWindow({
      width: 980,
      height: 640,
      minWidth: 820,
      minHeight: 560,
      title: 'OpenSquad 安装程序',
      autoHideMenuBar: true,
      webPreferences: {
        preload: path.join(__dirname, 'setup-preload.cjs'),
        contextIsolation: true,
        nodeIntegration: false,
      },
    })

    const finish = (result: SetupWizardResult) => {
      if (finished) return
      finished = true
      ipcMain.removeHandler('setup:start')
      ipcMain.removeHandler('setup:cancel')
      if (!win.isDestroyed()) win.close()
      resolve(result)
    }

    ipcMain.handle('setup:start', async () => {
      const logs: string[] = []
      const log = (line: string) => {
        logs.push(line)
        if (!win.isDestroyed()) win.webContents.send('setup:log', line)
      }

      try {
        await installAgentRuntime({
          log,
          cancelled: () => cancelled,
          state: {},
          onStepProgress: (stepId: SetupStepId, pct: number) => {
            if (!win.isDestroyed()) win.webContents.send('setup:step-progress', { stepId, pct })
          },
        })
        win.webContents.send('setup:complete', { ok: true })
        setTimeout(() => finish({ ok: true }), options.setupOnly ? 1200 : 600)
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        log(`ERROR: ${message}`)
        win.webContents.send('setup:complete', { ok: false, error: message })
        if (message === 'Cancelled') finish({ ok: false, cancelled: true })
      }
    })

    ipcMain.handle('setup:cancel', () => {
      cancelled = true
      finish({ ok: false, cancelled: true })
    })

    win.webContents.on('did-finish-load', () => {
      if (win.isDestroyed()) return
      win.webContents.send('setup:init', {
        steps: SETUP_STEPS.map((s) => ({ id: s.id, title: s.title })),
        setupOnly: options.setupOnly ?? false,
      })
    })

    void win.loadFile(resolveSetupHtmlPath())
    win.on('closed', () => {
      if (!finished) finish({ ok: false, cancelled: true })
    })
  })
}
