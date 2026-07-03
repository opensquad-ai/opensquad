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
  let changed = false

  // 1. Enable `import site` so site-packages processing runs (needed for pip
  //    and for installed packages to be importable).
  if (!content.includes('import site')) {
    content = content.trimEnd() + '\nimport site\n'
    changed = true
  }

  // 2. Explicitly add `Lib\site-packages` to the _pth file. When a ._pth file
  //    is present, Python **ignores PYTHONPATH** — only paths listed in the
  //    _pth are searched. `import site` alone should add site-packages, but
  //    being explicit is a belt-and-suspenders fix: some embed builds have a
  //    site.py that doesn't add Lib\site-packages when _pth is active, which
  //    causes pip to install packages that are then not importable.
  if (!content.includes('Lib\\site-packages')) {
    content = content.trimEnd() + '\nLib\\site-packages\n'
    changed = true
  }

  if (changed) {
    fs.writeFileSync(pthPath, content, 'utf-8')
    log(`Updated ${pthName} (enabled import site + Lib\\site-packages)`)
  }
}

// ── System Python probing (venv mode) ────────────────────────────────────────
// Instead of always downloading the 12MB embed zip, probe for a system
// Python 3.11+ and create a venv from it. venv has three big advantages over
// embed:
//   1. venv ships with pip (via ensurepip) — no get-pip.py fallback hell
//   2. venv has a working site.py — no _pth configuration needed
//   3. venv's python.exe is a real interpreter, not a stripped embed
// The trade-off: venv depends on the system Python install (uninstalling
// it breaks the venv). We accept this for the much better dep-install UX.

async function probeSystemPython(): Promise<string[]> {
  const found: string[] = []
  // Probe order: py launcher (Windows preferred) → direct commands.
  // IMPORTANT: Only accept Python 3.11. The PyInstaller bundle is compiled
  // with 3.11, and _internal/ contains 3.11-compiled .pyd files. If a venv
  // created from 3.12 falls back to importing from _internal/ (e.g.
  // when a pip-installed dep is missing), it loads python311.dll and crashes
  // with "Module use of python311.dll conflicts with this version of Python".
  // 3.13+ is also incompatible (different ABI). Only 3.11 is safe.
  const probes: Array<{ cmd: string; args: string[] }> = [
    { cmd: 'py', args: ['-3.11', '-c', 'import sys; print(sys.executable)'] },
    { cmd: 'python3.11', args: ['-c', 'import sys; print(sys.executable)'] },
  ]

  for (const { cmd, args } of probes) {
    try {
      const result = await new Promise<{ ok: boolean; output: string }>((resolve) => {
        const proc = spawn(cmd, args, { windowsHide: true })
        let out = ''
        const timer = setTimeout(() => {
          proc.kill()
          resolve({ ok: false, output: '' })
        }, 8000)
        proc.stdout?.on('data', (d: Buffer) => { out += d.toString() })
        proc.on('error', () => {
          clearTimeout(timer)
          resolve({ ok: false, output: '' })
        })
        proc.on('exit', (code) => {
          clearTimeout(timer)
          resolve({ ok: code === 0, output: out.trim() })
        })
      })
      if (result.ok && result.output && fs.existsSync(result.output)) {
        // Verify version is exactly 3.11 (the PyInstaller bundle version).
        const versionOk = await verifyPythonVersion(result.output, [3, 11], [3, 11])
        if (versionOk && !found.includes(result.output)) {
          found.push(result.output)
        }
      }
    } catch {
      /* ignore — try next probe */
    }
  }
  return found
}

