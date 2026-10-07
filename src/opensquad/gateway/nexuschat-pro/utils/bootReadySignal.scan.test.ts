/**
 * The app and the shell must agree on exactly one thing: how the app announces
 * "I am past my startup gate".
 *
 * The inline boot-screen loader in `index.html` cannot import from `App.tsx` (it is
 * not a module), so the two sides are wired through an attribute name that neither
 * compiler checks. If either side renames it the animation keeps rotating until its
 * 120s cap on every cold start — no type error, no failing page, just a wrong screen.
 * This is the guard for that.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const SHELL = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const APP = fs.readFileSync(path.join(ROOT, 'App.tsx'), 'utf8');

describe('the app → shell readiness signal', () => {
  it('is set by the app when it leaves its startup gate', () => {
    expect(APP).toContain("document.documentElement.dataset.opensquadReady = '1'");
  });

  it('is what the shell waits for', () => {
    expect(SHELL).toMatch(/attributeFilter: \['data-opensquad-ready'\]/);
  });

  it('names the same attribute on both sides', () => {
    // dataset.opensquadReady <-> data-opensquad-ready
    const appKey = (APP.match(/dataset\.(opensquad[A-Za-z]*) = '1'/) || [])[1];
    const shellAttr = (SHELL.match(/attributeFilter: \['(data-[a-z-]+)'\]/) || [])[1];
    expect(appKey, 'App.tsx no longer sets a readiness flag for the shell').toBeTruthy();
    expect(shellAttr, 'index.html no longer waits for a readiness attribute').toBeTruthy();

    const expected = 'data-' + appKey!.replace(/([A-Z])/g, '-$1').toLowerCase();
    expect(shellAttr).toBe(expected);
  });
});
