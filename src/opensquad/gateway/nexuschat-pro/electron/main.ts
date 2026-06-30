import { app, BrowserWindow, dialog, Menu, Tray, nativeImage, ipcMain } from 'electron'
import { spawn, ChildProcess } from 'child_process'
import net from 'net'
import path from 'path'
import http from 'http'
import fs from 'fs'
import { buildElectronPopupMenus, isElectronMenuId } from './electron-menus'
import { resolveDesktopWorkspace } from './desktop-workspace'
import { runDesktopUpdate, type UpdateStatus } from './desktop-updater'

// ── 常量 ─────────────────────────────────────────────────────────────────────
// Backend port: read from environment variable (set by docker-entrypoint.sh or
// launcher), fallback to 9555 (the default in src/opensquad/gateway/config.json).
// Was 9510 — that didn't match the actual backend port, so health checks timed
// out and the Electron app showed 'Backend did not start in time'.
const BACKEND_PORT  = parseInt(process.env.OPENSQUAD_PORT || '9555', 10)
const FRONTEND_PORT = parseInt(
  process.env.OPENSQUAD_FRONTEND_PORT || process.env.VITE_DEV_PORT || '5173',
  10,
)
// Launcher (agent process manager) management port. The desktop app spawns a
// second run.exe instance with `--service launcher` so the Agent Workstation
// page can list/configure agents. Without this, the UI shows
// "Launcher is not running (cannot connect to http://127.0.0.1:9600)".
const LAUNCHER_PORT = parseInt(process.env.OPENSQUAD_LAUNCHER_PORT || '9600', 10)
const DEV_MODE      = process.env.ELECTRON_DEV === '1' || process.env.ELECTRON_DEV === 'true'
const HEALTH_URL    = DEV_MODE
  ? `http://127.0.0.1:${FRONTEND_PORT}/`
  : `http://127.0.0.1:${BACKEND_PORT}/health`
const APP_URL       = DEV_MODE
  ? `http://127.0.0.1:${FRONTEND_PORT}`
  : `http://127.0.0.1:${BACKEND_PORT}`
const STARTUP_TIMEOUT_MS = 45_000   // 后端最长等待时间
const APP_DISPLAY_NAME = 'OpenSquad'

// Application icon — Windows expects an .ico, other platforms accept PNG.
// Path is resolved relative to the compiled main.cjs (dist-electron/).
const APP_ICON_PATH = process.platform === 'win32'
  ? path.join(__dirname, '..', 'assets', 'icon.ico')
  : path.join(__dirname, '..', 'assets', 'icon.png')

// Two backend processes share one PyInstaller binary:
//   gatewayProcess  → run.exe                      (FastAPI on BACKEND_PORT)
//   launcherProcess → run.exe --service launcher   (mgmt API on LAUNCHER_PORT)
// In DEV_MODE both stay null — the user runs `opensquad start` separately.
let gatewayProcess:  ChildProcess | null = null
let launcherProcess: ChildProcess | null = null
let mainWindow:      BrowserWindow | null = null
let tray:            Tray | null = null
const USE_CUSTOM_TITLEBAR = process.platform === 'win32'
let popupMenus = buildElectronPopupMenus()

function registerElectronIpc(): void {
  ipcMain.handle('electron:popup-menu', async (event, menuId: string) => {
    if (!isElectronMenuId(menuId)) return
    const win = BrowserWindow.fromWebContents(event.sender)
    popupMenus[menuId].popup({ window: win ?? mainWindow ?? undefined })
  })

  ipcMain.handle('electron:window-control', async (_event, action: string) => {
    const win = mainWindow
    if (!win) return
    if (action === 'minimize') win.minimize()
    else if (action === 'maximize') {
      if (win.isMaximized()) win.unmaximize()
      else win.maximize()
    } else if (action === 'close') win.close()
  })

  ipcMain.handle('electron:is-maximized', async () => mainWindow?.isMaximized() ?? false)

  ipcMain.handle('electron:pick-workspace-folder', async () => {
    const win = mainWindow ?? BrowserWindow.getFocusedWindow()
    const opts = {
      title: 'Select workspace folder',
      properties: ['openDirectory', 'createDirectory'] as Array<'openDirectory' | 'createDirectory'>,
    }
    const result = win
      ? await dialog.showOpenDialog(win, opts)
      : await dialog.showOpenDialog(opts)
    if (result.canceled || !result.filePaths[0]) return null
    return result.filePaths[0]
  })

  ipcMain.handle('electron:restart-app', async () => {
    app.relaunch()
    app.exit(0)
  })

  ipcMain.handle(
    'electron:download-and-install-update',
    async (event, payload: { url: string; fileName: string }) => {
      const win = BrowserWindow.fromWebContents(event.sender)
      const sendStatus = (status: UpdateStatus) => {
        win?.webContents.send('electron:update-status', status)
      }
      try {
        await runDesktopUpdate(payload.url, payload.fileName, sendStatus)
        return { ok: true as const }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        return { ok: false as const, error: message }
      }
    },
  )
}

