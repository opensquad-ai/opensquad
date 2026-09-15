/**
 * Regression locks for the theme engine.
 *
 * `themeEngine.ts` holds ~500 lines of colour maths that had **no tests at
 * all** before this file. Each `describe` below pins a behaviour that was
 * either broken or silently inert; the comment on each block records what the
 * failure looked like so a future refactor knows what it is protecting.
 */
import { describe, it, expect } from 'vitest';
import {
  BOUNDARY_CONTRAST_FLOOR,
  CONTRAST_MAX,
  CONTRAST_MIN,
  DEFAULT_THEME_PREFS,
  PRESET_METAS,
  buildPalette,
  contrastRatioHex,
  formatContrastLabel,
  formatRatioLabel,
  getPresetPrimary,
  getPresetSurfaceHue,
  hexToRgb,
  normalizeHex,
} from './themeEngine';

const APPEARANCES = ['light', 'dark'] as const;
const PRESET_IDS = [...PRESET_METAS.map((m) => m.id), 'custom' as const];
const CONTRASTS = [CONTRAST_MIN, 7.5, CONTRAST_MAX];

/** Integer HSL→RGB rounding means a fitted colour can land a hair under the
 *  requested ratio; the floor is enforced on the rounded hex, so allow a
 *  tolerance far smaller than any perceptible difference. */
const EPS = 0.02;

function palette(appearance: 'light' | 'dark', preset: string, contrast: number) {
  return buildPalette({
    appearance,
    primary: preset === 'custom' ? '#8A6A3D' : (PRESET_METAS.find((m) => m.id === preset)?.primary ?? '#3A6B52'),
    purity: DEFAULT_THEME_PREFS.purity,
    contrast,
    preset: preset as never,
  });
}

describe('contrast setting is a real, enforced floor', () => {
  // Before: `textMain` was derived from a fixed start lightness (L≈20 light /
  //   L≈90 dark) with a direction that already satisfied the target on the
  //   first loop iteration, so the slider did nothing — dark-mode body text was
  //   measured pinned at #E7E6E4 for contrast 3, 7.5 and 12 alike.
  it.each(APPEARANCES)('%s — body text meets the requested ratio for every preset', (appearance) => {
    for (const preset of PRESET_IDS) {
      for (const contrast of CONTRASTS) {
        const pal = palette(appearance, preset, contrast);
        expect(
          pal.contrastMain,
          `${appearance}/${preset} @${contrast}`,
        ).toBeGreaterThanOrEqual(contrast - EPS);
      }
    }
  });

  it.each(APPEARANCES)('%s — the slider actually moves the rendered text', (appearance) => {
    for (const preset of PRESET_IDS) {
      const lo = palette(appearance, preset, CONTRAST_MIN).contrastMain;
      const hi = palette(appearance, preset, CONTRAST_MAX).contrastMain;
      expect(hi, `${appearance}/${preset} range`).toBeGreaterThan(lo + 2);
    }
  });

  it.each(APPEARANCES)('%s — contrastMain reports the measured ratio, not the target', (appearance) => {
    const pal = palette(appearance, 'ink-green', 7.5);
    expect(pal.contrastMain).toBeCloseTo(contrastRatioHex(pal.textMain, pal.rail), 5);
  });
});

describe('secondary text stays readable', () => {
  // Before: textMuted was a fixed 45/50% mix toward the rail and measured
  // 2.97:1 (light) / 4.20:1 (dark) at every slider position.
  it.each(APPEARANCES)('%s — muted text clears WCAG AA 4.5:1 at EVERY setting', (appearance) => {
    // Deliberately independent of the slider's low end: at CONTRAST_MIN the
    // band alone lands near 3.8:1, so the floor is the binding constraint
    // there. Capping the muted target at `contrast` (an earlier draft) made
    // this fail for every preset, and lowering the floor does too.
    for (const preset of PRESET_IDS) {
      for (const contrast of CONTRASTS) {
        const pal = palette(appearance, preset, contrast);
        expect(pal.contrastMuted, `${appearance}/${preset} @${contrast}`).toBeGreaterThanOrEqual(
          4.5 - EPS,
        );
      }
    }
  });

  it.each(APPEARANCES)('%s — muted stays visibly lighter/lower weight than body text', (appearance) => {
    for (const preset of PRESET_IDS) {
      const pal = palette(appearance, preset, 7.5);
      const main = hexToRgb(pal.textMain);
      const muted = hexToRgb(pal.textMuted);
      const lum = (c: { r: number; g: number; b: number }) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
      const mainLum = lum(main);
      const mutedLum = lum(muted);
      const towardsBg = appearance === 'light' ? mutedLum > mainLum : mutedLum < mainLum;
      expect(towardsBg, `${appearance}/${preset}`).toBe(true);
    }
  });
});

