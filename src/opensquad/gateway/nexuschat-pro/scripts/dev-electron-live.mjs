/**
 * Electron 开发模式启动器
 *
 * - 设置 ELECTRON_DEV=1，跳过 PyInstaller 后端
 * - 窗口加载 Vite 开发服务器（默认 5173，支持 HMR）
 * - Gateway 端口仅用于 API（由 Vite proxy 转发）
 *
 * 用法：先在一个终端运行 `uv run opensquad start`，再运行 `npm run electron:dev:live`
 */
import { spawn } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND_DIR = path.resolve(__dirname, '..')
const PROJECT_SRC = path.resolve(FRONTEND_DIR, '../../..')

function tryLoadConfig(basePath) {
  for (const name of ['system_config.json', 'system_config.example.json']) {
    const fullPath = path.join(basePath, name)
    if (!existsSync(fullPath)) continue
    try {
      return JSON.parse(readFileSync(fullPath, 'utf-8'))
    } catch {
      /* ignore */
    }
  }
  return null
}

function resolveSystemConfig() {
  if (process.env.OPENSQUAD_WORKSPACE) {
    const cfg = tryLoadConfig(process.env.OPENSQUAD_WORKSPACE)
    if (cfg) return cfg
  }

  try {
    const lastWs = path.join(os.homedir(), '.opensquad', 'last_workspace.json')
    if (existsSync(lastWs)) {
      const { last_workspace: wsPath } = JSON.parse(readFileSync(lastWs, 'utf-8'))
      if (wsPath) {
        const cfg = tryLoadConfig(wsPath)
        if (cfg) return cfg
      }
    }
  } catch {
    /* ignore */
  }

  return tryLoadConfig(PROJECT_SRC) ?? tryLoadConfig(path.resolve(PROJECT_SRC, '..'))
}

function resolvePorts() {
  const cfg = resolveSystemConfig()
  return {
    gatewayPort: Number(process.env.OPENSQUAD_PORT || cfg?.ports?.gateway || 9510),
    frontendPort: Number(
      process.env.OPENSQUAD_FRONTEND_PORT
        || process.env.VITE_DEV_PORT
        || cfg?.ports?.frontend
        || 5173,
    ),
  }
}

function run(command, args, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: FRONTEND_DIR,
      env,
      stdio: 'inherit',
      shell: process.platform === 'win32',
    })
    child.on('error', reject)
    child.on('exit', (code) => {
      if (code === 0) resolve()
      else reject(new Error(`${command} exited with code ${code}`))
    })
  })
}

const { gatewayPort, frontendPort } = resolvePorts()
const env = {
  ...process.env,
  ELECTRON_DEV: '1',
  OPENSQUAD_PORT: String(gatewayPort),
  OPENSQUAD_FRONTEND_PORT: String(frontendPort),
  VITE_DEV_PORT: String(frontendPort),
}

console.log('')
console.log('============================================================')
console.log('  OpenSquad — Electron Live Dev')
console.log('============================================================')
console.log(`  Vite (window) : http://127.0.0.1:${frontendPort}`)
console.log(`  Gateway (API) : http://127.0.0.1:${gatewayPort}`)
console.log('  Prerequisite  : `uv run opensquad start` (Vite + Gateway must be running)')
console.log('  Hot reload    : React (Vite HMR) + Python (uvicorn reload)')
console.log('============================================================')
console.log('')

const electronBin = path.join(
  FRONTEND_DIR,
  'node_modules',
  '.bin',
  process.platform === 'win32' ? 'electron.cmd' : 'electron',
)

try {
  await run('node', ['scripts/compile-electron.mjs'], env)
  await run(electronBin, ['dist-electron/main.cjs'], env)
} catch (err) {
  console.error('[electron:dev:live] Failed:', err.message)
  process.exit(1)
}
