// @vitest-environment node
/**
 * Static guard: a theme colour token may not be used with an opacity modifier
 * unless its value can actually accept one.
 *
 * WHY THIS EXISTS
 * ---------------
 * Tailwind v3 substitutes `/<alpha>` into the token's value string. If the
 * value has no `<alpha-value>` placeholder — which is the case for a bare
 * `color-mix(...)` — the opacity modifier cannot be applied and Tailwind emits
 * **no CSS rule at all**. The class is then simply dead: the element keeps
 * whatever background it inherited, i.e. it renders fully transparent.
 *
 * On 2026-09-14 the chat footer tooltips were reported as "transparent
 * background, should follow the theme". Root cause was exactly this:
 * `bgDark` was a plain `color-mix(...)` while 7 call sites used
 * `bg-bgDark/60`…`/95`, none of which existed in the built CSS
 * (`grep -c 'bg-bgDark\/' dist/assets/*.css` → 0). Six of them are unrelated
 * surfaces that had been silently transparent ever since they were written:
 * the disabled plugin/service chips, the progress-bar tracks, the diff
 * "expand" toggle and the voice overlay.
 *
 * This is a build failure rather than a lint rule because nothing at runtime
 * complains: no console error, no missing asset, no failing behavioural test —
 * only a slightly-too-transparent panel that looks like a design choice.
 *
 *   R1  every `*-<token>/<alpha>` use in the frontend refers to a token whose
 *       value contains `<alpha-value>` (the dead-utility ban)
 *   R2  the token set is pinned, so adding/renaming a token forces a human to
 *       classify it as alpha-capable or not
 *   R3  HoverTooltip paints its bubble with a *theme* surface token — no
 *       hard-coded `bg-white` / `bg-black` / `bg-[#...]`, and no token that
 *       would be dropped by R1
 *
 * Mutations verified (each one makes this file fail):
 *   MB1 `bgDark` back to a single `color-mix(...)` (no `<alpha-value>`)
 *       → R1 (lists all 6 surviving `bg-bgDark/60|/95` sites) + R2 + R2b
 *   MB2 HoverTooltip bubble `bg-panel` → `bg-white`                      → R3
 *   MB3 HoverTooltip bubble `bg-panel` → `bg-panel/50`                   → R3
 *   MB4 drop `bg-*` from the bubble class list entirely                  → R3
 *   MB5 rename the `panel` colour token                                  → R2 + R3
 *
 * Known limits — do NOT over-trust this file:
 *   - It inspects utility class strings, so a class assembled at runtime
 *     (`bg-${x}`) is invisible to it. Tailwind would not generate that class
 *     either, so the blindness is shared with the build.
 *   - `*.test.ts(x)` files are skipped: they contain class names in literals.
 *   - It does not verify the *composited* look (a 95 %-opaque bubble over a
 *     busy background is still hard to read); that is a design call, not a
 *     correctness one.
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require_ = createRequire(import.meta.url);
const ROOT = path.resolve(__dirname, '..');

// ---------------------------------------------------------------------------
// Load the single source of truth: the Tailwind theme.
// ---------------------------------------------------------------------------
interface TailwindConfig {
  theme: { extend: { colors: Record<string, string> } };
}
const tailwind = require_(path.join(ROOT, 'tailwind.config.cjs')) as TailwindConfig;
const COLORS = tailwind.theme.extend.colors;
const TOKENS = Object.keys(COLORS);
const ALPHA_CAPABLE = new Set(TOKENS.filter((t) => COLORS[t].includes('<alpha-value>')));
const NON_ALPHA = TOKENS.filter((t) => !ALPHA_CAPABLE.has(t));

const SKIP_DIRS = new Set(['node_modules', 'dist', '.git', 'resources', 'coverage', 'dist-electron']);
const SOURCE_EXT = ['.ts', '.tsx', '.html'];

function sourceFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      sourceFiles(full, acc);
    } else if (SOURCE_EXT.includes(path.extname(entry.name)) && !/\.test\.tsx?$/.test(entry.name)) {
      acc.push(full);
    }
  }
  return acc;
}

const FILES = sourceFiles(ROOT).map((f) => ({ file: f, rel: path.relative(ROOT, f), src: fs.readFileSync(f, 'utf8') }));

/**
 * Colour utilities that accept a `/<alpha>` modifier. `border-t`/`divide-x` etc.
 * are not colours, so only the plain colour prefixes are listed.
 */