// ── 获取各平台后端二进制路径 ──────────────────────────────────────────────────
function getBackendExe(): string {
  // 打包后: resources/ 与 app.asar 同级
  // 开发时: nexuschat-pro/resources/
  const resourcesDir = app.isPackaged
    ? process.resourcesPath
    : path.join(__dirname, '..', 'resources')

  // PyInstaller with `exclude_binaries=True` + COLLECT produces a *folder*
  // `run/` containing the executable plus its bundled `_internal/` deps
  // (see opensquad_backend.spec). Spawn the binary inside that folder.
  const map: Record<string, string> = {
    win32:  path.join(resourcesDir, 'backend-win',   'run', 'run.exe'),
    darwin: path.join(resourcesDir, 'backend-mac',   'run', 'run'),
    linux:  path.join(resourcesDir, 'backend-linux', 'run', 'run'),
  }
  return map[process.platform] ?? map['linux']
}

// ── 启动 Python 后端 ──────────────────────────────────────────────────────────
// Electron userData holds app prefs (desktop-workspace.json). The active
// workspace (chat.db, uploads, agents) may live elsewhere after the user
// switches it in System Settings → Workspace.
function getBackendEnv(): { cwd: string; env: NodeJS.ProcessEnv } {
  const appDataDir = app.getPath('userData')
  fs.mkdirSync(appDataDir, { recursive: true })
  const workspaceDir = resolveDesktopWorkspace(appDataDir)
  fs.mkdirSync(workspaceDir, { recursive: true })
  return {
    cwd: workspaceDir,
    env: {
      ...process.env,
      // Fixed Electron config dir (writable prefs, never moves)
      OPENSQUAD_APP_DATA: appDataDir,
      // Active workspace root (may differ from appDataDir)
      OPENSQUAD_USER_DATA: workspaceDir,
      // 禁用 uvicorn 热重载（打包环境不支持）
      OPENSQUAD_RELOAD: '0',
    },
  }
}

function spawnBackend(args: string[], label: string): ChildProcess | null {
  const exe = getBackendExe()
  if (!fs.existsSync(exe)) {
    dialog.showErrorBox(
      'Backend not found',
      `Expected binary at:\n${exe}\n\nPlease rebuild the backend.`
    )
    app.quit()
    return null
  }
  const { cwd, env } = getBackendEnv()
  const proc = spawn(exe, args, { cwd, detached: false, env })
  proc.stdout?.on('data', (d: Buffer) =>
    console.log(`[${label}]`, d.toString().trimEnd()))
  proc.stderr?.on('data', (d: Buffer) =>
    console.error(`[${label}:err]`, d.toString().trimEnd()))
  proc.on('exit', (code, signal) =>
    console.log(`[${label}] exited  code=${code} signal=${signal}`))
  return proc
}

// Gateway: FastAPI backend on BACKEND_PORT (plain `run.exe`, no --service).
function startBackend(): void {
  gatewayProcess = spawnBackend([], 'backend')
}

// Launcher: agent process manager on LAUNCHER_PORT. Spawned as a second
// `run.exe` instance with `--service launcher`.
// `--no-auto-start`: agents still need a dedicated frozen entry (see docs).
// Plugin services with `service/main.py` auto-start here; the launcher uses
// system Python when bundled (see process_manager._plugin_python_executable).
function startLauncher(): void {
  launcherProcess = spawnBackend(
    [
      '--service', 'launcher',
      '--mgmt-port', String(LAUNCHER_PORT),
      '--no-auto-start',
    ],
    'launcher',
  )
}

// ── 轮询 URL 直到服务就绪 ─────────────────────────────────────────────────────
function waitForUrl(url: string, label: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + STARTUP_TIMEOUT_MS

    const check = () => {
      const req = http.get(url, (res) => {
        res.resume()
        if (res.statusCode && res.statusCode < 500) return resolve()
        schedule()
      })
      req.on('error', schedule)
      req.setTimeout(1000, () => { req.destroy(); schedule() })
    }

    const schedule = () => {
      if (Date.now() >= deadline) {
        return reject(new Error(`${label} startup timeout (${url})`))
      }
      setTimeout(check, 600)
    }

    check()
  })
}

// ── 轮询 TCP 端口直到服务监听 ────────────────────────────────────────────────
// The launcher's management API has no /health endpoint (its routes start at
// /api/agents), so probe the port directly with a TCP connect instead.
function waitForPort(port: number, label: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + STARTUP_TIMEOUT_MS

    const check = () => {
      const sock = net.createConnection({ host: '127.0.0.1', port }, () => {
        sock.destroy()
        resolve()
      })
      sock.on('error', schedule)
      sock.setTimeout(1000, () => { sock.destroy(); schedule() })
    }

    const schedule = () => {
      if (Date.now() >= deadline) {
        return reject(new Error(`${label} startup timeout (port ${port})`))
      }
      setTimeout(check, 600)
    }

    check()
  })
}

