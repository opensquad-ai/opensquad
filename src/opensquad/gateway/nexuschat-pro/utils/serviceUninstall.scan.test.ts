// @vitest-environment node
/**
 * Service Manager uninstall — the UI half of a contract the backend cannot see.
 *
 * The Service Manager lists services from `GET /admin/services`, which now
 * carries a `builtin` flag resolved by the launcher from
 * `builtin_plugins.json` (see `tests/test_services_manage_uninstall.py`).
 * "Uninstall service" is really "uninstall the owning plugin":
 * `DELETE /admin/plugins/{id}`, which rejects built-ins with HTTP 400.
 *
 * Three things can rot here without any runtime error:
 *   - the flag stops being declared in the `ServiceStatus` mirror, so the UI
 *     reads `undefined` and `!undefined` renders an enabled button for
 *     `websearch` — a button whose only possible outcome is an error dialog;
 *   - the uninstall action starts firing without stopping the service first,
 *     leaving a live child holding its port with its directory deleted;
 *   - the dialog copy loses its zh/en pair (this page reuses the
 *     `pluginManager.*` namespace, where a missing key renders as the key name).
 *
 *   R1  `ServiceStatus` declares the `builtin` flag
 *   R2  the page forwards it as `canUninstall={!svc.builtin}` and both layouts
 *       disable their button from it
 *   R3  `confirmUninstall` stops the service BEFORE uninstalling the plugin
 *   R4  every key this feature reads exists in both locales, and the two
 *       `pluginManager` namespaces stay key-for-key identical
 *
 * Mutations verified (each one makes this file fail):
 *   MS1 `canUninstall={!svc.builtin}` → `canUninstall={true}`              → R2
 *   MS2 drop `builtin?: boolean` from ServiceStatus                        → R1
 *   MS3 swap the stop/uninstall calls in confirmUninstall                  → R3
 *   MS4 delete `uninstallServiceWarning` from locales/en.json              → R4
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const PAGE = read('components/ServiceManagerPage.tsx');
const API = read('services/api.ts');
const zh = JSON.parse(read('locales/zh.json'));
const en = JSON.parse(read('locales/en.json'));

/** Body of a top-level `interface <name> { ... }` in a .ts file. */
function interfaceBody(src: string, name: string): string {
  const start = src.indexOf(`export interface ${name} {`);
  expect(start, `${name} interface not found`).toBeGreaterThan(-1);
  const end = src.indexOf('\n}', start);
  return src.slice(start, end);
}

describe('Service Manager uninstall', () => {
  it('R1 — ServiceStatus mirrors the launcher protection flag', () => {
    const body = interfaceBody(API, 'ServiceStatus');
    expect(body).toMatch(/builtin\?:\s*boolean/);
  });

  it('R2 — the page gates the uninstall action on that flag', () => {
    // The hand-off from payload to prop, verbatim: a truthiness flip here is
    // what would re-enable uninstall for built-ins.
    expect(PAGE).toContain('canUninstall={!svc.builtin}');

    // Both layouts (list row + grid card) must disable from the prop rather
    // than each deciding on its own.
    const disabled = PAGE.match(/disabled=\{!canUninstall\}/g) || [];
    expect(disabled).toHaveLength(2);

    // And the disabled state has to be visible, not just functionally inert.
    expect(PAGE).toMatch(/canUninstall[\s\S]{0,200}cursor-not-allowed/);
  });

  it('R3 — the service is stopped before its plugin is uninstalled', () => {
    const body = PAGE.slice(
      PAGE.indexOf('const confirmUninstall'),
      PAGE.indexOf('const aliveCount'),
    );
    expect(body.length, 'confirmUninstall not found').toBeGreaterThan(0);

    const stopAt = body.indexOf('pluginServiceAPI.stop(');
    const uninstallAt = body.indexOf('pluginAPI.uninstall(');
    expect(stopAt, 'confirmUninstall no longer stops the service').toBeGreaterThan(-1);
    expect(uninstallAt, 'confirmUninstall no longer uninstalls the plugin').toBeGreaterThan(-1);
    expect(stopAt).toBeLessThan(uninstallAt);

    // The stop is best-effort (a never-started service is not an error), the
    // uninstall is not: swallowing its failure would report success on HTTP 400.
    expect(body).toMatch(/catch\s*\{\s*\/\/[^}]*\n\s*\}/);
  });

  it('R4 — uninstall copy exists in both locales', () => {
    const keys = [
      'uninstallServiceTitle',
      'uninstallServiceWarning',
      'uninstallServiceStopsHint',
      'uninstallShort',
      // reused from the shared namespace, so they must not be renamed away
      'uninstallIrreversible',
      'uninstalling',
      'confirmUninstall',
      'uninstallFailedMsg',
      'uninstallNeedsRestart',
      'cannotUninstallBundled',
    ];
    for (const k of keys) {
      expect(zh.pluginManager[k], `zh.pluginManager.${k} missing`).toBeTruthy();
      expect(en.pluginManager[k], `en.pluginManager.${k} missing`).toBeTruthy();
    }

    // The namespaces are shared with the plugin page; a key added to one file
    // only means one language silently renders the raw key.
    expect(Object.keys(zh.pluginManager).sort()).toEqual(Object.keys(en.pluginManager).sort());
  });
});
