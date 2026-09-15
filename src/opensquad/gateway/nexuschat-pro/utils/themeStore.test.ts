// @vitest-environment jsdom
/**
 * Regression locks for the theme store: preference sanitising, legacy
 * migration, and the CSS custom properties `applyThemePrefs` writes.
 *
 * Notes for whoever edits this file:
 *  - jsdom is required — `applyThemePrefs` returns early without a `document`,
 *    so a node-environment run would silently skip every CSS assertion below.
 *  - Do NOT switch this to happy-dom. It defines `nodeName` on the base
 *    `Node.prototype` (returning `''`), which breaks DOMPurify-style DOM
 *    walks elsewhere in the suite; jsdom is the shared, working choice.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import {
  DEFAULT_THEME_PREFS,
  PRESET_METAS,
  contrastRatioHex,
  hexToRgb,
} from './themeEngine';
import {
  applyThemePrefs,
  appearanceSliderPatch,
  getActivePalette,
  loadThemePrefs,
  sanitizePrefs,
  saveThemePrefs,
  updateThemePrefs,
  THEME_STORAGE_KEY,
  LEGACY_THEME_KEY,
  migrateLegacyChatTheme,
} from './themeStore';

/** `import.meta.url` is not a `file:` URL under the jsdom environment, so read
 *  the app root from the vitest cwd (which is `nexuschat-pro/`). */
const APP_ROOT = process.cwd();
const INDEX_CSS = fs.readFileSync(path.join(APP_ROOT, 'index.css'), 'utf8');
const INDEX_HTML = fs.readFileSync(path.join(APP_ROOT, 'index.html'), 'utf8');
const THEME_STORE_SRC = fs.readFileSync(path.join(APP_ROOT, 'utils/themeStore.ts'), 'utf8');

const rgbTriplet = (hex: string) => {
  const { r, g, b } = hexToRgb(hex);
  return `${r} ${g} ${b}`;
};

describe('sanitizePrefs — numeric prefs', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  // The bug: `Number(v) || fallback` treats 0 as absent. PURITY_MIN is 0, so
  // dragging the vibrance slider to its left end silently snapped back to 36.
  it('keeps an explicit purity of 0', () => {
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, purity: 0 }).purity).toBe(0);
    // A stringified 0 arrives from JSON-ish sources (host UI-prefs round-trip).
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, purity: '0' as never }).purity).toBe(0);
  });

  it('falls back only when the value is absent or unparseable', () => {
    for (const bad of [undefined, null, '', 'abc', Number.NaN]) {
      expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, purity: bad as never }).purity).toBe(
        DEFAULT_THEME_PREFS.purity,
      );
    }
  });

  it('clamps out-of-range numbers instead of discarding them', () => {
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, purity: 999 }).purity).toBe(100);
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, purity: -5 }).purity).toBe(0);
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, contrast: 0 }).contrast).toBe(3);
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, contrast: 500 }).contrast).toBe(12);
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, fontSize: 0 }).fontSize).toBe(0.875);
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, fontSize: 9 }).fontSize).toBe(1.25);
  });

  it('normalises the primary colour', () => {
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, primary: '#abc' }).primary).toBe('#AABBCC');
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, primary: 'garbage' }).primary).toBe('#2D4739');
  });
});

describe('sanitizePrefs — preset ids', () => {
  it('accepts every preset the UI offers, plus custom', () => {
    for (const meta of PRESET_METAS) {
      expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, preset: meta.id }).preset).toBe(meta.id);
    }
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, preset: 'custom' }).preset).toBe('custom');
  });

  // 'paper' was renamed to 'rose' but stayed in the hand-written validator, so
  // a stale value passed through and never self-healed: no preset highlighted,
  // surfaces derived from a fallback primary, forever.
  it('rejects the retired "paper" id and self-heals to the default', () => {
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, preset: 'paper' as never }).preset).toBe(
      DEFAULT_THEME_PREFS.preset,
    );
  });

  it('rejects unknown ids and invalid modes', () => {
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, preset: 'nope' as never }).preset).toBe(
      DEFAULT_THEME_PREFS.preset,
    );
    expect(sanitizePrefs({ ...DEFAULT_THEME_PREFS, mode: 'twilight' as never }).mode).toBe(
      DEFAULT_THEME_PREFS.mode,
    );
  });

  it('the validator stays in sync with the preset table', () => {
    // Derived from PRESET_METAS, so a new preset can never be forgotten here.
    expect(THEME_STORE_SRC).toMatch(/PRESET_METAS\.some\(\(m\) => m\.id === v\)/);
    expect(THEME_STORE_SRC).not.toMatch(/v === 'paper'/);
  });
});