describe('default appearance is not disturbed', () => {
  // The bands were chosen so the mid-point reproduces the pre-refactor look.
  // Losing this would silently restyle every existing user on upgrade.
  it('light default body text still lands near 10.4:1', () => {
    const pal = palette('light', 'ink-green', 7.5);
    expect(pal.contrastMain).toBeGreaterThan(9.5);
    expect(pal.contrastMain).toBeLessThan(11.5);
  });

  it('dark default body text still lands near 13:1', () => {
    const pal = palette('dark', 'ink-green', 7.5);
    expect(pal.contrastMain).toBeGreaterThan(12);
    expect(pal.contrastMain).toBeLessThan(14.5);
  });
});

describe('on-primary label colour', () => {
  // `--color-on-primary` is the only label colour guaranteed to contrast with
  // `primary`. index.css applies it to every `bg-primary` surface in BOTH
  // appearances (it used to be dark-mode only, which made white labels vanish
  // on a user-chosen light accent).
  it.each(APPEARANCES)('%s — label contrasts with the fill it sits on', (appearance) => {
    for (const primary of ['#FFFFFF', '#F2E8A0', '#FFE066', '#3A6B52', '#1F1F1F', '#0B0B0C']) {
      const pal = buildPalette({ appearance, primary, purity: 36, contrast: 7.5, preset: 'custom' });
      const ratio = contrastRatioHex(pal.onPrimary, pal.primary);
      // Falls back to `primary` itself only when neither white nor near-black works.
      expect(
        ratio >= 4.5 || pal.onPrimary === pal.primary,
        `${appearance} primary=${primary} onPrimary=${pal.onPrimary} ratio=${ratio.toFixed(2)}`,
      ).toBe(true);
    }
  });

  it('picks a dark label for a light accent (the light-mode bug)', () => {
    const pal = buildPalette({
      appearance: 'light',
      primary: '#F2E8A0',
      purity: 36,
      contrast: 7.5,
      preset: 'custom',
    });
    expect(contrastRatioHex('#FFFFFF', pal.primary)).toBeLessThan(4.5);
    expect(contrastRatioHex(pal.onPrimary, pal.primary)).toBeGreaterThanOrEqual(4.5);
  });
});

describe('pure-white preset stays monochrome', () => {
  it.each(APPEARANCES)('%s — text is neutral grey (r === g === b)', (appearance) => {
    const pal = palette(appearance, 'pure-white', 7.5);
    for (const hex of [pal.textMain, pal.textMuted]) {
      const { r, g, b } = hexToRgb(hex);
      expect({ hex, r, g, b }).toMatchObject({ r: g });
      expect({ hex, g, b }).toMatchObject({ g: b });
    }
  });

  it.each(APPEARANCES)('%s — text still follows the contrast slider', (appearance) => {
    const lo = palette(appearance, 'pure-white', CONTRAST_MIN).contrastMain;
    const hi = palette(appearance, 'pure-white', CONTRAST_MAX).contrastMain;
    expect(hi).toBeGreaterThan(lo + 2);
  });
});

describe('ratio formatting', () => {
  it('formatContrastLabel clamps into the slider range', () => {
    expect(formatContrastLabel(0)).toBe('3.0:1');
    expect(formatContrastLabel(99)).toBe('12.0:1');
  });

  it('formatRatioLabel does not clamp measured values', () => {
    // Dark-mode body copy routinely measures above the slider's own maximum;
    // clamping here would misreport what is actually on screen.
    expect(formatRatioLabel(13.01)).toBe('13.0:1');
    expect(formatRatioLabel(Number.NaN)).toBe('0.0:1');
  });
});

