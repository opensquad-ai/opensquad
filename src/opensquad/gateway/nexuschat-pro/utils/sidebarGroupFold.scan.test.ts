// @vitest-environment node
/**
 * Static guard for the collapsible sidebar groups (置顶 / 通讯 / 最近 / 归档).
 *
 * WHY THIS EXISTS
 * ---------------
 * Two things in this feature are invisible to a type checker and easy to
 * "clean up" back into a worse state:
 *
 *   1. The fold only animates because the rows stay MOUNTED (`0fr` grid row +
 *      `opacity: 0`). Reinstating the old `{open ? children : null}` conditional
 *      render compiles fine, passes every behavioural test, and silently turns
 *      the fold back into a snap — which is exactly what the user complained
 *      about (the left/right rails animate; this list did not).
 *   2. The header is a theme-tinted card whose resting wash must sit BETWEEN the
 *      page background and the hover wash — "white deeper by a little, half of
 *      the hover grey". A drift to `0` (invisible) or up to the hover value
 *      (no hover feedback left) is a silent design regression.
 *
 *   R1  index.css defines the three fold classes, uses the rail timing
 *       (`--duration-panel` + `--ease-soft`), keeps the chevron a single rotated
 *       node, and clips the `0fr` row via `.os-collapse-body`
 *   R2  SessionSidebar renders the fold unconditionally (no `open ? … : null`),
 *       swaps in the rotated chevron, and drops the un-animatable Right/Down swap
 *   R3  resting header tint > 0 and < hover tint, in BOTH appearances
 *   R4  all three classes are covered by the `prefers-reduced-motion` opt-out
 *
 * Mutations verified (each one makes this file fail):
 *   MF1 `SidebarSection` back to `{open ? children : null}`            → R2
 *   MF2 drop `overflow: hidden` from `.os-collapse-body`               → R1
 *   MF3 `.os-collapse.is-closed` → `grid-template-rows: 1fr`           → R1
 *   MF4 chevron back to `<ChevronRight/>`/`<ChevronDown/>` swap        → R2
 *   MF5 `.os-group-header` resting tint → `0%` (invisible)             → R3
 *   MF6 `.os-group-header` resting tint → the hover value (10%)        → R3
 *   MF7 drop the classes from the reduced-motion block                  → R4
 *
 * Known limits — do NOT over-trust this file:
 *   - Source-text assertions, not a rendering test. `grid-template-rows`
 *     interpolation itself is a browser capability (Chromium >= 107); this file
 *     only pins that we asked for it.
 *   - The tint check reads the declared percentages, not the composited result.
 *     For the on-screen value measure `getComputedStyle` in a real browser.
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(__dirname, '..');
const CSS = fs.readFileSync(path.join(ROOT, 'index.css'), 'utf8');
const SIDEBAR = fs.readFileSync(path.join(ROOT, 'components/ai-chat/SessionSidebar.tsx'), 'utf8');

/** Body of a rule that starts at column 0 (so `.dark .x` can be told from `.x`). */
function ruleAtLineStart(selector: string): string {
  const m = CSS.match(new RegExp(`\\n${selector.replace(/[.]/g, '\\.')}\\s*\\{([^}]*)\\}`));
  expect(m, `rule not found in index.css: ${selector}`).toBeTruthy();
  return m![1];
}

/** First percentage in a declaration body (`color-mix(… 5%, transparent)` → 5). */
function pct(body: string): number {
  const m = body.match(/([\d.]+)%/);
  expect(m, `no percentage in: ${body.trim()}`).toBeTruthy();
  return Number(m![1]);
}

const group = () => SIDEBAR.slice(SIDEBAR.indexOf('const SidebarSection'), SIDEBAR.indexOf('const SessionSidebarInner'));

/** Body of the `prefers-reduced-motion` block that mentions `marker`.
 *  Brace-matched, because the file has several such blocks (pulse orbit, …) and
 *  `lastIndexOf` happily lands on the wrong one. */
