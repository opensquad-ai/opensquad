import { app, net } from 'electron'
import { spawn, ChildProcess } from 'child_process'
import fs from 'fs'
import path from 'path'

export interface DownloadProgress {
  percent: number
  transferred: number
  total: number
}

export type UpdateStatusPhase = 'downloading' | 'preparing' | 'launching' | 'shutting-down'

export interface UpdateStatus extends Partial<DownloadProgress> {
  phase: UpdateStatusPhase
}

const TRUSTED_DOWNLOAD_HOSTS = new Set(['github.com', 'objects.githubusercontent.com'])
const UI_SETTLE_MS = 1200
const SHUTDOWN_SETTLE_MS = 900

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function assertTrustedDownloadUrl(rawUrl: string): void {
  let parsed: URL
  try {
    parsed = new URL(rawUrl)
  } catch {
    throw new Error('Invalid download URL')
  }
  if (parsed.protocol !== 'https:') {
    throw new Error('Download URL must use HTTPS')
  }
  const host = parsed.hostname.toLowerCase()
  if (!TRUSTED_DOWNLOAD_HOSTS.has(host) && !host.endsWith('.githubusercontent.com')) {
    throw new Error('Download URL is not from a trusted GitHub host')
  }
  if (host === 'github.com' && !parsed.pathname.includes('/opensquad-ai/opensquad/')) {
    throw new Error('Download URL is not from the OpenSquad repository')
  }
}

function sanitizeFileName(name: string): string {
  const base = path.basename(name).replace(/[^\w.\-() ]+/g, '_')
  return base || 'OpenSquad-update.bin'
}

function downloadOnce(
  url: string,
  dest: string,
  onProgress: (progress: DownloadProgress) => void,
  redirectCount = 0,
): Promise<void> {
  if (redirectCount > 8) {
    return Promise.reject(new Error('Too many redirects while downloading update'))
  }

  return new Promise((resolve, reject) => {
    const request = net.request(url)
    request.on('response', (response) => {
      const status = response.statusCode ?? 0
      const location = response.headers.location
      if (status >= 300 && status < 400 && location) {
        const nextUrl = Array.isArray(location) ? location[0] : location
        downloadOnce(nextUrl, dest, onProgress, redirectCount + 1).then(resolve).catch(reject)
        return
      }
      if (status < 200 || status >= 300) {
        reject(new Error(`Download failed with HTTP ${status}`))
        return
      }

      const totalHeader = response.headers['content-length']
      const total = typeof totalHeader === 'string' ? parseInt(totalHeader, 10) : 0
      let transferred = 0
      const file = fs.createWriteStream(dest)

      response.on('data', (chunk: Buffer) => {
        transferred += chunk.length
        file.write(chunk)
        onProgress({
          percent: total > 0 ? Math.min(100, (transferred / total) * 100) : 0,
          transferred,
          total,
        })
      })

      response.on('end', () => {
        file.end(() => resolve())
      })
      response.on('error', (err) => {
        file.close(() => fs.unlink(dest, () => reject(err)))
      })
    })
    request.on('error', reject)
    request.end()
  })
}

async function downloadInstaller(
  url: string,
  fileName: string,
  onProgress: (progress: DownloadProgress) => void,
): Promise<string> {
  assertTrustedDownloadUrl(url)
  const safeName = sanitizeFileName(fileName)
  const dest = path.join(app.getPath('temp'), safeName)
  await fs.promises.rm(dest, { force: true })
  await downloadOnce(url, dest, onProgress)
  return dest
}

async function launchInstaller(installerPath: string): Promise<void> {
  if (!fs.existsSync(installerPath)) {
    throw new Error('Installer file not found')
  }

  if (process.platform === 'win32') {
    await new Promise<void>((resolve, reject) => {
      const child: ChildProcess = spawn(installerPath, ['/S'], {
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
      })
      child.once('error', reject)
      child.once('spawn', () => resolve())
      child.unref()
    })
    return
  }

  if (process.platform === 'darwin') {
    spawn('open', [installerPath], { detached: true, stdio: 'ignore' }).unref()
    return
  }

  if (process.platform === 'linux') {
    if (installerPath.toLowerCase().endsWith('.appimage')) {
      await fs.promises.chmod(installerPath, 0o755)
      spawn(installerPath, [], { detached: true, stdio: 'ignore' }).unref()
      return
    }
    if (installerPath.toLowerCase().endsWith('.deb')) {
      spawn('xdg-open', [installerPath], { detached: true, stdio: 'ignore' }).unref()
      return
    }
  }

  throw new Error(`Automatic install is not supported for ${process.platform}`)
}

export async function runDesktopUpdate(
  url: string,
  fileName: string,
  onStatus: (status: UpdateStatus) => void,
): Promise<void> {
  onStatus({ phase: 'downloading', percent: 0, transferred: 0, total: 0 })

  const installerPath = await downloadInstaller(url, fileName, (progress) => {
    onStatus({ phase: 'downloading', ...progress })
  })

  onStatus({ phase: 'preparing' })
  await delay(UI_SETTLE_MS)

  onStatus({ phase: 'launching' })
  await delay(UI_SETTLE_MS)

  await launchInstaller(installerPath)

  onStatus({ phase: 'shutting-down' })
  await delay(SHUTDOWN_SETTLE_MS)
  app.quit()
}
