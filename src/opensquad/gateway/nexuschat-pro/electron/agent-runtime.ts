import { app } from 'electron'
import fs from 'fs'
import path from 'path'
import https from 'https'
import http from 'http'
import { spawn } from 'child_process'
import os from 'os'

export const AGENT_RUNTIME_MANIFEST = 'agent-runtime.json'

const PYTHON_VERSION = '3.11.9'
const PYTHON_EMBED_URL =
  `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`

export interface AgentRuntimeManifest {
  python: string
  version: string
  installed_at: string
  source: 'embed'
}

export function runtimeInstallDir(): string {
  return path.join(process.env.LOCALAPPDATA ?? app.getPath('home'), 'OpenSquad', 'runtime', 'python311')
}

export function runtimePythonExe(): string {
  return path.join(runtimeInstallDir(), 'python.exe')
}

export function manifestFilePath(): string {
  return path.join(app.getPath('userData'), AGENT_RUNTIME_MANIFEST)
}

export function readAgentRuntimeManifest(): AgentRuntimeManifest | null {
  const file = manifestFilePath()
  if (!fs.existsSync(file)) return null
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf-8')) as AgentRuntimeManifest
    if (data?.python && fs.existsSync(data.python)) return data
  } catch {
    /* ignore */
  }
  return null
}

export function isAgentRuntimeReady(): boolean {
  if (process.platform !== 'win32') return true
  const exe = readAgentRuntimeManifest()?.python ?? runtimePythonExe()
  return fs.existsSync(exe)
}

function writeManifest(pythonExe: string): AgentRuntimeManifest {
  const manifest: AgentRuntimeManifest = {
    python: pythonExe,
    version: PYTHON_VERSION,
    installed_at: new Date().toISOString(),
    source: 'embed',
  }
  fs.mkdirSync(path.dirname(manifestFilePath()), { recursive: true })
  fs.writeFileSync(manifestFilePath(), JSON.stringify(manifest, null, 2), 'utf-8')
  return manifest
}

function downloadFile(url: string, dest: string, onProgress?: (pct: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest)
    const fetch = (currentUrl: string, redirects = 0) => {
      if (redirects > 8) {
        reject(new Error('Too many redirects while downloading Python runtime'))
        return
      }
      const lib = currentUrl.startsWith('https:') ? https : http
      lib.get(currentUrl, (res) => {
        if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          fetch(res.headers.location, redirects + 1)
          return
        }
        if (res.statusCode !== 200) {
          reject(new Error(`Download failed: HTTP ${res.statusCode}`))
          return
        }
        const total = Number(res.headers['content-length'] ?? 0)
        let received = 0
        res.on('data', (chunk: Buffer) => {
          received += chunk.length
          if (total > 0 && onProgress) onProgress(Math.min(100, Math.round((received / total) * 100)))
        })
        res.pipe(file)
        file.on('finish', () => file.close(() => resolve()))
      }).on('error', reject)
    }
    fetch(url)
  })
}

async function extractZip(zipPath: string, destDir: string): Promise<void> {
  fs.mkdirSync(destDir, { recursive: true })
  if (process.platform === 'win32') {
    await new Promise<void>((resolve, reject) => {
      const ps = spawn(
        'powershell.exe',
        [
          '-NoProfile',
          '-Command',
          `Expand-Archive -LiteralPath '${zipPath.replace(/'/g, "''")}' -DestinationPath '${destDir.replace(/'/g, "''")}' -Force`,
        ],
        { stdio: 'inherit' },
      )
      ps.on('error', reject)
      ps.on('exit', (code) => (code === 0 ? resolve() : reject(new Error(`Expand-Archive exited ${code}`))))
    })
    return
  }
  await new Promise<void>((resolve, reject) => {
    const unzip = spawn('unzip', ['-o', zipPath, '-d', destDir], { stdio: 'inherit' })
    unzip.on('error', reject)
    unzip.on('exit', (code) => (code === 0 ? resolve() : reject(new Error(`unzip exited ${code}`))))
  })
}

