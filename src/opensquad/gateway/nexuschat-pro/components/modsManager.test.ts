// @vitest-environment jsdom
/**
 * Behavioural lock for the Mods page in System Settings.
 *
 * The page's whole value is the honesty of its verdicts, so the assertions are
 * about what it must NOT claim as much as what it shows:
 *
 * L1  the settings rail has a `mods` entry, and `isSettingsAppView('mods')` routes it
 *     into the settings shell (a nav item whose view never reaches the shell is dead);
 * L2  SystemConfigPage wires ModsManagerPage (lazy import + render branch);
 * L3  a mod blocked on `prompt.edit` / `$.settings.read` shows *those names and reasons*;
 * L4  a runnable mod shows no gap rows;
 * L5  the "host not implemented" banner renders — compatibility must not read as
 *     availability, and the enable toggle records intent only;
 * L6  every static `mods.*` key in the page exists in BOTH locales, and the two locale
 *     subtrees have identical key sets.
 *
 * `React.createElement` on purpose: the vitest `include` glob is `**\/*.test.ts`.
 */
import fs from 'node:fs';
import path from 'node:path';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import '../i18n';

const listMock = vi.fn();
const setEnabledMock = vi.fn();
const importMock = vi.fn();
const permsMock = vi.fn();
const setPermsMock = vi.fn();

vi.mock('../services/api', () => ({
  modsAPI: {
    list: (...a: unknown[]) => listMock(...a),
    setEnabled: (...a: unknown[]) => setEnabledMock(...a),
    importMod: (...a: unknown[]) => importMock(...a),
    permissions: (...a: unknown[]) => permsMock(...a),
    setPermissions: (...a: unknown[]) => setPermsMock(...a),
  },
  pluginAPI: {},
  pluginServiceAPI: {},
  adminAPI: {},
}));

import { ModsManagerPage } from './ModsManagerPage';
import { SETTINGS_APP_NAV_ITEMS, isSettingsAppView } from '../utils/appNavItems';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

const HOST = {
  available: false,
  reason: '未找到 Node 运行时——mod 宿主无法启动',
  scope: 'M0：仅 tool.call（可 deny）与最小 $',
  node_runtime: '',
  observed: { running: 0, degraded: 0, stale: 0 },
  last_failure: '',
  observed_at_ts: 0,
};

const MATRIX = {
  source: 'docs/claude-code-mods-compat-v0.md',
  upstream: 'Claude Code mods reference (v2.1.287 line)',
  events: { served: ['tool.call'], degraded: ['ui.render'], refused: ['prompt.edit'] },
  dollar: { served: ['$.store.get'], degraded: ['$.ui.log'], refused: ['$.settings.read'] },
  elements_open: ['Text'],
  elements_refused: ['Client'],
  slots_open: ['AbovePrompt'],
  slots_never: '审批/权限',
};

const blockedMod = {
  name: 'blocked-mod',
  dir_name: 'blocked-mod',
  version: '1.0.0',
  description: 'uses incompatible APIs',
  author: '',
  dir: '/tmp/blocked-mod',
  has_manifest: true,
  has_hooks: true,
  modules: ['hooks/mod.mjs'],
  verdict: 'blocked' as const,
  used_events: ['prompt.edit'],
  used_dollar: ['$.settings.read'],
  blocked_by: [
    { kind: 'event' as const, name: 'prompt.edit', why: '50ms 热路径' },
    { kind: 'dollar' as const, name: '$.settings.read', why: '读配置=读密钥面' },
  ],
  degraded_by: [],
  has_catch: false,
  notes: [],
  state: { enabled: false },
  permissions: { granted: ['fs.write'], domains: [] },
  plan: {
    loadable: true,
    modules: ['hooks/mod.mjs'],
    inert: [{ kind: 'event' as const, name: 'prompt.edit', why: '50ms 热路径' }],
    effect: { kind: 'no_effect' as const, events: [], dollar: [], draws: true },
    reason: '',
  },
};

