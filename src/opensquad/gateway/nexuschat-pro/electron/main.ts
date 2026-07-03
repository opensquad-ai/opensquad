import { app, BrowserWindow, dialog, Menu, Tray, nativeImage, ipcMain } from 'electron'
import { spawn, ChildProcess } from 'child_process'
import net from 'net'
import path from 'path'
import http from 'http'
import fs from 'fs'
import { buildElectronPopupMenus, isElectronMenuId } from './electron-menus'
import { resolveDesktopWorkspace } from './desktop-workspace'
import { runDesktopUpdate, type UpdateStatus } from './desktop-updater'
import { checkForUpdates, type UpdateChannel } from './update-checker'
import { agentPythonForBackendEnv, isAgentRuntimeReady } from './agent-runtime'
import { runSetupWizard } from './setup-window'

const SETUP_ONLY = process.argv.includes('--setup-runtime')

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

function resolvePackagedAsset(name: string): string {
  const devPath = path.join(__dirname, '..', 'assets', name)
  if (!app.isPackaged) return devPath
  const extraPath = path.join(process.resourcesPath, 'assets', name)
  return fs.existsSync(extraPath) ? extraPath : devPath
}

// Application icon — Windows expects an .ico, other platforms accept PNG.
const APP_ICON_PATH = process.platform === 'win32'
  ? resolvePackagedAsset('icon.ico')
  : resolvePackagedAsset('icon.png')

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

  // Manual update check (frontend "Check for updates" button).
  // Returns UpdateInfo; the frontend decides whether to prompt the user
  // and, if they accept, calls 'electron:download-and-install-update'
  // with the returned downloadUrl + fileName.
  ipcMain.handle(
    'electron:check-for-updates',
    async (_event, channel: UpdateChannel = 'stable') => {
      try {
        return await checkForUpdates(channel)
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        return {
          hasUpdate: false,
          currentVersion: app.getVersion(),
          latestVersion: app.getVersion(),
          isBeta: false,
          error: message,
        }
      }
    },
  )
}

// ── Auto update checker ──────────────────────────────────────────────────────
// Polls GitHub releases on a fixed cadence and notifies all open windows
// via 'electron:update-available' when a newer version is found. The user
// can accept/reject from the frontend; on accept, the frontend calls
// 'electron:download-and-install-update' (which uses desktop-updater.ts).
//
// Defaults to the stable channel. The frontend can switch to the beta
// channel via settings (not yet wired up — channel is hard-coded here
// until the UI lands).
const AUTO_UPDATE_INITIAL_DELAY_MS = 30_000 // 30s after app ready
const AUTO_UPDATE_INTERVAL_MS = 60 * 60 * 1000 // 1h

function startAutoUpdateChecker(channel: UpdateChannel = 'stable'): void {
  const tick = async () => {
    try {
      const info = await checkForUpdates(channel)
      if (info.hasUpdate) {
        // Broadcast to every open window so the frontend can show a
        // toast/badge. The user decides whether to actually install.
        for (const win of BrowserWindow.getAllWindows()) {
          if (!win.isDestroyed()) {
            win.webContents.send('electron:update-available', info)
          }
        }
      }
    } catch {
      // Silent — auto-check failures should never bother the user.
    }
  }
  setTimeout(tick, AUTO_UPDATE_INITIAL_DELAY_MS)
  setInterval(tick, AUTO_UPDATE_INTERVAL_MS)
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
  const agentPython = agentPythonForBackendEnv()
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    // Fixed Electron config dir (writable prefs, never moves)
    OPENSQUAD_APP_DATA: appDataDir,
    // Active workspace root (may differ from appDataDir)
    OPENSQUAD_USER_DATA: workspaceDir,
    // 禁用 uvicorn 热重载（打包环境不支持）
    OPENSQUAD_RELOAD: '0',
    // 强制 gateway 提供 dist/，避免误检测 Vite 端口导致空白页
    OPENSQUAD_DISABLE_VITE_PROXY: '1',
  }
  if (agentPython) {
    env.OPENSQUAD_PYTHON = agentPython
    env.OPENSQUAD_AGENT_RUNTIME = agentPython
  }
  return {
    cwd: workspaceDir,
    env,
  }
}

// Track whether the backend is being intentionally shut down (app quit).
// When true, the exit handler and health monitor skip auto-restart.
let _shuttingDown = false

