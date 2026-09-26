// @vitest-environment jsdom
/**
 * Regression locks: chat fenced code wells must follow the appearance exactly
 * like the file pane, with syntax token colours coming from ONE source
 * (utils/codeHighlight.ts → HLJS_THEME_CSS injected via ensureHljsTheme()).
 *
 * Background (2026-09-13 bug report): `.ai-markdown .ai-code-wrap` carried a
 * hardcoded near-black well (`#0b0f17`) in BOTH appearances plus a hardcoded
 * `#c8cdd8` text colour and two appearance-blind Palenight subsets in
 * index.css — agent code blocks floated as black rectangles on light themed
 * pages. The file pane already followed the theme; chat did not.
 *
 * Source-level assertions (fs.readFileSync), same technique as
 * themeStore.test.ts — there is no DOM stylesheet under test here, the
 * shipped CSS text itself is the contract.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

/** vitest cwd is `nexuschat-pro/` (see themeStore.test.ts for why). */
const APP_ROOT = process.cwd();
const INDEX_CSS = fs.readFileSync(path.join(APP_ROOT, 'index.css'), 'utf8');
const FENCED_SRC = fs.readFileSync(path.join(APP_ROOT, 'utils/fencedMarkdown.ts'), 'utf8');
const HIGHLIGHT_SRC = fs.readFileSync(path.join(APP_ROOT, 'utils/codeHighlight.ts'), 'utf8');

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Body of the first rule whose selector matches literally. */
function ruleFor(selector: string, css: string = INDEX_CSS): string | null {
  const m = new RegExp(`${escapeRe(selector)}\\s*\\{([^}]*)\\}`).exec(css);
  return m ? m[1] : null;
}

describe('chat code wells follow the appearance like the file pane', () => {
  it('light — the well derives from theme tokens, not a hardcoded black', () => {
    const body = ruleFor('.ai-markdown .ai-code-wrap');
    expect(body, 'base .ai-markdown .ai-code-wrap rule missing').not.toBeNull();
    // Same well formula the file pane uses (file-markdown block was folded
    // into this base rule — identical values, one path).
    expect(body).toContain('background: color-mix(in srgb, rgb(var(--color-bg)) 88%, rgb(var(--color-text-muted)) 12%)');
    expect(body).not.toContain('#0b0f17');
  });

  it('dark — the well keeps the classic near-black via the shared html.dark token', () => {
    const darkRule = ruleFor('html.dark .ai-markdown .ai-code-wrap');
    expect(darkRule, 'html.dark well rule missing').not.toBeNull();
    // The near-black is defined once as a token on html.dark and consumed by
    // every dark code well (chat, file pane, skill previews) — a light-scope
    // reoccurrence is how the original bug looked before it was ever reported.
    const tokenRule = ruleFor('html.dark');
    expect(tokenRule, 'html.dark token rule missing').not.toBeNull();
    expect(tokenRule).toContain('--os-code-well-bg: #0b0f17');
    expect(darkRule).toContain('var(--os-code-well-bg)');
    const occurrences = INDEX_CSS.match(/#0b0f17/g)?.length ?? 0;
    expect(occurrences, '#0b0f17 must exist only in the html.dark token rule').toBe(1);
  });

  it('light — code text uses --color-text-main; #c8cdd8 only under html.dark', () => {
    const body = ruleFor('.ai-markdown .ai-code-block code');
    expect(body, 'base code colour rule missing').not.toBeNull();
    expect(body).toContain('color: rgb(var(--color-text-main))');
    const darkRule = ruleFor('html.dark .ai-markdown .ai-code-block code');
    expect(darkRule, 'html.dark code colour rule missing').not.toBeNull();
    expect(darkRule).toContain('#c8cdd8');
  });

  it('index.css no longer pins chat token colours (single source in codeHighlight)', () => {
    // Both Palenight subsets were removed. If any `.hljs-*` colour rule lives
    // here again it will override the injected two-palette theme for chat
    // (higher specificity) and light mode regresses to pastel-on-light.
    const stray = INDEX_CSS.match(/\.ai-markdown[^{]*\.hljs-[^{]*\{[^}]*color/g);
    expect(stray, `index.css must not colour .hljs-* under .ai-markdown: ${stray?.join(' | ')}`).toBeNull();
    expect(INDEX_CSS).not.toContain('.ai-markdown:not(.file-markdown) .hljs-');
  });

  it('renderFencedMarkdown injects the shared token theme exactly once', () => {
    expect(HIGHLIGHT_SRC).toContain('export function ensureHljsTheme');
    // Guarded: a second call must be a no-op (one <style> per document).
    expect(HIGHLIGHT_SRC).toMatch(/if \(themeInjected/);
    // The producer of `.ai-code-wrap` HTML is what mounts the stylesheet —
    // chat, file pane and any future surface all funnel through here.
    expect(FENCED_SRC).toContain("import { ensureHljsTheme } from './codeHighlight'");
    expect(FENCED_SRC).toMatch(/export function renderFencedMarkdown[^]*?ensureHljsTheme\(\)/);
  });

  it('the injected theme really carries both palettes', () => {
    expect(HIGHLIGHT_SRC).toContain('html.dark .hljs-keyword');
    // GitHub Light sentinels (light appearance) vs Palenight sentinels (dark).
    expect(HIGHLIGHT_SRC).toContain('#cf222e');
    expect(HIGHLIGHT_SRC).toContain('#c792ea');
  });
});
