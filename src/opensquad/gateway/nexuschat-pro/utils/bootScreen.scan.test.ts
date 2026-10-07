/**
 * Plugin-contributed boot screen (缝 B) — the shell-side loader's contract.
 *
 * The boot animation itself lives in a *plugin* (declared via
 * `contributes.bootScreen`); the shell only asks the backend what to play. Four
 * properties keep that safe and honest, and each one is invisible in review:
 *
 *   L1  it asks the read-only endpoint `/api/ai-web/boot-screen` (no token — the
 *       loader runs before login);
 *   L2  it is **fail-open**: no declaration, a failed fetch, or a
 *       `prefers-reduced-motion` user falls back to the default loader, and a clip
 *       that cannot play is rotated past rather than treated as fatal — only a full
 *       lap of failures gives up. It must never block startup;
 *   L3  the overlay is appended to `document.body`, NOT `#root` — React replaces
 *       `#root` on mount, so a `#root`-hosted overlay would be wiped before it is
 *       ever seen (this is what makes it visible in the desktop app too);
 *   L4  it always has an exit: the playlist rotates until the app is ready, then the
 *       current clip ends it — plus click / key and a hard cap that does not depend
 *       on the plugin.
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
    // The response carries a *playlist*; the loader normalises it before deciding.
    expect(SHELL).toMatch(/var playlist = Array\.isArray\(data\.videos\)/);
    expect(SHELL).toMatch(/if \(!data \|\| !data\.enabled \|\| !playlist\.length/);
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

  it('stacks above every layer the app itself uses', () => {
    // The app has opaque full-screen panels at z-50 / z-[100] / z-[9999]. An overlay
    // below them is visible only until the UI mounts — which reads as "the animation
    // stopped early". Pinned as a number so a later tidy-up cannot quietly lower it.
    const block = SHELL.match(/\.boot-screen\s*\{([^}]*)\}/);
    expect(block, '.boot-screen rule went missing').toBeTruthy();
    const z = Number((block![1].match(/z-index:\s*(\d+)/) || [])[1]);
    expect(z).toBeGreaterThan(9999);
  });
});

describe('L4 — it always has a way out', () => {
  it('rotates the playlist instead of stopping at the first clip', () => {
    // Not one clip on repeat: the shell walks the list and wraps around.
    expect(SHELL).toMatch(/index = \(index \+ 1\) % playlist\.length/);
    expect(SHELL).toMatch(/addEventListener\('ended', function/);
    expect(SHELL).toMatch(/addEventListener\('error', function/);
  });

  it('gives up only after a whole lap of failures', () => {
    // One dead clip must not end the animation; a full lap must not hang it.
    expect(SHELL).toMatch(/failures \+= 1/);
    expect(SHELL).toMatch(/failures >= playlist\.length/);
  });

  it('lets the user skip with a click or a key', () => {
    expect(SHELL).toContain("box.addEventListener('click', done)");
    expect(SHELL).toContain("window.addEventListener('keydown', done)");
  });

  it('caps the wait when the plugin gave no holdMs', () => {
    // 120s — the documented hard cap, matching the backend's MAX_HOLD_MS.
    expect(SHELL).toMatch(/hold > 0 \? hold : 120000/);
  });
});