// Avoid duplicate restart attempts racing with each other.
let _gatewayRestarting = false

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
  proc.on('exit', (code, signal) => {
    console.log(`[${label}] exited  code=${code} signal=${signal}`)
    // Auto-restart the gateway if it died unexpectedly (not during app quit).
    // This is the primary fix for "Failed to fetch after idle": the backend
    // process can be killed by Windows Defender, OOM, or a background-thread
    // crash during idle. Without restart, port 9555 stays dead and every
    // fetch() from the UI fails.
    if (!_shuttingDown && label === 'backend' && !_gatewayRestarting) {
      console.error(`[electron] Gateway exited unexpectedly (code=${code} signal=${signal}). Auto-restarting in 2s...`)
      _gatewayRestarting = true
      setTimeout(() => {
        _gatewayRestarting = false
        if (_shuttingDown) return
        startBackend()
        // Wait for it to be ready, then reload the window so the UI reconnects.
        waitForBackendFullyReady()
          .then(() => {
            console.log('[electron] Gateway restarted successfully, reloading window')
            mainWindow?.webContents.reload()
          })
          .catch(() => {
            console.error('[electron] Gateway failed to restart in time')
            dialog.showErrorBox(
              'Backend Restart Failed',
              'The backend process crashed and could not be restarted.\nPlease restart the app manually.'
            )
          })
      }, 2000)
    }
  })
  return proc
}

// Gateway: FastAPI backend on BACKEND_PORT (plain `run.exe`, no --service).
function startBackend(): void {
  gatewayProcess = spawnBackend([], 'backend')
}

// ── Periodic backend health monitor ───────────────────────────────────────────
// After startup, the gateway process can die or hang without Electron knowing
// (the exit event fires for crashes, but a *hung* process — event loop blocked
// or deadlock — keeps its PID alive while /health stops responding). This
// monitor polls /health every 30s; if it fails N consecutive times, the
// gateway is killed and restarted.
const HEALTH_MONITOR_INTERVAL_MS = 30_000  // 30s
const HEALTH_MONITOR_MAX_FAILURES = 3      // 3 consecutive failures → restart
let _healthFailCount = 0

