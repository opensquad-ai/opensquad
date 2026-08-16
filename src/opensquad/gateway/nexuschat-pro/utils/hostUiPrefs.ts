/**
 * Host-persisted UI prefs so Vite (:5173) and packaged Gateway (:9555)
 * share theme / language / last agent despite origin-scoped localStorage.
 */
import i18n from '../i18n';
import { getAuthToken } from '../services/api';
import {
  applyThemePrefs,
  loadThemePrefs,
  sanitizePrefs,
  THEME_STORAGE_KEY,
} from './themeStore';
import type { ThemePrefs } from './themeEngine';

export type HostUiPrefs = {
  savedAt?: number;
  status?: string;
  theme?: Partial<ThemePrefs> | null;
  lang?: string | null;
  uiMode?: string | null;
  selectedAgent?: string | null;
  view?: string | null;
};

const LANG_KEY = 'opensquad_lang';
const UI_MODE_KEY = 'ai_chat_ui_mode';
const SELECTED_AGENT_KEY = 'nexus_selected_agent';
const VIEW_KEY = 'nexus_view';

let hostReady = false;
let pushTimer: ReturnType<typeof setTimeout> | null = null;

export function markHostUiPrefsReady(): void {
  hostReady = true;
}

export function collectLocalHostUiPrefs(): HostUiPrefs {
  let theme: ThemePrefs | undefined;
  try {
    // Do not send implicit ink-green defaults from a fresh origin — that
    // would overwrite the other port's saved theme on first packaged login.
    if (localStorage.getItem(THEME_STORAGE_KEY)) {
      theme = loadThemePrefs();
    }
  } catch {
    theme = undefined;
  }
  let lang: string | null = null;
  let uiMode: string | null = null;
  let selectedAgent: string | null = null;
  let view: string | null = null;
  try {
    lang = localStorage.getItem(LANG_KEY);
    uiMode = localStorage.getItem(UI_MODE_KEY);
    selectedAgent = localStorage.getItem(SELECTED_AGENT_KEY);
    view = localStorage.getItem(VIEW_KEY);
  } catch {
    /* ignore */
  }
  return {
    savedAt: Date.now(),
    theme,
    lang,
    uiMode,
    selectedAgent,
    view,
  };
}

/** Host wins for any field it actually has; local fills the rest. */
export function pickHydratePrefs(host: HostUiPrefs | null, local: HostUiPrefs): HostUiPrefs {
  if (!host) return { ...local };
  const out: HostUiPrefs = { ...local, savedAt: Number(host.savedAt) || local.savedAt };
  if (host.theme && typeof host.theme === 'object') out.theme = host.theme;
  if (host.lang === 'zh' || host.lang === 'en') out.lang = host.lang;
  if (host.uiMode === 'classic' || host.uiMode === 'solo') out.uiMode = host.uiMode;
  if (typeof host.selectedAgent === 'string' && host.selectedAgent.trim()) {
    out.selectedAgent = host.selectedAgent.trim();
  }
  if (typeof host.view === 'string' && host.view.trim()) out.view = host.view.trim();
  return out;
}

function writeLocalPrefs(prefs: HostUiPrefs): void {
  try {
    if (prefs.theme && typeof prefs.theme === 'object') {
      const next = sanitizePrefs(prefs.theme);
      localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(next));
      applyThemePrefs(next);
    }
    if (prefs.lang === 'zh' || prefs.lang === 'en') {
      localStorage.setItem(LANG_KEY, prefs.lang);
      void i18n.changeLanguage(prefs.lang);
    }
    if (prefs.uiMode === 'classic' || prefs.uiMode === 'solo') {
      localStorage.setItem(UI_MODE_KEY, prefs.uiMode);
    }
    if (typeof prefs.selectedAgent === 'string' && prefs.selectedAgent.trim()) {
      localStorage.setItem(SELECTED_AGENT_KEY, prefs.selectedAgent.trim());
    }
    if (typeof prefs.view === 'string' && prefs.view.trim()) {
      localStorage.setItem(VIEW_KEY, prefs.view.trim());
    }
  } catch {
    /* ignore */
  }
}

export async function fetchHostUiPrefs(signal?: AbortSignal): Promise<HostUiPrefs | null> {
  const res = await fetch('/api/ui-prefs', { cache: 'no-store', signal });
  if (!res.ok) return null;
  const data = await res.json();
  return data && typeof data === 'object' ? (data as HostUiPrefs) : null;
}

export async function hydrateHostUiPrefs(signal?: AbortSignal): Promise<HostUiPrefs | null> {
  let host: HostUiPrefs | null = null;
  try {
    host = await fetchHostUiPrefs(signal);
  } catch {
    host = null;
  }
  const local = collectLocalHostUiPrefs();
  const picked = pickHydratePrefs(host, local);
  writeLocalPrefs(picked);
  markHostUiPrefsReady();
  return host;
}

async function pushNow(): Promise<void> {
  if (!hostReady) return;
  const token = getAuthToken();
  if (!token) return;
  const body = collectLocalHostUiPrefs();
  try {
    await fetch('/api/ui-prefs', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    });
  } catch {
    /* ignore */
  }
}

/** Debounced PUT of current local prefs. No-op until hydrate + login. */
export function schedulePushHostUiPrefs(delayMs = 400): void {
  if (!hostReady) return;
  if (pushTimer) clearTimeout(pushTimer);
  pushTimer = setTimeout(() => {
    pushTimer = null;
    void pushNow();
  }, delayMs);
}

/** Call after login so a token exists for the first host write. */
export function flushHostUiPrefs(): void {
  markHostUiPrefsReady();
  schedulePushHostUiPrefs(0);
}
