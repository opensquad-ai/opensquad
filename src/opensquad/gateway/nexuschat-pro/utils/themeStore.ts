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
  ThemePalette,
  ThemePrefs,
  ThemePresetId,
  buildPalette,
  clamp,
  getPresetPrimary,
  getPresetSurfaceHue,
  hexToRgb,
  normalizeHex,
  resolveAppearance,
} from './themeEngine';

export const THEME_STORAGE_KEY = 'opensquad_theme_v2';
export const LEGACY_THEME_KEY = 'chat_theme';
export const THEME_PREFS_EVENT = 'themePrefsChanged';
export const OPEN_THEME_SETTINGS_EVENT = 'openThemeSettings';

/**
 * Convert a hex color to a space-separated RGB triplet (e.g. `#3D3428` → `61 52 40`).
 * Tailwind's color-opacity modifiers compile `border-border/40` to
 * `rgb(var(--color-border) / 0.4)`, which is only valid when the variable
 * is an RGB triplet. Storing the raw hex there makes the declaration
 * invalid and the browser falls back to `currentColor` (the body text),
 * which in dark mode is light — producing the "white border" effect.
 * The same triplet also works inside `rgb(var(--x) / α)` or
 * `color-mix(in srgb, rgb(var(--x)) …)`, so consumers stay simple.
 */
function hexToRgbTriplet(hex: string): string {
  const { r, g, b } = hexToRgb(hex);
  return `${r} ${g} ${b}`;
}

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
let lastPalette: ThemePalette | null = null;
let systemMediaCleanup: (() => void) | null = null;

/**
 * Palette produced by the most recent `applyThemePrefs` call.
 *
 * The settings panel needs *measured* numbers (e.g. the contrast ratio the
 * current theme actually renders) rather than an echo of the requested
 * preference — recomputing the palette there would duplicate the
 * appearance/primary/surfaceHue resolution logic and drift from it.
 */
export function getActivePalette(): ThemePalette | null {
  return lastPalette;
}

/**
 * Derived from `PRESET_METAS` so adding/renaming a preset can never leave a
 * stale id accepted here.
 *
 * This list used to be hand-written and still contained `'paper'` long after
 * that preset was renamed to `'rose'`, even though `ThemePresetId` no longer
 * had it. A persisted `'paper'` therefore passed validation, kept its slot
 * forever (never self-healing), rendered surfaces derived from a fallback
 * primary, and left the settings grid with no preset highlighted.
 */
function isPresetId(v: unknown): v is ThemePresetId {
  return v === 'custom' || PRESET_METAS.some((m) => m.id === v);
}

function isMode(v: unknown): v is AppearanceMode {
  return v === 'light' || v === 'dark' || v === 'system';
}

/**
 * Coerce a persisted numeric pref, falling back only when the value is
 * genuinely absent or unparseable.
 *
 * Do NOT replace this with `Number(v) || fallback`: `0` is falsy, and
 * `PURITY_MIN` is exactly `0`, so the purity slider silently snapped back to
 * the default 36 the moment the user dragged it to the left end (measured:
 * `sanitizePrefs({purity: 0}).purity === 36`).
 */
