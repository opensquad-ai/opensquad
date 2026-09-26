/**
 * Markdown table row hover — the highlight that marks the row you are reading.
 *
 * The table styles live in `index.html`'s boot stylesheet (not `index.css`),
 * next to the rest of `.ai-markdown .ai-table-wrap`, so the rule is pinned
 * there. Three details are easy to lose and invisible in review:
 *
 *   R1  the tint is painted on the CELLS, not the <tr> — under
 *       `border-collapse: collapse` a row background only shows where the cells
 *       above it are transparent, so a `tbody tr { background }` rewrite can
 *       look fine in one theme and drop the highlight in another;
 *   R2  the tint is translucent and derived from `--color-text-main`, so the
 *       same rule darkens a light theme and lightens a dark one. An opaque
 *       `background: #eee` would be a bright band in dark mode;
 *   R3  it stays scoped to markdown tables (`.ai-table-wrap`) — a bare
 *       `tr:hover` would repaint every table in the app, including the
 *       file-preview and task-panel surfaces that never asked for it.
 *
 * Mutation verified: repointing the selector at `tbody tr`, hard-coding an
 * opaque grey, and dropping the `.ai-table-wrap` scope each fail a rule here.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
/** Source with comments stripped — these rules are about code, not prose. */
const SHELL = fs
  .readFileSync(path.join(ROOT, 'index.html'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

const rules = [...SHELL.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({
  selector: m[1].trim().replace(/\s+/g, ' '),
  body: m[2].trim(),
}));

const hoverRule = rules.find((r) => /:hover/.test(r.selector) && /ai-table-wrap/.test(r.selector));

describe('R1 — the hover paints the cells', () => {
  it('targets the body rows of a markdown table', () => {
    expect(hoverRule, 'the table row-hover rule went missing').toBeTruthy();
    expect(hoverRule!.selector).toContain('.ai-markdown');
    expect(hoverRule!.selector).toContain('tbody tr:hover');
  });

  it('paints `> td`, not the row box', () => {
    expect(hoverRule!.selector).toMatch(/tbody tr:hover > td$/);
  });
});

describe('R2 — the tint works in either appearance', () => {
  it('is translucent', () => {
    const alpha = hoverRule!.body.match(/rgb\(var\(--color-text-main\)\s*\/\s*([\d.]+)\)/);
    expect(alpha, 'the tint must be derived from --color-text-main with an alpha').toBeTruthy();
    const value = Number(alpha![1]);
    expect(value).toBeGreaterThan(0);
    expect(value).toBeLessThan(0.25);
  });

  it('has no opaque colour literal that would only suit one theme', () => {
    expect(hoverRule!.body).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(hoverRule!.body).not.toMatch(/\brgba?\(\s*\d/);
  });
});

describe('R3 — it stays scoped to markdown tables', () => {
  it('names the table wrapper', () => {
    expect(hoverRule!.selector).toContain('.ai-table-wrap');
  });

  it('and there is no unscoped row hover anywhere in the shell', () => {
    const stray = rules.filter(
      (r) => /(^|[\s,])tr:hover/.test(r.selector) && !r.selector.includes('.ai-table-wrap'),
    );
    expect(stray.map((r) => r.selector)).toEqual([]);
  });
});