describe('colour helpers', () => {
  it('normalizeHex expands shorthand and rejects junk', () => {
    expect(normalizeHex('#abc')).toBe('#AABBCC');
    expect(normalizeHex('336699')).toBe('#336699');
    expect(normalizeHex('nope')).toBe('#2D4739');
  });

  it('contrastRatioHex is symmetric and bounded', () => {
    expect(contrastRatioHex('#FFFFFF', '#000000')).toBeCloseTo(21, 5);
    expect(contrastRatioHex('#000000', '#FFFFFF')).toBeCloseTo(21, 5);
    expect(contrastRatioHex('#777777', '#777777')).toBeCloseTo(1, 5);
  });
});

describe('preset table integrity', () => {
  it('every preset has both surface pairs and i18n keys', () => {
    for (const meta of PRESET_METAS) {
      expect(meta.light, meta.id).toHaveProperty('bg');
      expect(meta.dark, meta.id).toHaveProperty('bg');
      expect(meta.i18nNameKey).toBe(`themeSettings.presets.${meta.id.replace(/-(\w)/g, (_, c) => c.toUpperCase())}.name`);
    }
  });

  it('never emits the retired "paper" id', () => {
    expect(PRESET_METAS.map((m) => m.id)).not.toContain('paper');
  });
});

/**
 * Mirrors what `applyThemePrefs` actually feeds `buildPalette`: built-in
 * presets resolve their accent through `getPresetPrimary(id, appearance)`,
 * so per-preset `darkPrimary` overrides apply.
 *
 * `random` keeps a user-rolled colour in production; `getPresetPrimary`
 * falls back to `DEFAULT_THEME_PREFS.primary` for it, which is used here as
 * a representative saturated accent (this also exercises the
 * `surfacesFromPrimary` surface path).
 *
 * The `palette()` helper above deliberately does NOT resolve the override —
 * it passes `meta.primary` straight through, which is the *light* accent.
 * That is why the `darkPrimary` mechanism had zero coverage and nobody
 * noticed one preset shipping without it.
 */
function effectivePalette(preset: (typeof PRESET_METAS)[number]['id'], appearance: 'light' | 'dark') {
  return buildPalette({
    appearance,
    primary: getPresetPrimary(preset, appearance),
    purity: DEFAULT_THEME_PREFS.purity,
    contrast: DEFAULT_THEME_PREFS.contrast,
    surfaceHue: getPresetSurfaceHue(preset),
    preset,
  });
}

