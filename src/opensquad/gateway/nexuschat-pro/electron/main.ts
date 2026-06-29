import { app, BrowserWindow, dialog, Menu, Tray, nativeImage, ipcMain } from 'electron'
import { spawn, ChildProcess } from 'child_process'
import path from 'path'
import http from 'http'
import fs from 'fs'
import { buildElectronPopupMenus, isElectronMenuId } from './electron-menus'

// ── 常量 ─────────────────────────────────────────────────────────────────────
// Backend port: read from environment variable (set by docker-entrypoint.sh or
// launcher), fallback to 9510 (the default gateway port in system_config).
const BACKEND_PORT  = parseInt(process.env.OPENSQUAD_PORT || '9510', 10)
const FRONTEND_PORT = parseInt(
  process.env.OPENSQUAD_FRONTEND_PORT || process.env.VITE_DEV_PORT || '5173',
  10,
)
const DEV_MODE      = process.env.ELECTRON_DEV === '1' || process.env.ELECTRON_DEV === 'true'
const HEALTH_URL    = DEV_MODE
  ? `http://127.0.0.1:${FRONTEND_PORT}/`
  : `http://127.0.0.1:${BACKEND_PORT}/health`
const APP_URL       = DEV_MODE
  ? `http://127.0.0.1:${FRONTEND_PORT}`
  : `http://127.0.0.1:${BACKEND_PORT}`
const STARTUP_TIMEOUT_MS = 45_000   // 后端最长等待时间

// Application icon — Windows expects an .ico, other platforms accept PNG.
// Path is resolved relative to the compiled main.cjs (dist-electron/).
const APP_ICON_PATH = process.platform === 'win32'
  ? path.join(__dirname, '..', 'assets', 'icon.ico')
  : path.join(__dirname, '..', 'assets', 'icon.png')

let backendProcess: ChildProcess | null = null
let mainWindow:     BrowserWindow | null = null
let tray:           Tray | null = null
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
}

// ── 获取各平台后端二进制路径 ──────────────────────────────────────────────────
function getBackendExe(): string {
  // 打包后: resources/ 与 app.asar 同级
  // 开发时: nexuschat-pro/resources/
  const resourcesDir = app.isPackaged
    ? process.resourcesPath
    : path.join(__dirname, '..', 'resources')

  const map: Record<string, string> = {
    win32:  path.join(resourcesDir, 'backend-win',   'run.exe'),
    darwin: path.join(resourcesDir, 'backend-mac',   'run'),
    linux:  path.join(resourcesDir, 'backend-linux', 'run'),
  }
  return map[process.platform] ?? map['linux']
}

// ── 启动 Python 后端 ──────────────────────────────────────────────────────────
function startBackend(): void {
  const exe = getBackendExe()

  if (!fs.existsSync(exe)) {
    dialog.showErrorBox(
      'Backend not found',
      `Expected binary at:\n${exe}\n\nPlease rebuild the backend.`
    )
    app.quit()
    return
  }

  // userData 作为运行时数据根目录（chat.db、logs 等写入此处）
  const userDataDir = app.getPath('userData')
  fs.mkdirSync(userDataDir, { recursive: true })

  backendProcess = spawn(exe, [], {
    cwd: userDataDir,
    detached: false,
    env: {
      ...process.env,
      // 告知后端使用哪个目录写运行时数据
      OPENSQUAD_USER_DATA: userDataDir,
      // 禁用 uvicorn 热重载（打包环境不支持）
      OPENSQUAD_RELOAD: '0',
    },
  })

  backendProcess.stdout?.on('data', (d: Buffer) =>
    console.log('[backend]', d.toString().trimEnd()))
  backendProcess.stderr?.on('data', (d: Buffer) =>
    console.error('[backend:err]', d.toString().trimEnd()))
  backendProcess.on('exit', (code, signal) =>
    console.log(`[backend] exited  code=${code} signal=${signal}`))
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

// ── 创建主窗口 ────────────────────────────────────────────────────────────────
async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width:     1280,
    height:    800,
    minWidth:  900,
    minHeight: 600,
    title:     USE_CUSTOM_TITLEBAR ? '' : 'NexusChat Pro',
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
    `<div>Starting NexusChat Pro…</div>` +
    `</body></html>`
  )
  mainWindow.show()

  try {
    const readyLabel = DEV_MODE ? 'Vite dev server' : 'Backend'
    await waitForUrl(HEALTH_URL, readyLabel)
    await mainWindow.loadURL(APP_URL)
    if (!USE_CUSTOM_TITLEBAR) {
      mainWindow.setTitle('NexusChat Pro')
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
  tray.setToolTip('NexusChat Pro')
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
  } else {
    console.log(
      '[electron] DEV_MODE enabled — skipping backend spawn; loading Vite at',
      APP_URL,
      `(gateway API expected on port ${BACKEND_PORT})`,
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
  if (backendProcess && !backendProcess.killed) {
    console.log('[electron] Terminating backend process…')
    // Windows 用 taskkill 确保子进程树全部结束
    if (process.platform === 'win32') {
      try {
        spawn('taskkill', ['/pid', String(backendProcess.pid), '/f', '/t'])
      } catch { /* ignore */ }
    } else {
      backendProcess.kill('SIGTERM')
    }
    backendProcess = null
  }
})
