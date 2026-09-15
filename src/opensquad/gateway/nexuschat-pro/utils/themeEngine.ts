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
  | 'rose'
  | 'pure-white'
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
  /** Foreground colour that contrasts with `primary` for filled buttons,
   *  badges, and other "primary on primary" surfaces. In light mode this is
   *  near-white (text on a dark accent). In dark mode, presets whose primary
   *  is a light neutral (rose's `#E6E6E6`) flip this to a near-black so the
   *  label stays legible. */
  onPrimary: string;
  bg: string;
  /** Side rails (session list, workspace files) — slightly deeper */
  rail: string;
  /** Nested wells inside rails — deeper still */
  nest: string;
  /** Center stage (Agent Web chat) — lighter wash */
  stage: string;
  panel: string;
  border: string;
  /** Containment stroke for a *floating* surface (the message composer).
   *  `border` is intentionally faint — dividers and card edges read better
   *  soft — which leaves a floating surface with no boundary at all once the
   *  drop shadow stops working (black-on-near-black). This one is held to
   *  `BOUNDARY_CONTRAST_FLOOR` against the page surface instead. */
  boundary: string;
  bubbleSelf: string;
  bubbleOther: string;
  textMain: string;
  textMuted: string;
  /** Achieved (measured) contrast ratio of `textMain` against the page
   *  surface. Exposed so the settings panel can show the real number instead
   *  of echoing the requested target back at the user. */
  contrastMain: number;
  /** Achieved contrast ratio of `textMuted` against the page surface. */
  contrastMuted: number;
}

export interface SurfacePair {
  bg: string;
  panel: string;
  border: string;
}

