/**
 * Claude-inspired theme engine.
 * Aesthetic: low-chroma accents, warm neutrals (light) / soft charcoal (dark),
 * semantic CSS tokens — avoid purple-pink "AI slop" defaults.
 */

export type AppearanceMode = 'light' | 'dark' | 'system';
export type ThemePresetId =
  | 'random'
  | 'ink-green'
  | 'lake-blue'
  | 'minimal'
  | 'violet'
  | 'luxury'
  | 'custom';

export interface ThemePrefs {
  mode: AppearanceMode;
  preset: ThemePresetId;
  primary: string;
  purity: number; // 0–100
  contrast: number; // ~3–12 (approx WCAG ratio target)
  fontSize: number; // relative multiplier, e.g. 0.875–1.25
  serif: boolean;
}

export interface ThemePalette {
  primary: string;
  bg: string;
  /** Side rails (session list, workspace files) — slightly deeper */
  rail: string;
  /** Nested wells inside rails — deeper still */
  nest: string;
  /** Center stage (Agent Web chat) — lighter wash */
  stage: string;
  panel: string;
  border: string;
  bubbleSelf: string;
  bubbleOther: string;
  textMain: string;
  textMuted: string;
}

export interface SurfacePair {
  bg: string;
  panel: string;
  border: string;
}

export interface PresetMeta {
  id: Exclude<ThemePresetId, 'custom'>;
  primary: string;
  /** Soft warm / cool bias for surface generation */
  surfaceHue: number;
  /** Distinct light/dark page surfaces — not just accent color */
  light: SurfacePair;
  dark: SurfacePair;
  i18nNameKey: string;
  i18nDescKey: string;
}

export const PRESET_METAS: PresetMeta[] = [
  {
    id: 'random',
    primary: '#4A7A5C',
    surfaceHue: 40,
    light: { bg: '#F5F3EE', panel: '#FFFEFB', border: '#E5E0D6' },
    dark: { bg: '#171A18', panel: '#222623', border: '#323833' },
    i18nNameKey: 'themeSettings.presets.random.name',
    i18nDescKey: 'themeSettings.presets.random.desc',
  },
  {
    id: 'ink-green',
    primary: '#3A6B52',
    surfaceHue: 38,
    // Warm parchment — lighter & a touch livelier
    light: { bg: '#F6F3EC', panel: '#FFFEFB', border: '#E6DFD2' },
    dark: { bg: '#1A1F1C', panel: '#242A26', border: '#343B36' },
    i18nNameKey: 'themeSettings.presets.inkGreen.name',
    i18nDescKey: 'themeSettings.presets.inkGreen.desc',
  },
  {
    id: 'lake-blue',
    primary: '#3D7A9A',
    surfaceHue: 210,
    light: { bg: '#EEF5F9', panel: '#F8FCFF', border: '#C9D9E4' },
    dark: { bg: '#12181F', panel: '#1A222B', border: '#2A3542' },
    i18nNameKey: 'themeSettings.presets.lakeBlue.name',
    i18nDescKey: 'themeSettings.presets.lakeBlue.desc',
  },
  {
    id: 'minimal',
    primary: '#3A5A8A',
    surfaceHue: 220,
    light: { bg: '#F1F4F8', panel: '#FAFBFD', border: '#D4DBE6' },
    dark: { bg: '#0F141B', panel: '#171D27', border: '#273041' },
    i18nNameKey: 'themeSettings.presets.minimal.name',
    i18nDescKey: 'themeSettings.presets.minimal.desc',
  },
  {
    id: 'violet',
    primary: '#6B5580',
    surfaceHue: 280,
    light: { bg: '#F5F0F7', panel: '#FCFAFD', border: '#E2D8EA' },
    dark: { bg: '#17141C', panel: '#211C28', border: '#342C3E' },
    i18nNameKey: 'themeSettings.presets.violet.name',
    i18nDescKey: 'themeSettings.presets.violet.desc',
  },
  {
    id: 'luxury',
    primary: '#7A6545',
    surfaceHue: 42,
    light: { bg: '#F4EDE2', panel: '#FFFEFA', border: '#E2D6C4' },
    dark: { bg: '#1A1610', panel: '#2A241C', border: '#3D3428' },
    i18nNameKey: 'themeSettings.presets.luxury.name',
    i18nDescKey: 'themeSettings.presets.luxury.desc',
  },
];