function configureEmbedPython(installDir: string, log: (line: string) => void): void {
  const pthName = fs.readdirSync(installDir).find((f) => f.endsWith('._pth'))
  if (!pthName) return
  const pthPath = path.join(installDir, pthName)
  let content = fs.readFileSync(pthPath, 'utf-8')
  if (!content.includes('import site')) {
    content = content.trimEnd() + '\nimport site\n'
    fs.writeFileSync(pthPath, content, 'utf-8')
    log(`Updated ${pthName} (enabled import site)`)
  }
}

async function verifyPython(exe: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const proc = spawn(exe, ['-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")'], {
      windowsHide: true,
    })
    let out = ''
    proc.stdout?.on('data', (d: Buffer) => { out += d.toString() })
    proc.on('error', reject)
    proc.on('exit', (code) => {
      if (code === 0 && out.trim()) resolve(out.trim())
      else reject(new Error(`Python verification failed (code ${code})`))
    })
  })
}

export type SetupStepId =
  | 'prepare'
  | 'download'
  | 'extract'
  | 'configure'
  | 'verify'
  | 'manifest'
  | 'done'

export interface SetupStep {
  id: SetupStepId
  title: string
  run: (ctx: SetupContext) => Promise<void>
}

export interface SetupContext {
  log: (line: string) => void
  onStepProgress: (stepId: SetupStepId, pct: number) => void
  cancelled: () => boolean
  state: { zipPath?: string }
}

export const SETUP_STEPS: SetupStep[] = [
  {
    id: 'prepare',
    title: '准备 Agent 运行时目录',
    async run({ log }) {
      const dir = runtimeInstallDir()
      fs.mkdirSync(dir, { recursive: true })
      log(`Runtime directory: ${dir}`)
    },
  },
  {
    id: 'download',
    title: `下载 Python ${PYTHON_VERSION}（Agent 专用）`,
    async run({ log, onStepProgress, cancelled, state }) {
      const zipPath = path.join(os.tmpdir(), `opensquad-python-${PYTHON_VERSION}-embed-amd64.zip`)
      log(`Downloading from ${PYTHON_EMBED_URL}`)
      await downloadFile(PYTHON_EMBED_URL, zipPath, (pct) => {
        if (!cancelled()) onStepProgress('download', pct)
      })
      if (cancelled()) throw new Error('Cancelled')
      log(`Saved to ${zipPath}`)
      state.zipPath = zipPath
    },
  },
  {
    id: 'extract',
    title: '解压 Python 运行时',
    async run({ log, cancelled, state }) {
      if (cancelled()) throw new Error('Cancelled')
      const zipPath = state.zipPath
      if (!zipPath) throw new Error('Missing downloaded zip')
      const dir = runtimeInstallDir()
      log(`Extracting to ${dir}`)
      await extractZip(zipPath, dir)
      try { fs.unlinkSync(zipPath) } catch { /* ignore */ }
    },
  },
  {
    id: 'configure',
    title: '配置 Python 环境',
    async run({ log }) {
      configureEmbedPython(runtimeInstallDir(), log)
    },
  },
  {
    id: 'verify',
    title: '验证 Agent Python',
    async run({ log }) {
      const exe = runtimePythonExe()
      if (!fs.existsSync(exe)) throw new Error(`python.exe not found at ${exe}`)
      const version = await verifyPython(exe)
      log(`Verified Python ${version} at ${exe}`)
    },
  },
  {
    id: 'manifest',
    title: '写入运行时配置',
    async run({ log }) {
      const manifest = writeManifest(runtimePythonExe())
      log(`Manifest: ${manifestFilePath()}`)
      log(`Agent Python: ${manifest.python}`)
    },
  },
  {
    id: 'done',
    title: '安装完成',
    async run({ log }) {
      log('Agent runtime is ready.')
    },
  },
]

export async function installAgentRuntime(ctx: SetupContext): Promise<void> {
  if (!ctx.state) ctx.state = {}
  for (const step of SETUP_STEPS) {
    if (ctx.cancelled()) throw new Error('Cancelled')
    ctx.onStepProgress(step.id, 0)
    await step.run(ctx)
    ctx.onStepProgress(step.id, 100)
  }
}

export function agentPythonForBackendEnv(): string | undefined {
  const manifest = readAgentRuntimeManifest()
  if (manifest?.python && fs.existsSync(manifest.python)) return manifest.python
  const fallback = runtimePythonExe()
  return fs.existsSync(fallback) ? fallback : undefined
}