describe('legacy migration', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('maps a legacy chat_theme value onto the v2 prefs', () => {
    localStorage.setItem(LEGACY_THEME_KEY, 'midnight');
    const migrated = migrateLegacyChatTheme();
    expect(migrated).not.toBeNull();
    expect(migrated!.mode).toBe('dark');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeTruthy();
  });

  it('does not touch an existing v2 entry', () => {
    localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(DEFAULT_THEME_PREFS));
    localStorage.setItem(LEGACY_THEME_KEY, 'midnight');
    expect(migrateLegacyChatTheme()).toBeNull();
    expect(loadThemePrefs().mode).toBe(DEFAULT_THEME_PREFS.mode);
  });

  it('falls back to defaults for an unknown legacy value', () => {
    localStorage.setItem(LEGACY_THEME_KEY, 'not-a-theme');
    const migrated = migrateLegacyChatTheme();
    expect(migrated!.preset).toBe(DEFAULT_THEME_PREFS.preset);
  });
});

describe('applyThemePrefs — CSS custom properties', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('style');
    document.documentElement.className = '';
  });

  it('writes RGB triplets, never hex', () => {
    // hex here makes `rgb(var(--color-x) / α)` invalid, the declaration is
    // dropped, and every themed border falls back to `currentColor` — the
    // original "white border in dark mode" bug.
    const prefs = { ...DEFAULT_THEME_PREFS, mode: 'light' as const, preset: 'ink-green' as const };
    applyThemePrefs(prefs);
    const styles = document.documentElement.style;
    const palette = getActivePalette();
    expect(palette, 'applyThemePrefs must record the applied palette').not.toBeNull();
    for (const token of [
      'primary',
      'on-primary',
      'bg',
      'rail',
      'nest',
      'stage',
      'panel',
      'border',
      'bubble-self',
      'bubble-other',
      'text-main',
      'text-muted',
    ] as const) {
      expect(styles.getPropertyValue(`--color-${token}`).trim(), `--color-${token}`).toMatch(
        /^\d{1,3} \d{1,3} \d{1,3}$/,
      );
    }
    expect(styles.getPropertyValue('--color-primary').trim()).toBe(rgbTriplet(palette!.primary));
    expect(styles.getPropertyValue('--color-text-main').trim()).toBe(rgbTriplet(palette!.textMain));
    // boot background keeps a real <color> so the loader can transition it
    expect(styles.getPropertyValue('--boot-bg').trim()).toBe(palette!.bg);
  });

  it('toggles the dark class and records appearance + preset on the root', () => {
    applyThemePrefs({ ...DEFAULT_THEME_PREFS, mode: 'dark' });
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.dataset.appearance).toBe('dark');
    applyThemePrefs({ ...DEFAULT_THEME_PREFS, mode: 'light', preset: 'lake-blue' });
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(document.documentElement.dataset.appearance).toBe('light');
    expect(document.documentElement.dataset.themePreset).toBe('lake-blue');
  });

  it('drives chat font size, font family and the serif switch', () => {
    applyThemePrefs({ ...DEFAULT_THEME_PREFS, fontSize: 1.2, serif: true });
    const styles = document.documentElement.style;
    expect(styles.getPropertyValue('--chat-font-size').trim()).toBe('1.2');
    expect(styles.getPropertyValue('--font-chat')).toMatch(/serif/i);
    expect(document.documentElement.classList.contains('font-serif')).toBe(true);
    applyThemePrefs({ ...DEFAULT_THEME_PREFS, serif: false });
    expect(document.documentElement.classList.contains('font-serif')).toBe(false);
  });

  it('strips retired theme-* classes from earlier versions', () => {
    document.documentElement.classList.add('theme-purple', 'theme-dracula');
    applyThemePrefs(DEFAULT_THEME_PREFS);
    expect(document.documentElement.classList.contains('theme-purple')).toBe(false);
    expect(document.documentElement.classList.contains('theme-dracula')).toBe(false);
  });

  it('keeps document chrome in sync with the palette', () => {
    applyThemePrefs({ ...DEFAULT_THEME_PREFS, mode: 'light' as const });
    const palette = getActivePalette()!;
    const asRgb = (hex: string) => {
      const { r, g, b } = hexToRgb(hex);
      return `rgb(${r}, ${g}, ${b})`;
    };
    const acceptable = [palette.bg, asRgb(palette.bg)];
    expect(acceptable).toContain(document.documentElement.style.backgroundColor);
    expect(acceptable).toContain(document.body.style.backgroundColor);
  });

  it('exposes the applied palette via getActivePalette', () => {
    applyThemePrefs({ ...DEFAULT_THEME_PREFS, mode: 'dark', preset: 'ink-green' });
    const pal = getActivePalette();
    expect(pal).not.toBeNull();
    expect(pal!.contrastMain).toBeCloseTo(contrastRatioHex(pal!.textMain, pal!.rail), 5);
    expect(pal!.contrastMain).toBeGreaterThanOrEqual(7.5 - 0.02);
  });
});