async function verifyPythonVersion(
  exe: string,
  minVersion: [number, number],
  maxVersion: [number, number],
): Promise<boolean> {
  return new Promise((resolve) => {
    const proc = spawn(
      exe,
      ['-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
      { windowsHide: true },
    )
    let out = ''
    proc.stdout?.on('data', (d: Buffer) => { out += d.toString() })
    proc.on('error', () => resolve(false))
    proc.on('exit', (code) => {
      if (code !== 0) {
        resolve(false)
        return
      }
      const parts = out.trim().split('.').map((n) => parseInt(n, 10))
      if (parts.length < 2 || parts.some(isNaN)) {
        resolve(false)
        return
      }
      const [major, minor] = parts
      const minOk = major > minVersion[0] || (major === minVersion[0] && minor >= minVersion[1])
      const maxOk = major < maxVersion[0] || (major === maxVersion[0] && minor <= maxVersion[1])
      resolve(minOk && maxOk)
    })
  })
}

async function createVenvFromSystemPython(
  systemPython: string,
  installDir: string,
  log: (line: string) => void,
): Promise<void> {
  log(`Creating venv from ${systemPython}...`)
  // `--clear` ensures a fresh venv even if the dir has stale files from a
  // previous embed install (e.g. user uninstalled embed-mode then reinstalled
  // in venv-mode). Without --clear, venv creation can fail on existing dir.
  await new Promise<void>((resolve, reject) => {
    const proc = spawn(systemPython, ['-m', 'venv', '--clear', installDir], {
      stdio: 'inherit',
      windowsHide: true,
    })
    proc.on('error', reject)
    proc.on('exit', (code) => {
      if (code === 0) resolve()
      else reject(new Error(`venv creation exited with code ${code}`))
    })
  })
  log(`venv created at ${installDir}`)
}

// venv's python.exe lives under Scripts/ on Windows, not at the install root.
function venvPythonExe(installDir: string): string {
  return process.platform === 'win32'
    ? path.join(installDir, 'Scripts', 'python.exe')
    : path.join(installDir, 'bin', 'python')
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
  | 'detect-python'
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
  state: {
    zipPath?: string
    // venv-mode state (set by 'detect-python' step).
    // When useVenv=true, download/extract are skipped and configure
    // creates a venv from systemPython instead of writing a _pth file.
    // Optional here — installAgentRuntime() defaults it to false so
    // callers can pass `{}` without TypeScript complaining.
    systemPython?: string
    useVenv?: boolean
  }
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
    id: 'detect-python',
    title: '探测系统 Python',
    async run({ log, state }) {
      // Probe for a system Python 3.11 before falling back to the
      // 12MB embed download. venv-mode skips download/extract entirely
      // and creates a venv (which ships with pip via ensurepip, no
      // get-pip.py fallback hell, no _pth configuration needed).
      const candidates = await probeSystemPython()
      if (candidates.length > 0) {
        state.systemPython = candidates[0]
        state.useVenv = true
        log(`Found system Python: ${candidates[0]}`)
        log('Will create venv (skips 12MB embed download).')
      } else {
        state.useVenv = false
        log('No system Python 3.11 found, will download embed.')
      }
    },
  },
  {
    id: 'download',
    title: `下载 Python ${PYTHON_VERSION}（Agent 专用）`,
    async run({ log, onStepProgress, cancelled, state }) {
      if (state.useVenv) {
        log('Skipped — using venv from system Python.')
        return
      }
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
      if (state.useVenv) {
        log('Skipped — using venv from system Python.')
        return
      }
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
    async run({ log, state }) {
      if (state.useVenv && state.systemPython) {
        // venv mode: create a venv from the system Python. venv ships
        // with pip (via ensurepip) and has a working site.py, so no
        // _pth configuration is needed.
        await createVenvFromSystemPython(state.systemPython, runtimeInstallDir(), log)
      } else {
        // embed mode: enable `import site` + Lib\site-packages in _pth
        // so that pip-installed packages are importable.
        configureEmbedPython(runtimeInstallDir(), log)
      }
    },
  },
  {
    id: 'verify',
    title: '验证 Agent Python',
    async run({ log, state }) {
      const exe = state.useVenv
        ? venvPythonExe(runtimeInstallDir())
        : runtimePythonExe()
      if (!fs.existsSync(exe)) throw new Error(`python.exe not found at ${exe}`)
      const version = await verifyPython(exe)
      log(`Verified Python ${version} at ${exe}`)
    },
  },
  {
    id: 'manifest',
    title: '写入运行时配置',
    async run({ log, state }) {
      const exe = state.useVenv
        ? venvPythonExe(runtimeInstallDir())
        : runtimePythonExe()
      const manifest = writeManifest(exe)
      log(`Manifest: ${manifestFilePath()}`)
      log(`Agent Python: ${manifest.python}`)
      log(`Mode: ${state.useVenv ? 'venv (from system Python)' : 'embed'}`)
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
  if (!ctx.state) ctx.state = { useVenv: false }
  // Default useVenv to false so the 'detect-python' step always runs the
  // probe (state mutates in-place across steps).
  if (ctx.state.useVenv === undefined) ctx.state.useVenv = false
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
  // Fallbacks: try embed path first, then venv path.
  // (Old installs may have either layout; manifest is the source of truth
  // but we cover the case where it's missing/corrupt.)
  const embedPath = runtimePythonExe()
  if (fs.existsSync(embedPath)) return embedPath
  const venvPath = venvPythonExe(runtimeInstallDir())
  return fs.existsSync(venvPath) ? venvPath : undefined
}
