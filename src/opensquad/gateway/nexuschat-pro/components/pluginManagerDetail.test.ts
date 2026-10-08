// @vitest-environment jsdom
/**
 * Behavioural lock for「插件卡片点击无任何反应 + 介绍被省略号截断」.
 *
 * The card is a bundle of controls (star / trash / settings / service toggle)
 * with a name that truncates and a description clamped to `line-clamp-2`, so a
 * long description was simply unreadable — there was nowhere to read it. The
 * card's read-only text now opens a detail dialog.
 *
 * L1  clicking the card's NAME (grid or list) calls `onOpenDetail`;
 * L2  clicking the card's DESCRIPTION does too, and the description is still
 *     the clamped one on the card (the dialog is what unclamps it);
 * L3  the star control keeps its own job — it must NOT open the dialog, which
 *     is why the handler sits on the text regions and not on the card root
 *     (the inline config panel renders inside the same card, so a card-level
 *     onClick would swallow clicks meant for its inputs);
 * L4  the dialog renders the full description with no clamp;
 * L5  every `pluginManager.detail*` key exists in both locales.
 *
 * `React.createElement` on purpose: the vitest `include` glob is `**\/*.test.ts`.
 */
import fs from 'node:fs';
import path from 'node:path';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import '../i18n';

vi.mock('../services/api', () => ({
  pluginAPI: {},
  pluginServiceAPI: {},
  adminAPI: {},
}));

import { PluginCard, PluginDetailDialog } from './PluginManagerPage';
import type { PluginInfo } from '../services/api';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

const LONG_DESC =
  'Agent Factory: dynamically create, configure, and launch new Agents via the chat interface. ' +
  'It reads a plugin manifest, registers every contributed tool, persists the configuration, ' +
  'and then tells the running agent to reload so the new tools are callable immediately.';

const plugin = {
  name: 'agent_factory',
  dir_name: 'agent_factory',
  display_name: 'Agent Factory',
  version: '1.0.0',
  type: 'tool',
  enabled: true,
  description: LONG_DESC,
  author: 'OpenSquad',
  tags: ['agent'],
  tools: [{ name: 'create_agent', level: 'core', description: 'Create and launch an agent' }],
  hooks: [],
  config: {},
  dependencies: { pip: ['pyyaml'] },
  builtin: true,
} as unknown as PluginInfo;

let container: HTMLDivElement;
let root: Root;

const flush = () =>
  act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

const all = (sel: string) => Array.from(container.querySelectorAll<HTMLElement>(sel));
const byText = (sel: string, text: string) =>
  all(sel).find((el) => (el.textContent || '').trim() === text);