describe('updateThemePrefs / saveThemePrefs', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('style');
  });

  it('applies, persists and returns the sanitized prefs', () => {
    const next = updateThemePrefs({ purity: 0, contrast: 4.2 });
    expect(next.purity).toBe(0);
    expect(JSON.parse(localStorage.getItem(THEME_STORAGE_KEY)!).purity).toBe(0);
  });

  it('never persists an invalid preset', () => {
    updateThemePrefs({ preset: 'paper' as never });
    expect(JSON.parse(localStorage.getItem(THEME_STORAGE_KEY)!).preset).toBe(
      DEFAULT_THEME_PREFS.preset,
    );
  });

  it('does not rewrite the preset when only purity/contrast change', () => {
    // The settings sliders used to force `preset: 'custom'`, which discarded
    // the preset's surface pair and repainted the page (measured: ink-green
    // #EFF0EB warm → #EBF0EE cool green).
    saveThemePrefs({ ...DEFAULT_THEME_PREFS, preset: 'ink-green' });
    const next = updateThemePrefs({ contrast: 9 });
    expect(next.preset).toBe('ink-green');
  });

  it('appearance sliders send exactly one field, never a preset', () => {
    for (const field of ['purity', 'contrast', 'fontSize'] as const) {
      const patch = appearanceSliderPatch(field, 5);
      expect(Object.keys(patch), field).toEqual([field]);
      expect('preset' in patch, field).toBe(false);
    }
  });

  it('the settings panel routes every slider through that helper', () => {
    // Behavioural coverage of the React component would need a DOM testing
    // library this repo does not have, so the wiring is pinned at source
    // level: all three sliders must go through `appearanceSliderPatch`, and
    // the old inline `preset: … 'custom'` payload must not come back.
    const panel = fs.readFileSync(path.join(APP_ROOT, 'components/ThemeSettingsModal.tsx'), 'utf8');
    for (const field of ['purity', 'contrast', 'fontSize']) {
      expect(panel, field).toContain(`appearanceSliderPatch('${field}'`);
    }
    expect(panel).not.toMatch(/preset:\s*prefs\.preset === 'random'/);
  });
});

describe('index.css — primary fill label colour', () => {
  // Guard for the light-mode half of the bug: the rule must NOT be scoped to
  // `.dark`, or a user-chosen light accent leaves `text-white` at 1.00:1.
  it('applies --color-on-primary to bg-primary in both appearances', () => {
    const rule = /(^|\n)([^\n{]*\.bg-primary[^\n{]*)\{([^}]*)\}/.exec(INDEX_CSS);
    expect(rule, '.bg-primary rule not found in index.css').not.toBeNull();
    const selector = rule![2].trim();
    expect(selector).toBe('.bg-primary');
    expect(selector.startsWith('.dark')).toBe(false);
    expect(rule![3]).toContain('var(--color-on-primary)');
  });

  it('defines an on-primary fallback in the boot stylesheet', () => {
    expect(INDEX_HTML).toMatch(/--color-on-primary:\s*\d{1,3} \d{1,3} \d{1,3}/);
  });
});

describe('agent inline code is legible in both appearances', () => {
  /**
   * Reported bug: with the `pure-white` preset in dark mode, agent-emitted
   * file references (`chat_api.py:1204`) were invisible in the chat.
   *
   * Chat bodies carry `prose` + `ai-markdown`, so this boot-stylesheet rule
   * is what paints every inline `<code>` in dark mode. It is only safe as
   * long as the token it points at is legible on the page — which is the
   * theme engine's job, not this rule's. Pin both halves:
   *   - here: the chain (inline code → `--color-primary`), so a future edit
   *     cannot swap in a hard-coded colour and quietly bypass the engine
   *     guarantee;
   *   - themeEngine.test.ts "accent colour stays distinguishable from the
   *     page": the guarantee itself, for every preset × appearance.
   */
  it('paints dark-mode inline code with --color-primary', () => {
    const rule = /\.dark\s+\.prose\s+code\s*\{([^}]*)\}/.exec(INDEX_HTML);
    expect(rule, '.dark .prose code rule not found in index.html').not.toBeNull();
    expect(rule![1]).toMatch(/color:\s*rgb\(var\(--color-primary\)\)/);
  });
});