// ── 创建主窗口 ────────────────────────────────────────────────────────────────
async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width:     1280,
    height:    800,
    minWidth:  900,
    minHeight: 600,
    title:     USE_CUSTOM_TITLEBAR ? '' : APP_DISPLAY_NAME,
    show:      false,
    frame:     !USE_CUSTOM_TITLEBAR,
    autoHideMenuBar: USE_CUSTOM_TITLEBAR,
    icon:      APP_ICON_PATH,
    webPreferences: {
      preload:          path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration:  false,
    },
  })

  if (USE_CUSTOM_TITLEBAR) {
    mainWindow.setMenu(null)
    mainWindow.on('maximize', () => mainWindow?.webContents.send('electron:maximized-changed', true))
    mainWindow.on('unmaximize', () => mainWindow?.webContents.send('electron:maximized-changed', false))
  }

  // 启动中占位页
  mainWindow.loadURL(
    `data:text/html,` +
    `<html><head><meta charset="utf-8"></head>` +
    `<body style="background:#0f0f1a;display:flex;flex-direction:column;` +
    `align-items:center;justify-content:center;height:100vh;margin:0;` +
    `font-family:sans-serif;color:#aaa">` +
    `<div style="font-size:2rem;margin-bottom:.5rem">⚡</div>` +
    `<div>Starting ${APP_DISPLAY_NAME}…</div>` +
    `</body></html>`
  )
  mainWindow.show()

  try {
    const readyLabel = DEV_MODE ? 'Vite dev server' : 'Backend'
    await waitForUrl(HEALTH_URL, readyLabel)
    // In packaged mode, also wait for the launcher management port so the
    // Agent Workstation page doesn't load before the launcher is reachable
    // (it would otherwise flash "Launcher is not running"). In DEV_MODE the
    // user runs `opensquad start` themselves, so we don't wait for 9600 here.
    if (!DEV_MODE) {
      try {
        await waitForPort(LAUNCHER_PORT, 'Launcher')
      } catch {
        // Non-fatal: gateway is up, the UI just won't show agents yet. Don't
        // quit — the launcher may still come up, or the user can restart it.
        console.error(`[electron] Launcher did not bind port ${LAUNCHER_PORT} in time; Agent Workstation will be unavailable.`)
      }
    }
    await mainWindow.loadURL(APP_URL)
    if (!USE_CUSTOM_TITLEBAR) {
      mainWindow.setTitle(APP_DISPLAY_NAME)
    }
  } catch {
    dialog.showErrorBox(
      'Startup Failed',
      DEV_MODE
        ? `Vite dev server did not start in time.\nRun \`uv run opensquad start\` first, then retry.\nExpected: ${APP_URL}`
        : 'Backend did not start in time.\nPlease check logs or reinstall the app.'
    )
    app.quit()
    return
  }

  mainWindow.on('closed', () => { mainWindow = null })
}

// ── 系统托盘 ────────────────────────────────────────────────────────────────
function createTray(): void {
  // 开发时 assets/ 在项目根，打包后由 extraResources 提供
  const iconPath = app.isPackaged
    ? path.join(process.resourcesPath, 'assets', 'tray.png')
    : path.join(__dirname, '..', 'assets', 'tray.png')
  const icon = fs.existsSync(iconPath)
    ? nativeImage.createFromPath(iconPath).resize({ width: 16, height: 16 })
    : nativeImage.createEmpty()

  tray = new Tray(icon)
  tray.setToolTip(APP_DISPLAY_NAME)
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Show',  click: () => mainWindow?.show() },
    { type: 'separator' },
    { label: 'Quit',  click: () => app.quit() },
  ]))
}

// ── 应用生命周期 ──────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  registerElectronIpc()
  if (USE_CUSTOM_TITLEBAR) {
    Menu.setApplicationMenu(null)
    popupMenus = buildElectronPopupMenus()
  }
  if (!DEV_MODE) {
    startBackend()
    // Spawn the launcher right after the gateway (auto-starts plugin services).
    // createWindow() waits for both to be ready.
    startLauncher()
  } else {
    console.log(
      '[electron] DEV_MODE enabled — skipping backend spawn; loading Vite at',
      APP_URL,
      `(gateway API expected on port ${BACKEND_PORT}, launcher on ${LAUNCHER_PORT})`,
    )
  }
  createTray()
  await createWindow()

  app.on('activate', async () => {
    if (BrowserWindow.getAllWindows().length === 0) await createWindow()
  })
})

app.on('window-all-closed', () => {
  // macOS 惯例：关闭窗口不退出，保留后台运行
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  // Tear down both backend processes. On Windows use taskkill /T so the whole
  // child tree (e.g. launcher-spawned agents) is cleaned up.
  const procs: Array<[string, ChildProcess | null]> = [
    ['backend',  gatewayProcess],
    ['launcher', launcherProcess],
  ]
  for (const [label, proc] of procs) {
    if (proc && !proc.killed) {
      console.log(`[electron] Terminating ${label} process…`)
      if (process.platform === 'win32') {
        try {
          spawn('taskkill', ['/pid', String(proc.pid), '/f', '/t'])
        } catch { /* ignore */ }
      } else {
        proc.kill('SIGTERM')
      }
    }
  }
  gatewayProcess  = null
  launcherProcess = null
})