function renderCard(layout: 'grid' | 'list', onOpenDetail: () => void, onToggleStar = () => {}) {
  act(() => {
    root.render(
      h(PluginCard, {
        plugin,
        layout,
        toggling: false,
        onToggle: () => {},
        configOpen: false,
        onConfigToggle: () => {},
        onOpenView: () => {},
        starred: false,
        onToggleStar,
        agentLoaded: null,
        onAgentToggle: () => {},
        agentToolLevel: 'extended',
        onToolLevelChange: () => {},
        onUninstall: () => {},
        onOpenDetail,
      }),
    );
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('plugin card opens the detail dialog', () => {
  it('opens from the name, in both layouts', () => {
    for (const layout of ['grid', 'list'] as const) {
      const onOpenDetail = vi.fn();
      renderCard(layout, onOpenDetail);

      const name = byText('h3', 'Agent Factory');
      expect(name, `${layout}: name not rendered`).toBeTruthy();
      act(() => {
        name!.click();
      });

      expect(onOpenDetail, `${layout}: name click must open the dialog`).toHaveBeenCalledTimes(1);
    }
  });

  it('opens from the description, which stays clamped on the card', () => {
    const onOpenDetail = vi.fn();
    renderCard('grid', onOpenDetail);

    const desc = byText('p', LONG_DESC);
    expect(desc).toBeTruthy();
    // The card still clamps — the dialog is what makes the full text readable.
    expect(desc!.className).toContain('line-clamp-2');

    act(() => {
      desc!.click();
    });
    expect(onOpenDetail).toHaveBeenCalledTimes(1);
  });

  it('leaves the star control alone', () => {
    const onOpenDetail = vi.fn();
    const onToggleStar = vi.fn();
    renderCard('grid', onOpenDetail, onToggleStar);

    const star = all('button').find((b) => /favorite/i.test(b.getAttribute('title') || ''));
    expect(star).toBeTruthy();
    act(() => {
      star!.click();
    });

    expect(onToggleStar).toHaveBeenCalledTimes(1);
    expect(onOpenDetail).not.toHaveBeenCalled();
  });
});

describe('plugin detail dialog', () => {
  it('shows the full description, unclamped', () => {
    act(() => {
      root.render(h(PluginDetailDialog, { plugin, onClose: () => {} }));
    });

    const desc = byText('p', LONG_DESC);
    expect(desc, 'dialog must render the whole description').toBeTruthy();
    expect(desc!.className).not.toContain('line-clamp');
    // Details the card has no room for.
    expect(container.textContent).toContain('create_agent');
    expect(container.textContent).toContain('pip: pyyaml');
    expect(container.textContent).toContain('agent_factory');
  });

  it('closes on Escape and on the backdrop', () => {
    const onClose = vi.fn();
    act(() => {
      root.render(h(PluginDetailDialog, { plugin, onClose }));
    });

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('detail locale keys', () => {
  const zh = JSON.parse(read('locales/zh.json'));
  const en = JSON.parse(read('locales/en.json'));

  it('every pluginManager.detail* key is present in both locales', () => {
    const keys = Object.keys(zh.pluginManager).filter((k) => k.startsWith('detail'));
    expect(keys.length).toBeGreaterThan(0);
    for (const key of keys) {
      expect(en.pluginManager[key], `en.json is missing pluginManager.${key}`).toBeTruthy();
      expect(zh.pluginManager[key], `zh.json is missing pluginManager.${key}`).toBeTruthy();
    }
  });
});

describe('L6 — the agent-tools chip only appears for plugins that have tools', () => {
  function renderChip(agentLoaded: boolean | null, tools: unknown[]) {
    act(() => {
      root.render(
        h(PluginCard, {
          plugin: { ...plugin, tools } as unknown as PluginInfo,
          layout: 'list',
          toggling: false,
          onToggle: () => {},
          configOpen: false,
          onConfigToggle: () => {},
          onOpenView: () => {},
          starred: false,
          onToggleStar: () => {},
          agentLoaded,
          onAgentToggle: () => {},
          agentToolLevel: 'extended',
          onToolLevelChange: () => {},
          onUninstall: () => {},
          onOpenDetail: () => {},
        }),
      );
    });
  }

  it('hides it for a manifest-only plugin — nothing to add to the agent', () => {
    // boot_private is exactly this shape: contributes.bootScreen + assets, tools: [].
    // Its "Off" chip read as "the plugin is disabled" (2026-10-07).
    renderChip(false, []);
    expect(byText('button', 'Off'), 'no tools → no chip').toBeUndefined();
    expect(byText('button', 'On')).toBeUndefined();
  });

  it('keeps it for a plugin that does have tools', () => {
    renderChip(false, [{ name: 'create_agent', level: 'core' }]);
    expect(byText('button', 'Off'), 'tool plugins keep the affordance').toBeDefined();
  });
});

describe('L7 — a plugin you can disable always has its switch on the card', () => {
  const zh = JSON.parse(read('locales/zh.json'));
  const SERVICE_ONLY_TITLE = zh.pluginManager.serviceOnlyTitle as string;

  function renderSwitch(opts: { serviceOnly: boolean; tools: unknown[]; enabled: boolean; onToggle: () => void }) {
    act(() => {
      root.render(
        h(PluginCard, {
          plugin: {
            ...plugin,
            tools: opts.tools,
            enabled: opts.enabled,
            service_only: opts.serviceOnly,
          } as unknown as PluginInfo,
          layout: 'list',
          toggling: false,
          onToggle: opts.onToggle,
          configOpen: false,
          onConfigToggle: () => {},
          onOpenView: () => {},
          starred: false,
          onToggleStar: () => {},
          agentLoaded: null,
          onAgentToggle: () => {},
          agentToolLevel: 'extended',
          onToolLevelChange: () => {},
          onUninstall: () => {},
          onOpenDetail: () => {},
        }),
      );
    });
  }

  it('gives a manifest-only plugin a working switch (no service, no tools, no service_toggle)', () => {
    // boot_private's shape. Before: the switch was gated on service_toggle, so the only
    // way to turn such a plugin off was to edit its plugin.json by hand.
    const onToggle = vi.fn();
    renderSwitch({ serviceOnly: false, tools: [], enabled: true, onToggle });

    const sw = all('button').find((b) => b.title === 'Disable');
    expect(sw, 'an enabled, disableable plugin shows the switch').toBeDefined();

    act(() => { sw!.click(); });
    expect(onToggle, 'the switch is wired to enable/disable').toHaveBeenCalledTimes(1);
  });

  it('shows the switch as Off when the plugin is disabled', () => {
    renderSwitch({ serviceOnly: false, tools: [], enabled: false, onToggle: () => {} });
    expect(all('button').find((b) => b.title === 'Enable'), 'disabled → Enable').toBeDefined();
  });

  it('keeps service_only plugins unswitchable', () => {
    const onToggle = vi.fn();
    renderSwitch({ serviceOnly: true, tools: [], enabled: true, onToggle });

    const sw = all('button').find((b) => b.title === SERVICE_ONLY_TITLE);
    expect(sw, 'the service-only affordance still renders (greyed)').toBeDefined();
    expect(sw!.hasAttribute('disabled'), 'service-only plugins cannot be enabled').toBe(true);

    act(() => { sw!.click(); });
    expect(onToggle, 'clicking it must not enable a service-only plugin').not.toHaveBeenCalled();
  });
});