export const DEFAULT_THEME_PREFS: ThemePrefs = {
  mode: 'system',
  preset: 'ink-green',
  primary: '#3A6B52',
  purity: 36,
  contrast: 7.5,
  fontSize: 1,
  serif: false,
};

export const FONT_SIZE_MIN = 0.875;
export const FONT_SIZE_MAX = 1.25;
export const PURITY_MIN = 0;
export const PURITY_MAX = 100;
export const CONTRAST_MIN = 3;
export const CONTRAST_MAX = 12;

export function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

export function normalizeHex(hex: string): string {
  let h = (hex || '').trim().replace(/^#/, '');
  if (/^[0-9a-fA-F]{3}$/.test(h)) {
    h = h.split('').map((c) => c + c).join('');
  }
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return '#2D4739';
  return `#${h.toUpperCase()}`;
}

export function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const h = normalizeHex(hex).slice(1);
  return {
    r: parseInt(h.slice(0, 2), 16),
    g: parseInt(h.slice(2, 4), 16),
    b: parseInt(h.slice(4, 6), 16),
  };
}

export function rgbToHex(r: number, g: number, b: number): string {
  const to = (n: number) =>
    clamp(Math.round(n), 0, 255).toString(16).padStart(2, '0').toUpperCase();
  return `#${to(r)}${to(g)}${to(b)}`;
}

export function rgbToHsl(r: number, g: number, b: number): { h: number; s: number; l: number } {
  r /= 255;
  g /= 255;
  b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l: l * 100 };
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = 0;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
  else if (max === g) h = ((b - r) / d + 2) / 6;
  else h = ((r - g) / d + 4) / 6;
  return { h: h * 360, s: s * 100, l: l * 100 };
}

export function hslToRgb(h: number, s: number, l: number): { r: number; g: number; b: number } {
  h = ((h % 360) + 360) % 360;
  s = clamp(s, 0, 100) / 100;
  l = clamp(l, 0, 100) / 100;
  if (s === 0) {
    const v = l * 255;
    return { r: v, g: v, b: v };
  }
  const hue2rgb = (p: number, q: number, t: number) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const hk = h / 360;
  return {
    r: hue2rgb(p, q, hk + 1 / 3) * 255,
    g: hue2rgb(p, q, hk) * 255,
    b: hue2rgb(p, q, hk - 1 / 3) * 255,
  };
}

export function hslToHex(h: number, s: number, l: number): string {
  const { r, g, b } = hslToRgb(h, s, l);
  return rgbToHex(r, g, b);
}

