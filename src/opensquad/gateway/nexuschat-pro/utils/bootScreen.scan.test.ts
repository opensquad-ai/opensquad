/**
 * Plugin-contributed boot screen (缝 B) — the shell-side loader's contract.
 *
 * The boot animation itself lives in a *plugin* (declared via
 * `contributes.bootScreen`); the shell only asks the backend what to play. Four
 * properties keep that safe and honest, and each one is invisible in review:
 *
 *   L1  it asks the read-only endpoint `/api/ai-web/boot-screen` (no token — the
 *       loader runs before login);
 *   L2  it is **fail-open**: no declaration, a failed fetch, a video error, or a
 *       `prefers-reduced-motion` user all fall back to the default loader and
 *       must never block startup;
 *   L3  the overlay is appended to `document.body`, NOT `#root` — React replaces
 *       `#root` on mount, so a `#root`-hosted overlay would be wiped before it is
 *       ever seen (this is what makes it visible in the desktop app too);
 *   L4  it always has an exit: `ended` / `holdMs` / click / key, plus a safety
 *       cap when the plugin gave no `holdMs`.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
/** Comments stripped — these assertions are about code, not prose. */
const SHELL = fs
  .readFileSync(path.join(ROOT, 'index.html'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

describe('L1 — it asks the backend, unauthenticated', () => {
  it('fetches the boot-screen endpoint', () => {
    expect(SHELL).toContain("'/api/ai-web/boot-screen'");
  });

  it('carries no bearer token or auth header', () => {
    expect(SHELL).not.toMatch(/boot-screen[\s\S]{0,200}Authorization/);
  });
});

describe('L2 — fail-open, never blocks startup', () => {
  it('honours prefers-reduced-motion by doing nothing', () => {
    expect(SHELL).toContain('prefers-reduced-motion: reduce');
    expect(SHELL).toMatch(/matchMedia\('\(prefers-reduced-motion: reduce\)'\)/);
  });

  it('swallows fetch failures', () => {
    // Comments are stripped above, so match the call, not the marker prose.
    expect(SHELL).toMatch(/\.catch\(function/);
  });

  it('treats a missing or disabled declaration as a no-op', () => {
    expect(SHELL).toMatch(/if \(!data \|\| !data\.enabled \|\| !data\.video/);
  });
});

describe('L3 — it survives React mounting', () => {
  it('appends the overlay to document.body, not #root', () => {
    expect(SHELL).toContain('document.body.appendChild(box)');
    expect(SHELL).not.toMatch(/#root[\s\S]{0,80}appendChild/);
  });

  it('styles the overlay and its leave state', () => {
    expect(SHELL).toMatch(/\.boot-screen\s*\{/);
    expect(SHELL).toMatch(/\.boot-screen\.is-leaving\s*\{/);
  });
});

describe('L4 — it always has a way out', () => {
  it('removes on the video ending or erroring', () => {
    expect(SHELL).toContain("addEventListener('ended', done)");
    expect(SHELL).toContain("addEventListener('error', done)");
  });

  it('lets the user skip with a click or a key', () => {
    expect(SHELL).toContain("box.addEventListener('click', done)");
    expect(SHELL).toContain("window.addEventListener('keydown', done)");
  });

  it('caps the wait when the plugin gave no holdMs', () => {
    expect(SHELL).toMatch(/hold > 0 \? hold : \d+/);
  });
});