function numOr(v: unknown, fallback: number): number {
  if (v === null || v === undefined || v === '') return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export function sanitizePrefs(raw: Partial<ThemePrefs> | null | undefined): ThemePrefs {
  const base = { ...DEFAULT_THEME_PREFS, ...(raw || {}) };
  return {
    mode: isMode(base.mode) ? base.mode : DEFAULT_THEME_PREFS.mode,
    preset: isPresetId(base.preset) ? base.preset : DEFAULT_THEME_PREFS.preset,
    primary: normalizeHex(base.primary || DEFAULT_THEME_PREFS.primary),
    purity: clamp(numOr(base.purity, DEFAULT_THEME_PREFS.purity), PURITY_MIN, PURITY_MAX),
    contrast: clamp(numOr(base.contrast, DEFAULT_THEME_PREFS.contrast), CONTRAST_MIN, CONTRAST_MAX),
    fontSize: clamp(numOr(base.fontSize, DEFAULT_THEME_PREFS.fontSize), FONT_SIZE_MIN, FONT_SIZE_MAX),
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
      const parsed = sanitizePrefs(JSON.parse(raw));
      // One-shot lift from prior overly-muted defaults (purity 18 / dark primary)
      let next = parsed;
      if (parsed.purity === 18) {
        next = { ...next, purity: DEFAULT_THEME_PREFS.purity };
      }
      if (
        parsed.preset === 'ink-green' &&
        normalizeHex(parsed.primary) === '#2D4739'
      ) {
        next = { ...next, primary: DEFAULT_THEME_PREFS.primary };
      }
      if (next !== parsed) {
        try {
          localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(next));
        } catch {
          /* ignore */
        }
      }
      cachedPrefs = next;
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
  void import('./hostUiPrefs').then((m) => m.schedulePushHostUiPrefs()).catch(() => undefined);
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
  // For built-in presets, resolve the *effective* primary against the
  // current appearance so presets whose light-mode primary is illegible
  // on dark surfaces (e.g. rose / pure-white's near-black charcoal) get an
  // explicit `darkPrimary` override. Custom / random keep the user's saved
  // colour.
  //
  // This resolution is load-bearing beyond button fills: the accent is also
  // painted as *text* on the page (`.dark .prose code`, `.prose a`,
  // `text-primary`), so a preset that skips `darkPrimary` while owning a
  // near-black accent renders agent output invisibly in dark mode.
  // themeEngine.test.ts → "accent colour stays distinguishable from the
  // page" guards the whole table.
  const effectivePrimary =
    next.preset === 'custom' || next.preset === 'random'
      ? next.primary
      : getPresetPrimary(next.preset, appearance);
  const surfaceHue =
    next.preset === 'custom' || next.preset === 'random'
      ? getPresetSurfaceHue('ink-green')
      : getPresetSurfaceHue(next.preset);

  const palette = buildPalette({
    appearance,
    primary: effectivePrimary,
    purity: next.purity,
    contrast: next.contrast,
    surfaceHue,
    preset: next.preset,
  });
  lastPalette = palette;

  clearLegacyThemeClasses(root);
  root.classList.toggle('dark', appearance === 'dark');
  root.classList.toggle('font-serif', next.serif);
  root.dataset.appearance = appearance;
  root.dataset.themePreset = next.preset;

  // Tailwind opacity modifiers (e.g. `border-border/40`) compile to
  // `rgb(var(--color-border) / 0.4)`, which only works when the variable
  // is a space-separated RGB triplet — not a hex string. Storing the
  // triplet here lets every theme-coloured border/opacity utility work
  // in both light and dark mode without falling back to `currentColor`.
  root.style.setProperty('--color-primary', hexToRgbTriplet(palette.primary));
  root.style.setProperty('--color-on-primary', hexToRgbTriplet(palette.onPrimary));
  root.style.setProperty('--color-bg', hexToRgbTriplet(palette.bg));
  root.style.setProperty('--boot-bg', palette.bg);
  root.style.setProperty('--color-rail', hexToRgbTriplet(palette.rail));
  root.style.setProperty('--color-nest', hexToRgbTriplet(palette.nest));
  root.style.setProperty('--color-stage', hexToRgbTriplet(palette.stage));
  root.style.setProperty('--color-panel', hexToRgbTriplet(palette.panel));
  root.style.setProperty('--color-border', hexToRgbTriplet(palette.border));
  root.style.setProperty('--color-boundary', hexToRgbTriplet(palette.boundary));
  root.style.setProperty('--color-bubble-self', hexToRgbTriplet(palette.bubbleSelf));
  root.style.setProperty('--color-bubble-other', hexToRgbTriplet(palette.bubbleOther));
  root.style.setProperty('--color-text-main', hexToRgbTriplet(palette.textMain));
  root.style.setProperty('--color-text-muted', hexToRgbTriplet(palette.textMuted));
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

/**
 * Patch produced by one of the appearance sliders (vibrance / contrast /
 * font size).
 *
 * Exists as a named function purely so the invariant "adjusting a slider must
 * not change the selected preset" can be asserted directly. These sliders used
 * to also send `preset: 'custom'`, which threw away the preset's hand-tuned
 * surface pair and swapped in accent-derived surfaces — so moving the contrast
 * slider visibly repainted the page (measured: ink-green `#EFF0EB` warm →
 * `#EBF0EE` cool green). Contrast and vibrance are orthogonal to surface
 * selection; only the colour picker is allowed to switch to `custom`.
 */
export function appearanceSliderPatch(
  field: 'purity' | 'contrast' | 'fontSize',
  value: number,
): Partial<ThemePrefs> {
  return { [field]: value };
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
