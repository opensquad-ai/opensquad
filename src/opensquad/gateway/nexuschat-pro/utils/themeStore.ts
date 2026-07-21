/**
 * Theme preferences store: load / save / apply / migrate legacy chat_theme.
 */

import {
  AppearanceMode,
  CONTRAST_MAX,
  CONTRAST_MIN,
  DEFAULT_THEME_PREFS,
  FONT_SIZE_MAX,
  FONT_SIZE_MIN,
  PURITY_MAX,
  PURITY_MIN,
  PRESET_METAS,
  ThemePrefs,
  ThemePresetId,
  buildPalette,
  clamp,
  getPresetSurfaceHue,
  normalizeHex,
  resolveAppearance,
} from './themeEngine';

export const THEME_STORAGE_KEY = 'opensquad_theme_v2';
export const LEGACY_THEME_KEY = 'chat_theme';
export const THEME_PREFS_EVENT = 'themePrefsChanged';
export const OPEN_THEME_SETTINGS_EVENT = 'openThemeSettings';

const LEGACY_THEME_CLASSES = [
  'default',
  'warm',
  'coffee',
  'coffee-dark',
  'red',
  'orange',
  'pink',
  'yellow',
  'green',
  'cyan',
  'blue',
  'purple',
  'midnight',
  'opencode',
  'tokyonight',
  'catppuccin',
  'catppuccin-macchiato',
  'nord',
  'onedark',
  'everforest',
  'gruvbox',
  'kanagawa',
  'sakura',
  'dracula',
  'ayu',
  'monokai',
  'matrix',
] as const;

const LEGACY_MAP: Record<string, Partial<ThemePrefs>> = {
  default: { mode: 'light', preset: 'ink-green', primary: '#2D4739' },
  warm: { mode: 'light', preset: 'ink-green', primary: '#E07A5F', purity: 45 },
  coffee: { mode: 'light', preset: 'luxury', primary: '#6B5A3E' },
  sakura: { mode: 'light', preset: 'violet', primary: '#C48B9F', purity: 35 },
  yellow: { mode: 'light', preset: 'luxury', primary: '#A68B3C' },
  orange: { mode: 'light', preset: 'luxury', primary: '#B56A3A' },
  pink: { mode: 'light', preset: 'violet', primary: '#A66B7C' },
  red: { mode: 'light', preset: 'ink-green', primary: '#8B3A3A', purity: 40 },
  blue: { mode: 'light', preset: 'lake-blue', primary: '#3D6B8A' },
  cyan: { mode: 'light', preset: 'lake-blue', primary: '#3A7A7A' },
  green: { mode: 'light', preset: 'ink-green', primary: '#3A6B4A' },
  purple: { mode: 'light', preset: 'violet', primary: '#5C4A6E' },
  'coffee-dark': { mode: 'dark', preset: 'luxury', primary: '#6B5A3E' },
  midnight: { mode: 'dark', preset: 'minimal', primary: '#2F4A6E' },
  opencode: { mode: 'dark', preset: 'minimal', primary: '#4A5A8A' },
  tokyonight: { mode: 'dark', preset: 'minimal', primary: '#3D5A8A' },
  catppuccin: { mode: 'dark', preset: 'violet', primary: '#5C4A6E' },
  'catppuccin-macchiato': { mode: 'dark', preset: 'violet', primary: '#5C4A6E' },
  nord: { mode: 'dark', preset: 'lake-blue', primary: '#3D6B8A' },
  onedark: { mode: 'dark', preset: 'minimal', primary: '#2F4A6E' },
  everforest: { mode: 'dark', preset: 'ink-green', primary: '#2D4739' },
  gruvbox: { mode: 'dark', preset: 'luxury', primary: '#6B5A3E' },
  kanagawa: { mode: 'dark', preset: 'lake-blue', primary: '#3D5A6E' },
  dracula: { mode: 'dark', preset: 'violet', primary: '#5C4A6E' },
  ayu: { mode: 'dark', preset: 'luxury', primary: '#6B5A3E' },
  monokai: { mode: 'dark', preset: 'ink-green', primary: '#4A6B3A' },
  matrix: { mode: 'dark', preset: 'ink-green', primary: '#1A4A2A' },
};

let cachedPrefs: ThemePrefs | null = null;
let systemMediaCleanup: (() => void) | null = null;

function isPresetId(v: unknown): v is ThemePresetId {
  return (
    v === 'random' ||
    v === 'ink-green' ||
    v === 'lake-blue' ||
    v === 'minimal' ||
    v === 'violet' ||
    v === 'luxury' ||
    v === 'custom'
  );
}

function isMode(v: unknown): v is AppearanceMode {
  return v === 'light' || v === 'dark' || v === 'system';
}

export function sanitizePrefs(raw: Partial<ThemePrefs> | null | undefined): ThemePrefs {
  const base = { ...DEFAULT_THEME_PREFS, ...(raw || {}) };
  return {
    mode: isMode(base.mode) ? base.mode : DEFAULT_THEME_PREFS.mode,
    preset: isPresetId(base.preset) ? base.preset : DEFAULT_THEME_PREFS.preset,
    primary: normalizeHex(base.primary || DEFAULT_THEME_PREFS.primary),
    purity: clamp(Number(base.purity) || DEFAULT_THEME_PREFS.purity, PURITY_MIN, PURITY_MAX),
    contrast: clamp(
      Number(base.contrast) || DEFAULT_THEME_PREFS.contrast,
      CONTRAST_MIN,
      CONTRAST_MAX,
    ),
    fontSize: clamp(
      Number(base.fontSize) || DEFAULT_THEME_PREFS.fontSize,
      FONT_SIZE_MIN,
      FONT_SIZE_MAX,
    ),
    serif: Boolean(base.serif),
  };
}

