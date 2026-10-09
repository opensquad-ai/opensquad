// @vitest-environment jsdom
/**
 * Behavioural lock for「插件停用后，它在系统设置里的入口必须一起消失」.
 *
 * The Mods rail entry is the mods_host plugin's own surface — with the plugin off
 * there is no host behind the page. It used to be a hardcoded entry that stayed on
 * the rail no matter what the plugin's toggle said (the user's report: turned it
 * off, entry still there).
 *
 * L1  the `mods` entry declares the plugin it belongs to (a hardcoded entry with no
 *     gate is exactly the bug);
 * L2  the filter drops it when that plugin is disabled *or* gone, keeps it while
 *     enabled, and keeps everything when the plugin list is not known yet — an
 *     unreachable launcher must not make a working page unreachable;
 * L3  `usePluginEnabled` reads the same endpoint the Plugin Manager toggles, is
 *     `null` until the read lands, and a *failed* read keeps the last known map
 *     instead of reporting every plugin as off (which would hide live entries);
 * L4  SystemConfigPage builds its rail through the filter — asserting the constant
 *     alone would pass while the rail still renders the unfiltered list;
 * L5  the Plugin Manager announces its toggles, so the entry leaves the rail in the
 *     same session rather than after a reload.
 *
 * `React.createElement` on purpose: the vitest `include` glob is `**\/*.test.ts`.
 */
import fs from 'node:fs';
import path from 'node:path';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getPluginsMock = vi.fn();

vi.mock('../services/api', () => ({
  pluginAPI: { getPlugins: (...a: unknown[]) => getPluginsMock(...a) },
  pluginServiceAPI: {},
  adminAPI: {},
}));

import { SETTINGS_APP_NAV_ITEMS, visibleAppNavItems } from './appNavItems';
import { PLUGINS_CHANGED_EVENT, usePluginEnabled } from './usePluginEnabled';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

const views = (items: Array<{ view: string }>) => items.map((i) => i.view);

const plugin = (name: string, enabled: boolean) => ({ name, enabled }) as never;

describe('settings rail gating', () => {
  it('L1 the mods entry is gated on the plugin that serves it', () => {
    const entry = SETTINGS_APP_NAV_ITEMS.find((i) => i.view === 'mods');
    expect(entry?.requiresPlugin).toBe('mods_host');
    // Every other static entry has no gate: the filter must not touch them.
    expect(
      SETTINGS_APP_NAV_ITEMS.filter((i) => i.requiresPlugin).map((i) => i.view),
    ).toEqual(['mods']);
  });

  it('L2 the filter follows the plugin, and only when it knows', () => {
    const all = views(SETTINGS_APP_NAV_ITEMS);
    expect(all).toContain('mods');

    // Disabled → gone; every other entry survives.
    expect(views(visibleAppNavItems(SETTINGS_APP_NAV_ITEMS, { mods_host: false }))).toEqual(
      all.filter((v) => v !== 'mods'),
    );
    // Not installed → gone as well.
    expect(views(visibleAppNavItems(SETTINGS_APP_NAV_ITEMS, { boot_private: true }))).toEqual(
      all.filter((v) => v !== 'mods'),
    );
    // Enabled → kept, in place.
    expect(views(visibleAppNavItems(SETTINGS_APP_NAV_ITEMS, { mods_host: true }))).toEqual(all);
    // Unknown (launcher has not answered) → kept: never hide what we cannot judge.
    expect(visibleAppNavItems(SETTINGS_APP_NAV_ITEMS, null)).toEqual(SETTINGS_APP_NAV_ITEMS);
  });

  it('L4 the rail renders the filtered list, not the constant', () => {
    const src = read('components/SystemConfigPage.tsx');
    expect(src).toContain('visibleAppNavItems(SETTINGS_APP_NAV_ITEMS, pluginEnabled)');
    // The unfiltered constant must not reach the rail any more.
    expect(src).not.toContain('...SETTINGS_APP_NAV_ITEMS.map');
  });

  it('L5 the plugin manager announces its toggles', () => {
    const src = read('components/PluginManagerPage.tsx');
    const start = src.indexOf('const togglePlugin');
    const body = src.slice(start, start + 700);
    expect(body).toContain('notifyPluginsChanged()');
  });
});

describe('usePluginEnabled', () => {
  let container: HTMLDivElement;
  let root: Root;
  const seen: Array<Record<string, boolean> | null> = [];

  function Probe() {
    seen.push(usePluginEnabled());
    return null;
  }

  const latest = () => seen[seen.length - 1];

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    getPluginsMock.mockReset();
    seen.length = 0;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function mount() {
    await act(async () => {
      root.render(h(Probe));
    });
  }

  it('L3 reports per-name state, and starts unknown', async () => {
    let resolveRead: (v: unknown) => void = () => {};
    getPluginsMock.mockReturnValue(new Promise((r) => (resolveRead = r)));
    await mount();
    // Nothing has answered yet — "unknown", not "all off".
    expect(latest()).toBeNull();

    await act(async () => {
      resolveRead({ plugins: [plugin('mods_host', false), plugin('boot_private', true)] });
    });
    expect(latest()).toEqual({ mods_host: false, boot_private: true });
  });

  it('L3 a failed read keeps the last known map', async () => {
    getPluginsMock.mockResolvedValue({ plugins: [plugin('mods_host', false)] });
    await mount();
    expect(latest()).toEqual({ mods_host: false });

    getPluginsMock.mockRejectedValue(new Error('launcher unreachable'));
    await act(async () => {
      window.dispatchEvent(new Event(PLUGINS_CHANGED_EVENT));
    });
    // Not `{}` (which would claim mods_host is installed-and-off by accident) and
    // not "everything enabled" either — the last answer stands.
    expect(latest()).toEqual({ mods_host: false });
  });

  it('L3 re-reads on the change broadcast', async () => {
    getPluginsMock.mockResolvedValue({ plugins: [plugin('mods_host', true)] });
    await mount();
    expect(latest()).toEqual({ mods_host: true });

    getPluginsMock.mockResolvedValue({ plugins: [plugin('mods_host', false)] });
    await act(async () => {
      window.dispatchEvent(new Event(PLUGINS_CHANGED_EVENT));
    });
    expect(latest()).toEqual({ mods_host: false });
    expect(views(visibleAppNavItems(SETTINGS_APP_NAV_ITEMS, latest()))).not.toContain('mods');
  });
});
