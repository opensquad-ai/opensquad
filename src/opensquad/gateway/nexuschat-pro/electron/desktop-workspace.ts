import fs from 'fs'
import path from 'path'

/** Must match opensquad/workspace_utils.py DESKTOP_WORKSPACE_CONFIG */
export const DESKTOP_WORKSPACE_CONFIG = 'desktop-workspace.json'

export interface DesktopWorkspaceConfig {
  workspace_path: string
  updated_at?: string
}

function isValidWorkspaceDir(dir: string): boolean {
  if (!dir || !fs.existsSync(dir)) return false
  // Prefer the formal marker; also accept a chat DB (accounts) so Mac
  // recovery from a legacy userData folder still counts as valid.
  return (
    fs.existsSync(path.join(dir, '.opensquad')) ||
    fs.existsSync(path.join(dir, 'gateway', 'backend', 'chat.db'))
  )
}

/** Read the user-chosen workspace path from appData; default to appData itself. */
export function resolveDesktopWorkspace(appDataDir: string): string {
  const cfgPath = path.join(appDataDir, DESKTOP_WORKSPACE_CONFIG)
  if (!fs.existsSync(cfgPath)) {
    return appDataDir
  }
  try {
    const raw = JSON.parse(fs.readFileSync(cfgPath, 'utf-8')) as DesktopWorkspaceConfig
    const configured = (raw.workspace_path || '').trim()
    if (configured && isValidWorkspaceDir(configured)) {
      return path.resolve(configured)
    }
    if (configured) {
      console.warn(
        `[electron] Configured workspace invalid or missing (${configured}); using app data dir`,
      )
      writeDesktopWorkspace(appDataDir, appDataDir)
    }
  } catch (err) {
    console.warn('[electron] Failed to read desktop-workspace.json:', err)
  }
  return appDataDir
}

export function writeDesktopWorkspace(appDataDir: string, workspacePath: string): void {
  fs.mkdirSync(appDataDir, { recursive: true })
  const payload: DesktopWorkspaceConfig = {
    workspace_path: path.resolve(workspacePath),
    updated_at: new Date().toISOString(),
  }
  fs.writeFileSync(
    path.join(appDataDir, DESKTOP_WORKSPACE_CONFIG),
    JSON.stringify(payload, null, 2),
    'utf-8',
  )
}