export interface PresetMeta {
  id: Exclude<ThemePresetId, 'custom'>;
  primary: string;
  /** Optional primary override for dark mode. Use this for presets whose
   *  light-mode primary is illegible on a dark surface (e.g. the rose
   *  preset is intentionally a near-black charcoal on white surfaces) so
   *  primary-coloured text in dark mode (button labels, focus rings, etc.)
   *  doesn't disappear. Light mode is unaffected. */
  darkPrimary?: string;
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
    // Renamed from "paper" — what was sold as "pure paper white" actually
    // reads as a faint rose / warm tint once the purity slider tints the
    // surfaces, so we now call it what it looks like.
    id: 'rose',
    primary: '#1F1F1F',
    // In dark mode, invert to a light neutral so primary-coloured text
    // (e.g. "Connect Provider" button label, "AI" badges) stays legible
    // on the near-black surface. The hue/feel is preserved.
    darkPrimary: '#E6E6E6',
    surfaceHue: 0,
    light: { bg: '#FFFFFF', panel: '#FFFFFF', border: '#ECEEF1' },
    dark: { bg: '#0A0B0D', panel: '#121316', border: '#1E2025' },
    i18nNameKey: 'themeSettings.presets.rose.name',
    i18nDescKey: 'themeSettings.presets.rose.desc',
  },
  {
    // True white in light mode; near-black in dark mode. Both keep the
    // surface on the same monochromatic family so the only visual
    // change between modes is the inversion of bg / text.
    //
    // `darkPrimary` is required here for the same reason as `rose`: the
    // accent is not only a *fill*. It is also painted as text and
    // iconography straight onto the page — `text-primary`, the app-wide
    // `.dark .prose code` rule (agent inline code such as `chat_api.py:1204`),
    // `.prose a`, focus rings, slider fills. The previous "no override
    // needed" note assumed the accent only ever appeared as a button
    // background on a matching surface; that is false for text. Without
    // the override the near-black accent sat on the near-black dark
    // surface at 1.15:1, making agent-emitted file references invisible
    // in this preset's dark mode (13.2:1 with it).
    id: 'pure-white',
    primary: '#1F1F1F',
    darkPrimary: '#E6E6E6',
    surfaceHue: 0,
    light: { bg: '#FFFFFF', panel: '#FFFFFF', border: '#F0F0F0' },
    dark: { bg: '#0A0A0B', panel: '#131316', border: '#26262B' },
    i18nNameKey: 'themeSettings.presets.pureWhite.name',
    i18nDescKey: 'themeSettings.presets.pureWhite.desc',
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

/** Secondary text must still be readable — 4.5:1 is the WCAG AA threshold
 *  for normal body copy, and it applies at *every* contrast setting (see
 *  `mutedTarget`). Previously `textMuted` was a fixed 45/50% mix toward the
 *  rail, which measured 2.97:1 (light) / 4.20:1 (dark) no matter what the
 *  contrast slider said. */
const MUTED_CONTRAST_FLOOR = 4.5;

/**
 * Lightness bands for body / secondary copy, per appearance.
 *
 * `contrast` interpolates between `soft` (CONTRAST_MIN) and `firm`
 * (CONTRAST_MAX). A single fixed start lightness cannot serve the whole range:
 * the previous implementation started light-mode text at L≈20 and dark-mode
 * text at L≈90 with a 'lighten' direction, so the loop satisfied its target on
 * the very first iteration for every setting — the slider was inert (measured:
 * dark-mode `textMain` stayed pinned at `#E7E6E4` for contrast 3, 7.5 and 12).
 *
 * The midpoints reproduce the previous default appearance (light L≈18, dark
 * L≈90 at contrast 7.5), so existing users see no shift at the default.
 */
const TEXT_LIGHTNESS_BAND: Record<
  'light' | 'dark',
  { main: { soft: number; firm: number }; muted: { soft: number; firm: number } }
> = {
  light: { main: { soft: 26, firm: 10 }, muted: { soft: 44, firm: 30 } },
  dark: { main: { soft: 84, firm: 96 }, muted: { soft: 68, firm: 84 } },
};

/**
 * Minimum contrast between a floating surface's stroke and the surface it
 * sits on.
 *
 * Elevation in this UI is carried by a drop shadow. That works in light mode
 * (a black shadow under a card on a light page) and is worthless in dark mode,
 * where the shadow is black on near-black — so over there the stroke is the
 * *only* thing that can say "this is a card". Measured before this floor
 * existed, the dark `border` against the page surface is **1.03–1.31:1** for
 * every preset (rose is the worst at 1.03), i.e. the composer was
 * indistinguishable from the page until focus drew its ring — reported from a
 * pair of screenshots showing exactly that.
 *
 * `border` cannot simply be raised to fix it: it is deliberately mixed 70%
 * toward the panel so card edges stop reading as white lines, and that
 * softness is what dividers want. Hence a separate stroke with a floor.
 *
 * 2.4 clears the perceptual threshold for a 1px hairline while staying below
 * the focus ring (measured 3.3–3.7:1), so "focused" still reads as stronger
 * than "at rest" instead of the two collapsing into one another.
 */
export const BOUNDARY_CONTRAST_FLOOR = 2.4;

/**
 * Walk lightness away from the background until the candidate clears `target`
 * against *every* reference surface, then return that lightness.
 *
 * Checking all refs (rail *and* panel) matters: in dark mode the panel is
 * lighter than the rail, so a colour that only satisfies the rail can still
 * fall short where the copy actually sits.
 */
function fitLightness(
  hue: number,
  sat: number,
  startL: number,
  refs: string[],
  target: number,
  direction: 'darken' | 'lighten',
): number {
  const step = direction === 'darken' ? -1 : 1;
  let l = startL;
  for (let i = 0; i < 101; i++) {
    const candidate = hslToHex(hue, sat, l);
    if (refs.every((ref) => contrastRatioHex(candidate, ref) >= target)) return l;
    const next = l + step;
    if (next < 0 || next > 100) break;
    l = next;
  }
  return l;
}

/**
 * Containment stroke for a floating surface, derived from the preset's own
 * `border` so the hue/saturation stay in the design's family — but walked
 * away from the surface until it clears `BOUNDARY_CONTRAST_FLOOR`.
 *
 * `fitLightness` returns the start lightness unchanged when it already
 * satisfies the target, so presets that ship a strong enough dark border are
 * left alone; only the ones that collapse (all of them, currently) move, and
 * they move by the fewest steps that clear the floor rather than snapping to
 * white.
 *
 * Light appearance returns `border` untouched on purpose: there the shadow
 * does carry the elevation and the panel is already lighter than the page, so
 * nothing needs repainting.
 */
function deriveBoundary(border: string, surface: string, appearance: 'light' | 'dark'): string {
  if (appearance === 'light') return border;
  const { r, g, b } = hexToRgb(border);
  const { h, s, l } = rgbToHsl(r, g, b);
  const fitted = fitLightness(h, s, l, [surface], BOUNDARY_CONTRAST_FLOOR, 'lighten');
  return hslToHex(h, s, fitted);
}

/**
 * Derive body + secondary text for an appearance from the contrast setting.
 * `contrast` moves the text through its comfort band; the WCAG-ish target is
 * enforced as a *floor*, so the measured ratio always meets or exceeds what
 * the settings panel reports.
 */
function deriveTextColors(opts: {
  appearance: 'light' | 'dark';
  surfaceHue: number;
  cT: number;
  cTarget: number;
  refs: string[];
  /** Force grayscale (pure-white preset must stay monochrome). */
  neutral?: boolean;
}): { textMain: string; textMuted: string } {
  const dark = opts.appearance === 'dark';
  const direction: 'darken' | 'lighten' = dark ? 'lighten' : 'darken';
  const band = TEXT_LIGHTNESS_BAND[opts.appearance];
  const at = (b: { soft: number; firm: number }) => b.soft + (b.firm - b.soft) * opts.cT;
  const mainSat = opts.neutral ? 0 : dark ? 4 + opts.cT * 4 : 10 + opts.cT * 8;
  const mutedSat = opts.neutral ? 0 : dark ? 2 + opts.cT * 3 : 4 + opts.cT * 5;
  // Secondary text keeps its own accessibility floor and it is NOT capped by
  // the slider: at the lowest setting the band alone lands around 3.8:1, so
  // capping at `cTarget` would silently drop secondary copy below WCAG AA.
  // Reading order beats fidelity to a low slider value here.
  const mutedTarget = Math.max(MUTED_CONTRAST_FLOOR, opts.cTarget * 0.62);
  return {
    textMain: hslToHex(
      opts.surfaceHue,
      mainSat,
      fitLightness(opts.surfaceHue, mainSat, at(band.main), opts.refs, opts.cTarget, direction),
    ),
    textMuted: hslToHex(
      opts.surfaceHue,
      mutedSat,
      fitLightness(opts.surfaceHue, mutedSat, at(band.muted), opts.refs, mutedTarget, direction),
    ),
  };
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

  // pure-white must stay truly neutral: mixing white with a near-black
  // primary turns the result into a gray whose HSL hue defaults to 0
  // (rose), and the rail/nest/stage derivation then forces a minimum
  // saturation that paints the whole palette pink. Skip the primary
  // tint for this preset and derive every surface from `base` directly.
  //
  // pure-white inverts surface AND text together between light and dark
  // modes: light mode is white bg + dark text, dark mode is near-black
  // bg + light text. Text colours must therefore follow the appearance
  // — fixing them to dark text (the previous behaviour) made body copy
  // disappear against the dark-mode near-black surface.
  if (opts.preset === 'pure-white') {
    const onPrimary = contrastRatioHex('#FFFFFF', primary) >= 4.5
      ? '#FFFFFF'
      : (contrastRatioHex('#0B0B0C', primary) >= 4.5 ? '#0B0B0C' : primary);
    // pure-white inverts surface AND text together between light and dark
    // modes: light mode is white bg + dark text, dark mode is near-black
    // bg + light text — so the text must follow the appearance.
    // `neutral: true` keeps the preset monochrome; the contrast slider is
    // still honoured (these two colours used to be hard-coded constants that
    // ignored the setting entirely).
    const { textMain, textMuted } = deriveTextColors({
      appearance: opts.appearance,
      surfaceHue: 0,
      cT,
      cTarget,
      refs: [base.bg, base.panel],
      neutral: true,
    });
    return {
      primary,
      onPrimary,
      bg: base.bg,
      rail: base.bg,
      nest: base.bg,
      stage: base.bg,
      panel: base.panel,
      border: base.border,
      // pure-white collapses rail/nest/stage onto `base.bg`, so that is the
      // surface a floating card sits on.
      boundary: deriveBoundary(base.border, base.bg, opts.appearance),
      bubbleSelf: base.panel,
      bubbleOther: base.panel,
      textMain,
      textMuted,
      contrastMain: contrastRatioHex(textMain, base.bg),
      contrastMuted: contrastRatioHex(textMuted, base.bg),
    };
  }

  // Tint surfaces toward primary — purity raises liveliness
  const tintAmount = opts.appearance === 'light' ? 0.035 + tint * 0.14 : 0.05 + tint * 0.16;
  let bg = mixHex(base.bg, primary, tintAmount);
  let panel = mixHex(base.panel, primary, tintAmount * 0.4);
  let border = mixHex(base.border, primary, tintAmount * 0.55);

  // Pick a foreground that contrasts with the primary fill. We don't reuse
  // `textMain` because the page text colour is derived against the *page*
  // surface, not the primary fill. Use the standard WCAG-ish threshold of
  // ~4.5:1 for body text so labels stay legible on filled buttons.
  const onPrimary = contrastRatioHex('#FFFFFF', primary) >= 4.5
    ? '#FFFFFF'
    : (contrastRatioHex('#0B0B0C', primary) >= 4.5 ? '#0B0B0C' : primary);

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
    // Stage (page wash): lighter than rail, still carries theme hue
    const stage = hslToHex(
      railHsl.h,
      clamp(railHsl.s * 0.55 + 2 + tint * 5, 6, 22),
      clamp(railHsl.l + 3.2 + (1 - tint) * 0.8, 93, 97),
    );
    // Panel (center column): lightest surface — whiter than rails, soft tint (not pure white)
    panel = hslToHex(
      railHsl.h,
      clamp(railHsl.s * 0.4 + 1.5 + tint * 4, 5, 18),
      clamp(railHsl.l + 5.2 + (1 - tint) * 0.6, 95.5, 98.2),
    );
    const { textMain, textMuted } = deriveTextColors({
      appearance: 'light',
      surfaceHue: sh,
      cT,
      cTarget,
      refs: [rail, panel],
    });
    return {
      primary,
      onPrimary,
      bg: stage,
      rail,
      nest,
      stage,
      panel,
      border,
      boundary: deriveBoundary(border, stage, 'light'),
      bubbleSelf: mixHex(primary, stage, 0.82),
      bubbleOther: panel,
      textMain,
      textMuted,
      contrastMain: contrastRatioHex(textMain, rail),
      contrastMuted: contrastRatioHex(textMuted, rail),
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
  // Pull dark-mode borders much closer to the panel so they don't read as
  // "white lines" against the dark surface (user feedback on the rose
  // preset in dark mode, but applies broadly). 0.7 = 70% panel + 30% original.
  border = mixHex(border, panel, 0.7);
  const { textMain, textMuted } = deriveTextColors({
    appearance: 'dark',
    surfaceHue: sh,
    cT,
    cTarget,
    refs: [rail, panel],
  });
  return {
    primary,
    onPrimary,
    bg: stage,
    rail,
    nest,
    stage,
    panel,
    border,
    boundary: deriveBoundary(border, stage, 'dark'),
    bubbleSelf: mixHex(primary, panel, 0.55),
    bubbleOther: mixHex(panel, primary, 0.06),
    textMain,
    textMuted,
    contrastMain: contrastRatioHex(textMain, rail),
    contrastMuted: contrastRatioHex(textMuted, rail),
  };
}

export function getPresetPrimary(id: ThemePresetId, appearance?: 'light' | 'dark'): string {
  if (id === 'custom' || id === 'random') return DEFAULT_THEME_PREFS.primary;
  const meta = PRESET_METAS.find((p) => p.id === id);
  if (!meta) return DEFAULT_THEME_PREFS.primary;
  if (appearance === 'dark' && meta.darkPrimary) return meta.darkPrimary;
  return meta.primary;
}

export function getPresetSurfaceHue(id: ThemePresetId): number {
  const meta = PRESET_METAS.find((p) => p.id === id);
  return meta?.surfaceHue ?? 38;
}

export function formatContrastLabel(contrast: number): string {
  return `${clamp(contrast, CONTRAST_MIN, CONTRAST_MAX).toFixed(1)}:1`;
}

/**
 * Format a *measured* contrast ratio for display.
 *
 * `formatContrastLabel` clamps into the slider's own 3–12 range, which is
 * correct for the requested target but would silently flatten a measured
 * ratio above 12 (dark-mode body copy routinely lands around 13:1).
 */
export function formatRatioLabel(ratio: number): string {
  return `${(Number.isFinite(ratio) ? ratio : 0).toFixed(1)}:1`;
}