/** Relative luminance (sRGB), 0–1 */
export function relativeLuminance(hex: string): number {
  const { r, g, b } = hexToRgb(hex);
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

export function contrastRatioHex(fg: string, bg: string): number {
  const L1 = relativeLuminance(fg);
  const L2 = relativeLuminance(bg);
  const light = Math.max(L1, L2);
  const dark = Math.min(L1, L2);
  return (light + 0.05) / (dark + 0.05);
}

export function mixHex(a: string, b: string, t: number): string {
  const A = hexToRgb(a);
  const B = hexToRgb(b);
  const u = clamp(t, 0, 1);
  return rgbToHex(
    A.r + (B.r - A.r) * u,
    A.g + (B.g - A.g) * u,
    A.b + (B.b - A.b) * u,
  );
}

export function applyPurity(hex: string, purity: number): string {
  const { r, g, b } = hexToRgb(hex);
  const { h, s: _s, l } = rgbToHsl(r, g, b);
  // Map 0–100 purity to ~10–52% saturation (livelier but still soft)
  const targetS = 10 + (clamp(purity, 0, 100) / 100) * 42;
  return hslToHex(h, targetS, l);
}

export function randomPrimary(): string {
  // Prefer earthy / calm hues; avoid neon pink-purple
  const hues = [28, 38, 95, 145, 165, 200, 215, 250, 30];
  const h = hues[Math.floor(Math.random() * hues.length)] + (Math.random() * 20 - 10);
  const s = 22 + Math.random() * 24;
  const l = 32 + Math.random() * 16;
  return hslToHex(h, s, l);
}

export function resolveAppearance(mode: AppearanceMode): 'light' | 'dark' {
  if (mode === 'light') return 'light';
  if (mode === 'dark') return 'dark';
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  return 'light';
}

function adjustTextForContrast(
  startHex: string,
  bg: string,
  target: number,
  direction: 'darken' | 'lighten',
): string {
  const rgb = hexToRgb(startHex);
  let { h, s, l } = rgbToHsl(rgb.r, rgb.g, rgb.b);
  for (let i = 0; i < 16; i++) {
    const candidate = hslToHex(h, s, l);
    if (contrastRatioHex(candidate, bg) >= target) return candidate;
    l = direction === 'darken' ? Math.max(4, l - 3) : Math.min(98, l + 2);
  }
  return hslToHex(h, s, l);
}

/** Derive light/dark page surfaces from an arbitrary primary (custom / random). */
export function surfacesFromPrimary(primary: string): { light: SurfacePair; dark: SurfacePair } {
  const rgb = hexToRgb(normalizeHex(primary));
  const { h } = rgbToHsl(rgb.r, rgb.g, rgb.b);
  return {
    light: {
      bg: hslToHex(h, 18, 94),
      panel: hslToHex(h, 12, 98.5),
      border: hslToHex(h, 14, 86),
    },
    dark: {
      bg: hslToHex(h, 12, 12),
      panel: hslToHex(h, 10, 17),
      border: hslToHex(h, 10, 26),
    },
  };
}

export function getPresetSurfaces(id: ThemePresetId, primary?: string): {
  light: SurfacePair;
  dark: SurfacePair;
} {
  if (id === 'custom' || id === 'random') {
    return surfacesFromPrimary(primary || DEFAULT_THEME_PREFS.primary);
  }
  const meta = PRESET_METAS.find((p) => p.id === id);
  if (!meta) return surfacesFromPrimary(primary || DEFAULT_THEME_PREFS.primary);
  return { light: meta.light, dark: meta.dark };
}

/**
 * Build full semantic palette from primary + purity + contrast + appearance.
 * Presets carry distinct page surfaces; purity tints them toward primary;
 * contrast pulls text/bg apart toward the target WCAG-ish ratio.
 */
export function buildPalette(opts: {
  appearance: 'light' | 'dark';
  primary: string;
  purity: number;
  contrast: number;
  surfaceHue?: number;
  preset?: ThemePresetId;
}): ThemePalette {
  const primary = applyPurity(normalizeHex(opts.primary), opts.purity);
  const rgb = hexToRgb(primary);
  const { h: ph } = rgbToHsl(rgb.r, rgb.g, rgb.b);
  const sh = opts.surfaceHue ?? (ph >= 20 && ph <= 160 ? 38 : ph);
  const cTarget = clamp(opts.contrast, CONTRAST_MIN, CONTRAST_MAX);
  const cT = (cTarget - CONTRAST_MIN) / (CONTRAST_MAX - CONTRAST_MIN);
  // Purity 0 → neutral surfaces; 100 → clearly tinted toward primary
  const tint = clamp(opts.purity, 0, 100) / 100;

  const surfaces = getPresetSurfaces(opts.preset || 'custom', opts.primary);
  const base = opts.appearance === 'light' ? surfaces.light : surfaces.dark;

  // Tint surfaces toward primary — purity raises liveliness
  const tintAmount = opts.appearance === 'light' ? 0.035 + tint * 0.14 : 0.05 + tint * 0.16;
  let bg = mixHex(base.bg, primary, tintAmount);
  let panel = mixHex(base.panel, primary, tintAmount * 0.4);
  const border = mixHex(base.border, primary, tintAmount * 0.55);

  if (opts.appearance === 'light') {
    const c = hexToRgb(bg);
    const bgHsl = rgbToHsl(c.r, c.g, c.b);
    // Rail (side cards): slightly deeper than page, gentle chroma
    const rail = hslToHex(
      bgHsl.h,
      clamp(bgHsl.s + 2 + tint * 6, 6, 26),
      clamp(bgHsl.l - 2 - cT * 1.5, 88, 95),
    );
    // Nest (wells inside rails): one soft step deeper
    const railRgb = hexToRgb(rail);
    const railHsl = rgbToHsl(railRgb.r, railRgb.g, railRgb.b);
    const nest = hslToHex(
      railHsl.h,
      clamp(railHsl.s + 1 + tint * 2, 6, 28),
      clamp(railHsl.l - 2.5, 84, 93),
    );
    // Stage (page wash behind cards): bright, quiet
    const p = hexToRgb(panel);
    const pHsl = rgbToHsl(p.r, p.g, p.b);
    const stage = hslToHex(pHsl.h, clamp(pHsl.s + tint * 2, 4, 18), clamp(pHsl.l, 96, 99));
    panel = hslToHex(pHsl.h, clamp(pHsl.s, 3, 16), clamp(Math.max(pHsl.l, 98.5), 97.5, 100));
    const textMain = adjustTextForContrast(
      hslToHex(sh, 10 + cT * 8, 20 - cT * 4),
      rail,
      cTarget,
      'darken',
    );
    const textMuted = mixHex(textMain, rail, 0.45);
    return {
      primary,
      bg: stage,
      rail,
      nest,
      stage,
      panel,
      border,
      bubbleSelf: mixHex(primary, stage, 0.82),
      bubbleOther: panel,
      textMain,
      textMuted,
    };
  }

  {
    const c = hexToRgb(bg);
    const bgHsl = rgbToHsl(c.r, c.g, c.b);
    bg = hslToHex(bgHsl.h, clamp(bgHsl.s - 2, 4, 16), clamp(bgHsl.l - cT * 3, 6, 18));
    panel = mixHex(panel, '#000000', cT * 0.12);
  }
  // Dark: nest deepest, rail mid, stage lifted, panel raised
  const rail = bg;
  const nest = mixHex(rail, '#000000', 0.18);
  const stage = mixHex(bg, panel, 0.4);
  const textMain = adjustTextForContrast(hslToHex(sh, 6, 90), rail, cTarget, 'lighten');
  const textMuted = mixHex(textMain, rail, 0.5);
  return {
    primary,
    bg: stage,
    rail,
    nest,
    stage,
    panel,
    border,
    bubbleSelf: mixHex(primary, panel, 0.55),
    bubbleOther: mixHex(panel, primary, 0.06),
    textMain,
    textMuted,
  };
}

export function getPresetPrimary(id: ThemePresetId): string {
  if (id === 'custom' || id === 'random') return DEFAULT_THEME_PREFS.primary;
  const meta = PRESET_METAS.find((p) => p.id === id);
  return meta?.primary ?? DEFAULT_THEME_PREFS.primary;
}

export function getPresetSurfaceHue(id: ThemePresetId): number {
  const meta = PRESET_METAS.find((p) => p.id === id);
  return meta?.surfaceHue ?? 38;
}

export function formatContrastLabel(contrast: number): string {
  return `${clamp(contrast, CONTRAST_MIN, CONTRAST_MAX).toFixed(1)}:1`;
}