function startBackendHealthMonitor(): void {
  setInterval(() => {
    if (_shuttingDown || _gatewayRestarting) return
    const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/health`, (res) => {
      res.resume()
      if (res.statusCode && res.statusCode < 500) {
        _healthFailCount = 0
      } else {
        _healthFailCount++
        console.warn(`[health-monitor] /health returned ${res.statusCode} (${_healthFailCount}/${HEALTH_MONITOR_MAX_FAILURES})`)
      }
    })
    req.on('error', (err) => {
      _healthFailCount++
      console.warn(`[health-monitor] /health error: ${err.message} (${_healthFailCount}/${HEALTH_MONITOR_MAX_FAILURES})`)
      if (_healthFailCount >= HEALTH_MONITOR_MAX_FAILURES) {
        console.error('[health-monitor] Gateway unresponsive, forcing restart...')
        forceRestartGateway()
      }
    })
    req.setTimeout(5000, () => {
      req.destroy()
      _healthFailCount++
      console.warn(`[health-monitor] /health timeout (${_healthFailCount}/${HEALTH_MONITOR_MAX_FAILURES})`)
      if (_healthFailCount >= HEALTH_MONITOR_MAX_FAILURES) {
        console.error('[health-monitor] Gateway unresponsive (timeout), forcing restart...')
        forceRestartGateway()
      }
    })
  }, HEALTH_MONITOR_INTERVAL_MS)
}

function forceRestartGateway(): void {
  if (_gatewayRestarting || _shuttingDown) return
  _gatewayRestarting = true
  _healthFailCount = 0
  // Kill the existing gateway process tree
  if (gatewayProcess && !gatewayProcess.killed) {
    console.log('[electron] Killing unresponsive gateway process...')
    if (process.platform === 'win32') {
      try {
        spawn('taskkill', ['/pid', String(gatewayProcess.pid), '/f', '/t'])
      } catch { /* ignore */ }
    } else {
      gatewayProcess.kill('SIGTERM')
    }
    gatewayProcess = null
  }
  // Restart after a short delay (the exit handler would also fire, but
  // _gatewayRestarting prevents a double-restart race).
  setTimeout(() => {
    _gatewayRestarting = false
    if (_shuttingDown) return
    console.log('[electron] Restarting gateway after health-monitor kill...')
    startBackend()
    waitForBackendFullyReady()
      .then(() => {
        console.log('[electron] Gateway restarted (health monitor), reloading window')
        mainWindow?.webContents.reload()
      })
      .catch(() => {
        console.error('[electron] Gateway failed to restart after health-monitor kill')
      })
  }, 2000)
}

// Launcher: agent process manager on LAUNCHER_PORT. Spawned as a second
// `run.exe` instance with `--service launcher`.
// `--no-auto-start`: agents still need a dedicated frozen entry (see docs).
// Plugin services (e.g. websearch) auto-start here when their plugin.json has
// auto_start=true. The launcher uses the Agent Python (installed by the setup
// wizard) to spawn them, NOT the frozen run.exe, so --no-services is no longer
// needed.
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

/** Packaged app: wait until DB/init finished (``ready`` in /health JSON). */
function waitForBackendFullyReady(): Promise<void> {
  const url = `http://127.0.0.1:${BACKEND_PORT}/health`
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + STARTUP_TIMEOUT_MS

    const check = () => {
      const req = http.get(url, (res) => {
        let body = ''
        res.on('data', (chunk: Buffer | string) => { body += chunk.toString() })
        res.on('end', () => {
          if (res.statusCode && res.statusCode < 500) {
            try {
              const data = JSON.parse(body) as { ready?: boolean }
              if (data.ready === true) return resolve()
            } catch {
              /* keep polling */
            }
          }
          schedule()
        })
      })
      req.on('error', schedule)
      req.setTimeout(2000, () => { req.destroy(); schedule() })
    }

    const schedule = () => {
      if (Date.now() >= deadline) {
        return reject(new Error(`Backend not fully ready (${url})`))
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

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    if (!mainWindow || validatedURL.startsWith('data:')) return
    console.error(`[electron] did-fail-load ${validatedURL}: ${errorCode} ${errorDescription}`)
    void mainWindow.loadURL(
      `data:text/html,` +
      `<html><head><meta charset="utf-8"></head>` +
      `<body style="background:#0f0f1a;color:#e2e8f0;font-family:sans-serif;padding:2rem">` +
      `<h2>Failed to load ${APP_DISPLAY_NAME}</h2>` +
      `<p>${errorDescription} (${errorCode})</p>` +
      `<p style="color:#94a3b8">URL: ${validatedURL}</p>` +
      `</body></html>`,
    )
  })

  try {
    const readyLabel = DEV_MODE ? 'Vite dev server' : 'Backend'
    await waitForUrl(HEALTH_URL, readyLabel)
    if (!DEV_MODE) {
      await waitForBackendFullyReady()
      if (!launcherProcess) {
        startLauncher()
      }
      try {
        await waitForPort(LAUNCHER_PORT, 'Launcher')
      } catch {
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
  const trayPath = resolvePackagedAsset('tray.png')
  const fallbackPath = APP_ICON_PATH
  const iconSource = fs.existsSync(trayPath)
    ? trayPath
    : (fs.existsSync(fallbackPath) ? fallbackPath : trayPath)
  const icon = fs.existsSync(iconSource)
    ? nativeImage.createFromPath(iconSource).resize({ width: 16, height: 16 })
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

  const needsAgentRuntime = !DEV_MODE && app.isPackaged && process.platform === 'win32'
  if (needsAgentRuntime && (SETUP_ONLY || !isAgentRuntimeReady())) {
    const result = await runSetupWizard({ setupOnly: SETUP_ONLY })
    if (!result.ok) {
      if (!result.cancelled) {
        dialog.showErrorBox(
          'Agent 运行时安装失败',
          '未能完成 Python 3.11 下载/配置。请检查网络后重试，或联系支持。',
        )
      }
      app.quit()
      return
    }
    if (SETUP_ONLY) {
      app.quit()
      return
    }
  }

  if (!DEV_MODE) {
    startBackend()
    // Launcher starts after gateway is fully ready (see createWindow) to avoid
    // parallel bootstrap_desktop_workspace() races on first launch.
  } else {
    console.log(
      '[electron] DEV_MODE enabled — skipping backend spawn; loading Vite at',
      APP_URL,
      `(gateway API expected on port ${BACKEND_PORT}, launcher on ${LAUNCHER_PORT})`,
    )
  }
  createTray()
  await createWindow()

  // Start periodic backend health monitor (detects hung/crashed gateway
  // during idle and auto-restarts it, preventing "Failed to fetch").
  if (!DEV_MODE) {
    startBackendHealthMonitor()
  }

  // Start background update checker (stable channel). Silent on failure;
  // only notifies the frontend when a newer release is found.
  if (!DEV_MODE) {
    startAutoUpdateChecker('stable')
  }

  app.on('activate', async () => {
    if (BrowserWindow.getAllWindows().length === 0) await createWindow()
  })
})

app.on('window-all-closed', () => {
  // macOS 惯例：关闭窗口不退出，保留后台运行
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  // Signal the exit handler and health monitor to skip auto-restart.
  _shuttingDown = true
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