const runnableMod = {
  ...blockedMod,
  name: 'clean-mod',
  dir_name: 'clean-mod',
  verdict: 'runnable' as const,
  used_events: ['tool.call'],
  used_dollar: ['$.store.get'],
  blocked_by: [],
  plan: {
    loadable: true,
    modules: ['hooks/mod.mjs'],
    inert: [],
    effect: { kind: 'works' as const, events: ['tool.call'], dollar: [], draws: false },
    reason: '',
  },
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  listMock.mockReset();
  setEnabledMock.mockReset();
  importMock.mockReset();
  permsMock.mockReset();
  setPermsMock.mockReset();
  permsMock.mockResolvedValue({
    ok: true,
    capabilities: [{ id: 'fs.write', blurb: '写文件（限 workspace 内）' }],
    mods: {},
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function renderPage() {
  await act(async () => {
    root.render(h(ModsManagerPage, { onBack: () => {} }));
  });
}

describe('mods page', () => {
  it('L1 is a settings view and sits on the rail next to Plugins', () => {
    expect(isSettingsAppView('mods')).toBe(true);
    const entry = SETTINGS_APP_NAV_ITEMS.find((i) => i.view === 'mods');
    expect(entry?.i18nKey).toBe('nav.mods');
    expect(SETTINGS_APP_NAV_ITEMS.map((i) => i.view)).toContain('mods');
  });

  it('L2 SystemConfigPage wires the panel', () => {
    const src = read('components/SystemConfigPage.tsx');
    expect(src).toContain("import('./ModsManagerPage')");
    expect(src).toContain("<ModsManagerPage onBack={backFromApp} />");
    expect(src).toContain("activeAppView === 'mods'");
  });

  it('L3 a blocked mod names each gap and its reason', async () => {
    listMock.mockResolvedValue({ ok: true, root: '/tmp/mods', mods: [blockedMod], counts: {}, matrix: MATRIX, host: HOST });
    await renderPage();

    const text = container.textContent || '';
    expect(text).toContain('blocked-mod');
    expect(text).toContain('prompt.edit');
    expect(text).toContain('50ms 热路径');
    expect(text).toContain('$.settings.read');
    expect(text).toContain('读配置=读密钥面');
  });

  it('L4 a runnable mod shows no gap rows', async () => {
    listMock.mockResolvedValue({
      ok: true,
      root: '/tmp/mods',
      mods: [runnableMod],
      counts: {},
      matrix: MATRIX,
      host: HOST,
    });
    await renderPage();

    const text = container.textContent || '';
    expect(text).toContain('clean-mod');
    expect(text).not.toContain('await next');
    // No refusal reason may leak into a compatible mod's card.
    expect(text).not.toContain('50ms 热路径');
    expect(text).not.toContain('读配置=读密钥面');
  });

  it('L5 the host-unavailable banner renders (compatibility != availability)', async () => {
    listMock.mockResolvedValue({ ok: true, root: '/tmp/mods', mods: [runnableMod], counts: {}, matrix: MATRIX, host: HOST });
    await renderPage();

    const text = container.textContent || '';
    expect(text).toContain(HOST.reason);
  });

  it('L10 the page answers "does it do anything?" per mod', async () => {
    listMock.mockResolvedValue({
      ok: true,
      root: '/tmp/mods',
      mods: [blockedMod, runnableMod],
      counts: {},
      matrix: MATRIX,
      host: HOST,
    });
    await renderPage();

    const text = container.textContent || '';
    // "loaded" must not be presented as "works": the two mods differ.
    expect(text).toContain('实际效果');
    expect(text).toContain('它挂的事件本机都不触发');
    expect(text).toContain('生效：tool.call');
  });

  it('L11 the gated capabilities are shown with their backend meaning', async () => {
    listMock.mockResolvedValue({
      ok: true,
      root: '/tmp/mods',
      mods: [runnableMod],
      counts: {},
      matrix: MATRIX,
      host: HOST,
    });
    await renderPage();
    await act(async () => {
      (container.querySelector('button[aria-label="details"]') as HTMLButtonElement).click();
    });

    const text = container.textContent || '';
    expect(text).toContain('信任与授权');
    expect(text).toContain('$.fs.write');
    // The blurb comes from the API, so the page cannot drift from the gates.
    expect(text).toContain('写文件（限 workspace 内）');
    expect(text).toContain('不是沙箱');
  });

  it('L8 a degraded host is shown, because fail-open must never be silent', async () => {
    listMock.mockResolvedValue({
      ok: true,
      root: '/tmp/mods',
      mods: [runnableMod],
      counts: {},
      matrix: MATRIX,
      host: {
        ...HOST,
        available: true,
        node_runtime: 'C:/node/node.exe',
        observed: { running: 0, degraded: 1, stale: 0 },
        last_failure: 'tool.call: mod host exited',
      },
    });
    await renderPage();

    const text = container.textContent || '';
    expect(text).toContain('tool.call: mod host exited');
    expect(text).toContain('fail-open');
  });

  it('L9 a per-contribution mod shows what stays inert', async () => {
    listMock.mockResolvedValue({
      ok: true,
      root: '/tmp/mods',
      mods: [blockedMod],
      counts: {},
      matrix: MATRIX,
      host: HOST,
    });
    await renderPage();

    const text = container.textContent || '';
    expect(text).toContain('1 项惰性');
  });

  it('L7 the experimental notice is always visible and states what is wired', async () => {
    // The banner must not be conditional on host state: a "runnable" verdict
    // still means "nothing is wired except tool.call" today.
    listMock.mockResolvedValue({
      ok: true,
      root: '/tmp/mods',
      mods: [runnableMod],
      counts: {},
      matrix: MATRIX,
      host: { ...HOST, available: true, node_runtime: 'C:/node/node.exe' },
    });
    await renderPage();

    const text = container.textContent || '';
    expect(text).toContain('实验性功能');
    expect(text).toContain(HOST.scope);
    // Availability must not hide the warning.
    expect(text).not.toContain(HOST.reason);
  });

  it('L6 every mods.* key resolves in both locales, with identical key sets', () => {
    const src = read('components/ModsManagerPage.tsx');
    const used = new Set<string>();
    for (const m of src.matchAll(/t\(\s*'mods\.([A-Za-z0-9_.]+)'/g)) {
      used.add(m[1]);
    }
    // Keys built from a verdict are dynamic; list the families explicitly.
    expect(src).toContain('t(`mods.verdict.${mod.verdict}`)');
    expect(src).toContain('t(`mods.verdictHint.${mod.verdict}`)');
    for (const v of ['runnable', 'partial', 'blocked', 'unknown']) {
      used.add(`verdict.${v}`);
      used.add(`verdictHint.${v}`);
    }

    const flat = (obj: Record<string, unknown>, prefix = ''): string[] => {
      const out: string[] = [];
      for (const [k, v] of Object.entries(obj)) {
        const key = prefix ? `${prefix}.${k}` : k;
        if (v && typeof v === 'object' && !Array.isArray(v)) out.push(...flat(v as Record<string, unknown>, key));
        else out.push(key);
      }
      return out;
    };

    const zh = JSON.parse(read('locales/zh.json'));
    const en = JSON.parse(read('locales/en.json'));
    expect(flat(zh.mods).sort()).toEqual(flat(en.mods).sort());

    const zhKeys = new Set(flat(zh.mods));
    const missing = [...used].filter((k) => !zhKeys.has(k) || !new Set(flat(en.mods)).has(k));
    expect(missing).toEqual([]);
  });

  it('L7 the banner states no host facts of its own — those numbers drift', () => {
    // The copy used to hardcode "three events / 14 $ members / render slots: 0"
    // and stayed that way while all three moved on. The running host is the
    // single source: `host.scope` is rendered right beside this text.
    const en = JSON.parse(read('locales/en.json')).mods;
    const zh = JSON.parse(read('locales/zh.json')).mods;
    for (const body of [en.experimentalBody, zh.experimentalBody] as string[]) {
      expect(body).not.toMatch(/\d/);
      expect(body).not.toMatch(/three|three events|三个事件/);
    }
    expect(en.experimentalBody).toMatch(/matrix/i);
    expect(zh.experimentalBody).toMatch(/矩阵/);

    const src = read('components/ModsManagerPage.tsx');
    expect(src).toContain('host?.scope'); // the facts come from the backend, not the prose
  });

  it('L8 importing asks once, and says what importing means', () => {
    const src = read('components/ModsManagerPage.tsx');
    const start = src.indexOf('const handleImport');
    const body = src.slice(start, start + 900);
    expect(body).toContain("window.confirm(t('mods.importConfirm'))");

    const en = JSON.parse(read('locales/en.json')).mods.importConfirm as string;
    const zh = JSON.parse(read('locales/zh.json')).mods.importConfirm as string;
    expect(en).toMatch(/arbitrary code/i);
    expect(en).not.toEqual(zh);
  });
});