function reducedMotionBlockContaining(marker: string): string {
  for (const m of CSS.matchAll(/@media \(prefers-reduced-motion: reduce\)\s*\{/g)) {
    const start = m.index! + m[0].length;
    let depth = 1;
    let i = start;
    while (i < CSS.length && depth > 0) {
      if (CSS[i] === '{') depth++;
      else if (CSS[i] === '}') depth--;
      i++;
    }
    const body = CSS.slice(start, i - 1);
    if (body.includes(marker)) return body;
  }
  throw new Error(`no \`prefers-reduced-motion\` block mentions ${marker}`);
}

describe('sidebar group fold + tint guard', () => {
  it('R1: the fold animates on the rail timing and the 0fr row is clipped', () => {
    const collapse = ruleAtLineStart('.os-collapse');
    expect(collapse).toContain('grid-template-rows');
    expect(collapse).toContain('var(--duration-panel)');
    expect(collapse).toContain('var(--ease-soft)');

    // Collapsed state must be `0fr` — `1fr` there is the silent "no fold" bug.
    const closed = ruleAtLineStart('.os-collapse.is-closed');
    expect(closed).toContain('0fr');

    // Without this the 0fr row still paints its overflow (content leak).
    const body = ruleAtLineStart('.os-collapse-body');
    expect(body).toContain('overflow: hidden');
    expect(body).toContain('min-height: 0');

    // A swapped icon node cannot transition; rotate one chevron instead.
    expect(ruleAtLineStart('.os-fold-chevron')).toContain('transition');
    expect(ruleAtLineStart('.os-fold-chevron.is-open')).toContain('rotate(90deg)');
  });

  it('R2: SessionSidebar folds through the shared primitive', () => {
    const block = group();
    expect(block).toContain('os-group-header');
    // Mount/clip/`inert`/rotation all live in the primitive now — see
    // `foldPrimitive.scan.test.ts` R1. Here we only pin that the sidebar goes
    // THROUGH it instead of hand-rolling the classes.
    expect(block).toContain('<Collapse open={open}>');
    expect(block).toContain('<FoldChevron open={open}');
    // The old conditional render — the exact thing that killed the animation.
    // (`${open ? … }` inside the className template literal is expected and fine;
    // only a conditional around the CHILDREN means they get unmounted.)
    expect(block).not.toMatch(/open\s*\?\s*children/);
    expect(block).not.toContain('ChevronDown');
    expect(block).toContain('aria-expanded');
  });

  it('R3: resting tint is deeper than the page but shallower than the hover wash', () => {
    const rest = pct(ruleAtLineStart('.os-group-header'));
    const restDark = pct(ruleAtLineStart('.dark .os-group-header'));
    const hover = pct(ruleAtLineStart('.os-group-header:hover'));
    const hoverDark = pct(ruleAtLineStart('.dark .os-group-header:hover'));
    for (const [label, v] of [
      ['rest', rest],
      ['restDark', restDark],
      ['hover', hover],
      ['hoverDark', hoverDark],
    ] as const) {
      expect(v, `${label} must be a non-zero wash`).toBeGreaterThan(0);
    }
    expect(rest).toBeLessThan(hover);
    expect(restDark).toBeLessThan(hoverDark);
    // The hover wash is shared with the session rows (`.os-interactive`) — if one
    // moves, the "halfway" resting value has to move with it.
    expect(pct(ruleAtLineStart('.dark .os-interactive:hover'))).toBe(hoverDark);
  });

  it('R4: every animated fold class honours prefers-reduced-motion', () => {
    const block = reducedMotionBlockContaining('.os-soft-rail-inner');
    for (const cls of ['.os-group-header', '.os-fold-chevron', '.os-collapse']) {
      expect(block, `${cls} missing from the reduced-motion opt-out`).toContain(cls);
    }
    expect(block).toContain('transition: none !important');
  });
});
