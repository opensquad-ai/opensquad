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

  it('waits for the app to be usable, not merely mounted', () => {
    // Two conditions, and either one alone is a bug the user can see:
    //   - mounting is not readiness (the app shows the same quad loader while it
    //     fetches its config), so the static-loader signal is gone;
    //   - leaving the gate is not readiness either (the app keeps showing that quad
    //     loader while it loads sessions), so it must also be gone from #root.
    expect(SHELL).toMatch(/data-opensquad-ready/);
    expect(SHELL).not.toMatch(/querySelector\('\.boot-loader-wrap'\)/);
    expect(SHELL).toMatch(/!quadLoaders\(\)/);
    expect(SHELL).toMatch(/#root svg\[role="status"\]/);
  });
});

describe('L3 — it survives React mounting', () => {
  it('appends the overlay to document.body, not #root', () => {
    expect(SHELL).toContain('document.body.appendChild(box)');
    // No appendChild onto the root element anywhere (a #root-hosted overlay would be
    // wiped by React on mount). Kept per-statement so a selector string mentioning
    // `#root` on another line cannot trip it.
    expect(SHELL).not.toMatch(/(rootEl|getElementById\('root'\))[^;\n]{0,60}appendChild/);
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

describe('L5 — the built-in quad is never what the user sees', () => {
  it('declares the overlay CSS in <head>, never inside #root', () => {
    // React clears #root's children on mount. A `.boot-screen` rule living inside
    // #root therefore vanishes ~600ms in: the overlay drops out of its fixed
    // full-screen position into normal flow at the bottom of the page and the user
    // sees the app's own quad instead. Measured: position:static, top=viewport height.
    const headEnd = SHELL.indexOf('</head>');
    const rootStart = SHELL.indexOf('<div id="root"');
    const ruleAt = SHELL.indexOf('.boot-screen {');
    expect(headEnd, 'no </head> in the shell').toBeGreaterThan(-1);
    expect(ruleAt, 'the .boot-screen rule went missing').toBeGreaterThan(-1);
    expect(ruleAt, 'overlay CSS must be declared before </head>').toBeLessThan(headEnd);
    expect(rootStart, '#root must start after </head>').toBeGreaterThan(headEnd);
  });

  it('swaps the poster in before it swaps the clip, so a seam is never blank', () => {
    // Changing src drops the current frame; the poster (next clip's captured first
    // frame) has to be in place first, otherwise the seam shows the backdrop.
    const at = SHELL.indexOf('var advance = function');
    expect(at, 'advance() went missing').toBeGreaterThan(-1);
    const body = SHELL.slice(at, at + 700);
    const posterAt = body.indexOf('video.poster = cover');
    const srcAt = body.indexOf('video.src = nextUrl');
    expect(posterAt, 'advance() no longer sets a poster').toBeGreaterThan(-1);
    expect(srcAt, 'advance() no longer swaps the clip').toBeGreaterThan(-1);
    expect(posterAt, 'poster must be set before src').toBeLessThan(srcAt);
  });

  it('hides the static glyph before the network answers', () => {
    // The glyph node stays in #root (the readiness check counts it); only its
    // visibility goes away, so the first moments are animation-only.
    expect(SHELL).toMatch(/#root \.boot-loader-wrap > svg\{visibility:hidden\}/);
  });

  it('plays the cached playlist first, so the first paint is already the animation', () => {
    expect(SHELL).toContain('localStorage.getItem(CACHE_KEY)');
    expect(SHELL).toContain('localStorage.setItem(');
  });

  it('hands over only when the user says so', () => {
    // A clip ending must never take the user in — ready or not. The only exits are the
    // user (click/key), a whole lap of load failures, and the cap.
    expect(SHELL).not.toMatch(/done\('ended-ready'\)/);
    expect(SHELL).not.toMatch(/done\('error-ready'\)/);
    expect(SHELL).toMatch(/box\.addEventListener\('click', onClick\)/);
    expect(SHELL).toMatch(/window\.addEventListener\('keydown', onKey\)/);
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
    // Named handlers: the keydown one is also what gets removed on exit.
    expect(SHELL).toMatch(/box\.addEventListener\('click', onClick\)/);
    expect(SHELL).toMatch(/window\.addEventListener\('keydown', onKey\)/);
    expect(SHELL).toMatch(/removeEventListener\('keydown', onKey\)/);
  });

  it('caps the wait when the plugin gave no holdMs', () => {
    // 120s — the documented hard cap, matching the backend's MAX_HOLD_MS.
    expect(SHELL).toMatch(/hold > 0 \? hold : 120000/);
  });
});
