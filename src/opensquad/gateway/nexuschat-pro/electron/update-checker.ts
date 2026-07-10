import { app, net } from 'electron'

// ── GitHub Release update checker ────────────────────────────────────────────
// Polls the GitHub releases API for the latest stable/beta tag and compares
// it against the running app version. The desktop-updater.ts module handles
// the actual download + install; this module only decides WHETHER an update
// is available and WHICH asset to download.
//
// Version comparison follows the project's tag convention:
//   v0.4.10        — stable (three-stage testing passed)
//   v0.4.10beta.3  — CI build, pre-stable
// A stable tag is always considered newer than its beta counterpart
// (v0.4.10 > v0.4.10beta.99) because stable means testing passed.

export interface UpdateInfo {
  hasUpdate: boolean
  currentVersion: string
  latestVersion: string
  downloadUrl?: string
  fileName?: string
  releaseNotes?: string
  isBeta: boolean
  releaseUrl?: string
}

export type UpdateChannel = 'stable' | 'beta'

interface GitHubRelease {
  tag_name: string
  name: string
  body: string
  html_url: string
  assets: Array<{
    name: string
    browser_download_url: string
    size: number
  }>
  prerelease: boolean
}

interface ParsedVersion {
  major: number
  minor: number
  patch: number
  beta: number | null // null = stable; number = beta.N
}

const GITHUB_API = 'https://api.github.com/repos/opensquad-ai/opensquad/releases'
const REQUEST_TIMEOUT_MS = 15000

export function parseVersion(tag: string): ParsedVersion | null {
  // Match: v0.4.10 or v0.4.10beta.3 (case-insensitive)
  const m = tag.match(/^v?(\d+)\.(\d+)\.(\d+)(?:beta\.(\d+))?$/i)
  if (!m) return null
  return {
    major: parseInt(m[1], 10),
    minor: parseInt(m[2], 10),
    patch: parseInt(m[3], 10),
    beta: m[4] ? parseInt(m[4], 10) : null,
  }
}

export function compareVersions(a: ParsedVersion, b: ParsedVersion): number {
  if (a.major !== b.major) return a.major - b.major
  if (a.minor !== b.minor) return a.minor - b.minor
  if (a.patch !== b.patch) return a.patch - b.patch
  // Stable (beta=null) ranks higher than any beta of the same major.minor.patch
  if (a.beta === null && b.beta !== null) return 1
  if (a.beta !== null && b.beta === null) return -1
  if (a.beta !== null && b.beta !== null) return a.beta - b.beta
  return 0
}

function pickAssetForPlatform(
  assets: Array<{ name: string; browser_download_url: string }>,
): { url: string; fileName: string } | null {
  const platform = process.platform
  const arch = process.arch // 'x64' | 'arm64'
  let pattern: RegExp
  if (platform === 'win32') {
    // NSIS installer (not portable) for auto-update
    pattern = /-win-x64-Setup\.exe$/
  } else if (platform === 'darwin') {
    pattern = arch === 'arm64' ? /-mac-arm64\.dmg$/ : /-mac-x64\.dmg$/
  } else if (platform === 'linux') {
    // electron-builder artifactName uses ${arch} → x64 (not x86_64)
    pattern = /-linux-x64\.AppImage$/
  } else {
    return null
  }
  const asset = assets.find((a) => pattern.test(a.name))
  if (!asset) return null
  return { url: asset.browser_download_url, fileName: asset.name }
}

function noUpdate(currentVersion: string): UpdateInfo {
  return {
    hasUpdate: false,
    currentVersion,
    latestVersion: currentVersion,
    isBeta: false,
  }
}

export async function checkForUpdates(channel: UpdateChannel = 'stable'): Promise<UpdateInfo> {
  const currentVersion = app.getVersion()
  const currentParsed = parseVersion(`v${currentVersion}`)
  if (!currentParsed) {
    return noUpdate(currentVersion)
  }

  return new Promise((resolve) => {
    const request = net.request(`${GITHUB_API}?per_page=30`)
    request.setHeader('User-Agent', 'OpenSquad-Desktop-Updater')
    const timer = setTimeout(() => {
      request.abort()
      resolve(noUpdate(currentVersion))
    }, REQUEST_TIMEOUT_MS)

    request.on('response', (response) => {
      const status = response.statusCode ?? 0
      if (status !== 200) {
        clearTimeout(timer)
        resolve(noUpdate(currentVersion))
        return
      }
      let body = ''
      response.on('data', (chunk: Buffer) => {
        body += chunk.toString()
      })
      response.on('end', () => {
        clearTimeout(timer)
        try {
          const releases: GitHubRelease[] = JSON.parse(body)
          let latestStable: GitHubRelease | null = null
          let latestBeta: GitHubRelease | null = null
          let latestStableParsed: ParsedVersion | null = null
          let latestBetaParsed: ParsedVersion | null = null

          for (const rel of releases) {
            const parsed = parseVersion(rel.tag_name)
            if (!parsed) continue
            if (parsed.beta === null) {
              if (!latestStableParsed || compareVersions(parsed, latestStableParsed) > 0) {
                latestStableParsed = parsed
                latestStable = rel
              }
            } else {
              if (!latestBetaParsed || compareVersions(parsed, latestBetaParsed) > 0) {
                latestBetaParsed = parsed
                latestBeta = rel
              }
            }
          }

          // Pick the target release based on channel
          let target: GitHubRelease | null = null
          let targetParsed: ParsedVersion | null = null
          let isBeta = false

          if (channel === 'beta' && latestBeta) {
            target = latestBeta
            targetParsed = latestBetaParsed
            isBeta = true
          } else if (latestStable) {
            target = latestStable
            targetParsed = latestStableParsed
            isBeta = false
          } else if (latestBeta) {
            // No stable found, fall back to beta
            target = latestBeta
            targetParsed = latestBetaParsed
            isBeta = true
          }

          if (!target || !targetParsed) {
            resolve(noUpdate(currentVersion))
            return
          }

          const hasUpdate = compareVersions(targetParsed, currentParsed) > 0
          const asset = pickAssetForPlatform(target.assets)

          resolve({
            hasUpdate,
            currentVersion,
            latestVersion: target.tag_name.replace(/^v/i, ''),
            downloadUrl: asset?.url,
            fileName: asset?.fileName,
            releaseNotes: target.body,
            isBeta,
            releaseUrl: target.html_url,
          })
        } catch {
          resolve(noUpdate(currentVersion))
        }
      })
    })
    request.on('error', () => {
      clearTimeout(timer)
      resolve(noUpdate(currentVersion))
    })
    request.end()
  })
}