export function migrateLegacyChatTheme(): ThemePrefs | null {
  try {
    if (localStorage.getItem(THEME_STORAGE_KEY)) return null;
    const legacy = localStorage.getItem(LEGACY_THEME_KEY);
    if (!legacy) return null;
    const mapped = LEGACY_MAP[legacy] || {
      mode: 'system' as const,
      preset: 'ink-green' as const,
      primary: '#2D4739',
    };
    const prefs = sanitizePrefs({ ...DEFAULT_THEME_PREFS, ...mapped });
    localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(prefs));
    return prefs;
  } catch {
    return null;
  }
}

export function loadThemePrefs(): ThemePrefs {
  if (cachedPrefs) return cachedPrefs;
  try {
    const migrated = migrateLegacyChatTheme();
    if (migrated) {
      cachedPrefs = migrated;
      return migrated;
    }
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    if (raw) {
      cachedPrefs = sanitizePrefs(JSON.parse(raw));
      return cachedPrefs;
    }
  } catch {
    /* ignore */
  }
  cachedPrefs = { ...DEFAULT_THEME_PREFS };
  return cachedPrefs;
}

export function saveThemePrefs(prefs: ThemePrefs): void {
  const next = sanitizePrefs(prefs);
  cachedPrefs = next;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(THEME_PREFS_EVENT, { detail: next }));
  }
}

function clearLegacyThemeClasses(root: HTMLElement): void {
  for (const id of LEGACY_THEME_CLASSES) {
    root.classList.remove(`theme-${id}`);
  }
}

export function applyThemePrefs(prefs?: ThemePrefs): ThemePrefs {
  const next = sanitizePrefs(prefs ?? loadThemePrefs());
  cachedPrefs = next;

  if (typeof document === 'undefined') return next;

  const root = document.documentElement;
  const appearance = resolveAppearance(next.mode);
  const surfaceHue =
    next.preset === 'custom' || next.preset === 'random'
      ? getPresetSurfaceHue('ink-green')
      : getPresetSurfaceHue(next.preset);

  const palette = buildPalette({
    appearance,
    primary: next.primary,
    purity: next.purity,
    contrast: next.contrast,
    surfaceHue,
    preset: next.preset,
  });

  clearLegacyThemeClasses(root);
  root.classList.toggle('dark', appearance === 'dark');
  root.classList.toggle('font-serif', next.serif);
  root.dataset.appearance = appearance;
  root.dataset.themePreset = next.preset;

  root.style.setProperty('--color-primary', palette.primary);
  root.style.setProperty('--color-bg', palette.bg);
  root.style.setProperty('--color-rail', palette.rail);
  root.style.setProperty('--color-stage', palette.stage);
  root.style.setProperty('--color-panel', palette.panel);
  root.style.setProperty('--color-border', palette.border);
  root.style.setProperty('--color-bubble-self', palette.bubbleSelf);
  root.style.setProperty('--color-bubble-other', palette.bubbleOther);
  root.style.setProperty('--color-text-main', palette.textMain);
  root.style.setProperty('--color-text-muted', palette.textMuted);
  root.style.setProperty('--chat-font-size', String(next.fontSize));
  root.style.setProperty(
    '--font-chat',
    next.serif
      ? '"Source Serif 4", "Noto Serif SC", Georgia, "Times New Roman", serif'
      : '"DM Sans", "Noto Sans SC", system-ui, sans-serif',
  );

  // Keep document chrome in sync (Tailwind bg-bgLight + body fallback)
  root.style.backgroundColor = palette.bg;
  root.style.color = palette.textMain;
  if (document.body) {
    document.body.style.backgroundColor = palette.bg;
    document.body.style.color = palette.textMain;
  }

  // theme-color meta for mobile chrome
  try {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', palette.bg);
  } catch {
    /* ignore */
  }

  ensureSystemListener(next.mode);
  return next;
}

function ensureSystemListener(mode: AppearanceMode): void {
  if (typeof window === 'undefined' || !window.matchMedia) return;
  if (systemMediaCleanup) {
    systemMediaCleanup();
    systemMediaCleanup = null;
  }
  if (mode !== 'system') return;
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  const onChange = () => {
    applyThemePrefs(loadThemePrefs());
  };
  if (mq.addEventListener) mq.addEventListener('change', onChange);
  else mq.addListener(onChange);
  systemMediaCleanup = () => {
    if (mq.removeEventListener) mq.removeEventListener('change', onChange);
    else mq.removeListener(onChange);
  };
}

export function updateThemePrefs(patch: Partial<ThemePrefs>): ThemePrefs {
  const next = sanitizePrefs({ ...loadThemePrefs(), ...patch });
  saveThemePrefs(next);
  return applyThemePrefs(next);
}

export function openThemeSettings(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(OPEN_THEME_SETTINGS_EVENT));
  }
}

export function subscribeThemePrefs(listener: (prefs: ThemePrefs) => void): () => void {
  const handler = (e: Event) => {
    const detail = (e as CustomEvent).detail as ThemePrefs | undefined;
    listener(sanitizePrefs(detail ?? loadThemePrefs()));
  };
  window.addEventListener(THEME_PREFS_EVENT, handler);
  return () => window.removeEventListener(THEME_PREFS_EVENT, handler);
}

/** Bootstrap: migrate + apply once at app start (call as early as possible). */
export function initTheme(): ThemePrefs {
  const prefs = loadThemePrefs();
  return applyThemePrefs(prefs);
}

export { PRESET_METAS };