describe('accent colour stays distinguishable from the page', () => {
  /**
   * The accent is not only used as a *fill* (`bg-primary`). It is also
   * painted as text/iconography directly onto the surface: `text-primary`,
   * `.dark .prose code` (agent inline code such as `file.py:123`), `.prose a`
   * links, focus rings, slider fills.
   *
   * Failure this guards: `pure-white` shipped without a `darkPrimary`, so
   * its near-black accent `#1F1F1F` landed on the near-black dark surface
   * `#0A0A0B` at **1.15:1** — agent-emitted file names and line numbers were
   * invisible in that preset's dark mode (reported from a screenshot).
   *
   * The floor is deliberately NOT the 4.5 body-text threshold: several
   * presets ship a muted mid-tone accent that measures 2.2–3.3:1 in dark
   * mode, which is an accepted, readable design choice. What is never
   * acceptable — and all this catches — is an accent that has collapsed into
   * the surface and reads as "no text at all". The lowest legitimate value
   * in the table is 2.15:1 (random @ panel), so the floor sits just below it.
   */
  const ACCENT_FLOOR = 2.0;

  it.each(APPEARANCES)('%s — every preset accent clears its bg and panel', (appearance) => {
    for (const meta of PRESET_METAS) {
      const pal = effectivePalette(meta.id, appearance);
      const surfaces = { bg: pal.bg, panel: pal.panel };
      for (const [name, surface] of Object.entries(surfaces)) {
        expect(
          contrastRatioHex(pal.primary, surface),
          `${appearance}/${meta.id} accent on ${name}`,
        ).toBeGreaterThanOrEqual(ACCENT_FLOOR);
      }
    }
  });

  it('pure-white dark mode inverts the accent instead of collapsing it', () => {
    const pal = effectivePalette('pure-white', 'dark');
    // Held to the body-text threshold, not the generic floor: for a
    // monochrome preset the accent *is* a foreground neutral, so it has to
    // be readable as one. Before the fix this measured 1.15:1.
    expect(contrastRatioHex(pal.primary, pal.bg)).toBeGreaterThanOrEqual(4.5);
    // ...and the inversion must stay inside the preset's neutral family.
    // This is a "not a colour" bound, not a strict-neutrality one: the
    // purity slider deliberately mixes a faint tint of the base hue into
    // every accent (measured spread 12 for this dark accent, 16 for its
    // `#271717` light counterpart), while a real accent is far above it
    // (e.g. #C792EA spreads 88).
    const { r, g, b } = hexToRgb(pal.primary);
    expect(
      Math.max(r, g, b) - Math.min(r, g, b),
      `accent ${pal.primary}`,
    ).toBeLessThanOrEqual(24);
  });

  it('a dark override never leaks into the light appearance', () => {
    // `random` is excluded on purpose: it bypasses the preset table and
    // carries a user-rolled colour, so it has no `darkPrimary` contract.
    for (const meta of PRESET_METAS) {
      if (meta.id === 'random') continue;
      expect(getPresetPrimary(meta.id, 'light'), meta.id).toBe(meta.primary);
      // Dark mode reuses the same accent unless the preset opts into an
      // override — that opt-in is the only thing keeping accent-painted
      // text readable over there.
      expect(getPresetPrimary(meta.id, 'dark'), meta.id).toBe(meta.darkPrimary ?? meta.primary);
    }
  });
});

describe('floating surfaces carry a visible boundary stroke', () => {
  /**
   * The message composer is a floating card: its elevation is carried by a
   * drop shadow, which in dark mode is black-on-near-black and therefore
   * carries nothing — so the stroke is the only thing left to delineate it.
   * Before `boundary` existed, `border` vs the page surface measured
   * 1.03–1.31:1 for every preset (dark borders are deliberately mixed 70%
   * toward the panel), and the composer was invisible until focus drew its
   * ring — reported from a before/after screenshot pair.
   */
  it('dark — every preset stroke clears the floor at every contrast setting', () => {
    for (const meta of PRESET_METAS) {
      for (const contrast of CONTRASTS) {
        const built = buildPalette({
          appearance: 'dark',
          primary: getPresetPrimary(meta.id, 'dark'),
          purity: DEFAULT_THEME_PREFS.purity,
          contrast,
          surfaceHue: getPresetSurfaceHue(meta.id),
          preset: meta.id,
        });
        expect(
          contrastRatioHex(built.boundary, built.stage),
          `${meta.id} @${contrast}: boundary ${built.boundary} on stage ${built.stage}`,
        ).toBeGreaterThanOrEqual(BOUNDARY_CONTRAST_FLOOR - EPS);
      }
    }
  });

  it('dark — the stroke is the closest one that clears the floor, not a snap to white', () => {
    for (const meta of PRESET_METAS) {
      const pal = effectivePalette(meta.id, 'dark');
      expect(
        contrastRatioHex(pal.boundary, pal.stage),
        `${meta.id} overshoot`,
      ).toBeLessThan(BOUNDARY_CONTRAST_FLOOR + 0.5);
    }
  });

  it('light — the stroke stays exactly the design border (no light-mode repaint)', () => {
    // Shadows do carry elevation on light surfaces, so the soft border is
    // kept; a fix for dark mode must not restyle light mode.
    for (const meta of PRESET_METAS) {
      const pal = effectivePalette(meta.id, 'light');
      expect(pal.boundary, meta.id).toBe(pal.border);
    }
  });
});