const COLOR_UTILITIES = [
  'bg',
  'text',
  'border',
  'ring',
  'ring-offset',
  'from',
  'to',
  'via',
  'fill',
  'stroke',
  'divide',
  'placeholder',
  'outline',
  'accent',
  'caret',
  'decoration',
  'shadow',
];

// The alpha suffix is part of the match so failure messages quote the whole
// dead class (`bg-bgDark/60`), not just its stem.
const ALPHA_USE = new RegExp(
  `\\b(?:${COLOR_UTILITIES.join('|')})-([A-Za-z][A-Za-z0-9]*)/((?:\\d{1,3})|\\[[^\\]\\s]+\\])`,
  'g',
);

describe('theme token transparency guard', () => {
  it('R1: no opacity modifier is used on a token that cannot take one', () => {
    const violations: string[] = [];
    for (const { rel, src } of FILES) {
      for (const m of src.matchAll(ALPHA_USE)) {
        const token = m[1];
        if (!TOKENS.includes(token)) continue; // Tailwind built-in / arbitrary colour
        if (!ALPHA_CAPABLE.has(token)) violations.push(`${rel}: ${m[0]}`);
      }
    }
    expect(
      violations,
      `Tailwind drops these utilities entirely (element renders transparent):\n${violations.join('\n')}`,
    ).toEqual([]);
  });

  it('R2: the token set is pinned — a new token must be classified', () => {
    expect(TOKENS.slice().sort()).toEqual(
      [
        'bgDark',
        'bgLight',
        'bgPage',
        'border',
        'boundary',
        'chatBubbleOther',
        'chatBubbleSelf',
        'nest',
        'onPrimary',
        'panel',
        'primary',
        'rail',
        'stage',
        'textMain',
        'textMuted',
      ].sort(),
    );
    // Every token is alpha-capable. If one is ever added that is not, it must
    // be added here deliberately — and R1 then bans `token/<alpha>` for it.
    expect(NON_ALPHA).toEqual([]);
  });

  it('R2b: `bgDark` itself accepts `<alpha-value>` (nested color-mix)', () => {
    expect(COLORS.bgDark).toContain('<alpha-value>');
    // Two mixes: the inner one carries the albedo, the outer one the alpha.
    expect(COLORS.bgDark.match(/color-mix\(/g)?.length).toBe(2);
  });

  it('R3: HoverTooltip paints its bubble with an opaque theme surface token', () => {
    const file = FILES.find((f) => f.rel === path.join('components', 'HoverTooltip.tsx'));
    expect(file, 'components/HoverTooltip.tsx not found').toBeTruthy();
    const src = file!.src;

    const start = src.indexOf('const bubbleCls');
    expect(start, 'bubbleCls class list not found').toBeGreaterThan(-1);
    const block = src.slice(start, src.indexOf(';', start));

    const bgClasses = Array.from(block.matchAll(/\bbg-([A-Za-z0-9-]+)(\/[^\s'`]+)?/g)).map((m) => ({
      token: m[1],
      alpha: m[2] ?? '',
      text: m[0],
    }));
    expect(bgClasses.length, `no bg-* utility in the bubble class list:\n${block}`).toBeGreaterThan(0);

    for (const bg of bgClasses) {
      expect(
        TOKENS.includes(bg.token),
        `HoverTooltip bubble must use a theme token, got a hard-coded colour: ${bg.text}`,
      ).toBe(true);
      expect(ALPHA_CAPABLE.has(bg.token) || !bg.alpha, `${bg.text} would be dropped by Tailwind`).toBe(true);
      // An opaque surface is the point of the fix — a translucent bubble lets
      // message text bleed through the label.
      expect(bg.alpha, `HoverTooltip bubble background must be opaque, got ${bg.text}`).toBe('');
    }
  });
});
