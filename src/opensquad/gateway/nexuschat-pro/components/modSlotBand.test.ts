// @vitest-environment jsdom
/**
 * The mod band's contract, per `docs/mods-bridge-m1-plan.md` P1:
 *
 * L1  with no frame for that session, the band adds **zero DOM** — the regression
 *     guard for every user who has no mods enabled;
 * L2  a frame renders through the element whitelist;
 * L3  an element outside the whitelist renders nothing (defence in depth: Python
 *     drops it first, but a hand-crafted frame must not get through either);
 * L4  a Button is rendered inert *and says so* — the press round-trip needs a
 *     gateway→agent command, and a live-looking dead button is worse than none.
 *
 * `React.createElement` on purpose: the vitest include glob is `**\/*.test.ts`.
 */
import { act } from 'react';
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { aiWsService } from '../services/aiWebSocket';
import { ModPaneView } from './ai-chat/ModPaneView';
import { ModSlotHost } from './ai-chat/ModSlotHost';
import {
  clearModSlots,
  getModSlot,
  listModPanes,
  PANE_SCOPE,
  setModSlot,
  type ModNode,
} from './ai-chat/modSlotStore';

const h = React.createElement;

let container: HTMLDivElement;
let root: Root;

const text = (children: string): ModNode => ({ type: 'Text', props: {}, children: [children] });
const box = (children: ModNode[]): ModNode => ({
  type: 'Box',
  props: { flexDirection: 'row', gap: 2 },
  children,
});

beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  clearModSlots('s1');
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const render = (sid: string) =>
  act(async () => {
    root.render(h(ModSlotHost, { sid }));
  });

describe('mod slot band', () => {
  it('L1 renders nothing at all without a frame', async () => {
    await render('s1');
    expect(container.querySelector('[data-mod-slot]')).toBeNull();
    expect(container.textContent).toBe('');
  });

  it('L1 renders nothing when the host has a different sid', async () => {
    act(() => setModSlot('other', 'AbovePrompt', [box([text('not mine')])]));
    await render('s1');
    expect(container.textContent).toBe('');
  });

  it('L2 renders a frame and updates when the tree changes', async () => {
    await render('s1');
    act(() => setModSlot('s1', 'AbovePrompt', [box([text('▶ Replay: 3 edits')])]));
    await render('s1');
    expect(container.querySelector('[data-mod-slot="AbovePrompt"]')).not.toBeNull();
    expect(container.textContent).toContain('▶ Replay: 3 edits');

    act(() => setModSlot('s1', 'AbovePrompt', [box([text('▶ Replay: 4 edits')])]));
    await render('s1');
    expect(container.textContent).toContain('▶ Replay: 4 edits');
  });

  it('L2 an empty tree removes the band again', async () => {
    act(() => setModSlot('s1', 'AbovePrompt', [box([text('hi')])]));
    await render('s1');
    expect(container.textContent).toContain('hi');

    act(() => setModSlot('s1', 'AbovePrompt', []));
    await render('s1');
    expect(container.querySelector('[data-mod-slot]')).toBeNull();
  });

  it('L3 an element outside the whitelist renders nothing', async () => {
    const evil = { type: 'Client', props: { module: './evil.js' } } as unknown as ModNode;
    act(() => setModSlot('s1', 'AbovePrompt', [box([evil, text('safe')])]));
    await render('s1');
    expect(container.textContent).toContain('safe');
    expect(container.innerHTML).not.toContain('evil');
  });

  it('L4 a Button sends its host-minted id back, never a handle', async () => {
    const sent: Array<[string, unknown]> = [];
    const spy = vi.spyOn(aiWsService, 'sendCommand').mockImplementation((cmd: string, data?: unknown) => {
      sent.push([cmd, data]);
    });
    try {
      const button = { type: 'Button', props: { label: 'Ping', action: 'AbovePrompt:1' } } as ModNode;
      act(() => setModSlot('s1', 'AbovePrompt', [box([button])]));
      await render('s1');

      const el = container.querySelector('button') as HTMLButtonElement | null;
      expect(el).not.toBeNull();
      expect(el!.disabled).toBe(false);
      expect(el!.textContent).toBe('Ping');

      act(() => el!.click());
      expect(sent).toEqual([['mod_action', { action: 'AbovePrompt:1', session_id: 's1' }]]);
    } finally {
      spy.mockRestore();
    }
  });

  it('L4 a Button without an action id stays inert', async () => {
    const button = { type: 'Button', props: { label: 'Dead' } } as ModNode;
    act(() => setModSlot('s1', 'AbovePrompt', [box([button])]));
    await render('s1');
    const el = container.querySelector('button') as HTMLButtonElement;
    expect(el.disabled).toBe(true);
  });

  it('L5 panes are keyed by the id the mod asked for', async () => {
    act(() => setModSlot('s1', 'Pane', [box([text('replay view')])], 0, 'replay-theater'));
    act(() => setModSlot('s1', 'Pane', [box([text('button panel')])], 0, 'quick-buttons-setup'));

    expect(listModPanes('s1').sort()).toEqual(['quick-buttons-setup', 'replay-theater']);
    // The other pane's content must not leak into this pane.
    expect(getModSlot('s1', 'Pane', 'replay-theater')?.nodes).toHaveLength(1);
    expect(JSON.stringify(getModSlot('s1', 'Pane', 'quick-buttons-setup')?.nodes)).toContain('button panel');
    expect(getModSlot('s1', 'Pane', 'nope')).toBeUndefined();
    // And rendering one pane does not affect the band.
    await act(async () => {
      root.render(h(ModSlotHost, { sid: 's1', slot: 'Pane', paneId: 'replay-theater' }));
    });
    expect(container.querySelector('[data-mod-slot="Pane"]')).not.toBeNull();
    expect(container.textContent).toContain('replay view');
    expect(container.textContent).not.toContain('button panel');
  });

  it('L6 a mod pane renders without any session — it belongs to the workspace', async () => {
    // Regression lock: the pane shell has no session id, so keying pane frames by
    // session meant `ModPaneView` could never find its content.
    act(() => setModSlot(PANE_SCOPE, 'Pane', [box([text('pane body')])], 0, 'some-pane'));
    await act(async () => {
      root.render(h(ModPaneView, { paneId: 'some-pane' }));
    });
    expect(container.querySelector('[data-mod-slot="Pane"]')).not.toBeNull();
    expect(container.textContent).toContain('pane body');

    // A pane that has no frame stays empty rather than leaking another pane's.
    await act(async () => {
      root.render(h(ModPaneView, { paneId: 'other-pane' }));
    });
    expect(container.textContent).toBe('');
  });

  it('L4 a dropped-count hint is surfaced rather than hidden', async () => {
    act(() => setModSlot('s1', 'AbovePrompt', [box([text('hi')])], 3));
    await render('s1');
    expect(container.textContent).toContain('3 项被白名单拦下');
  });

  it('L7 the downlink reads `content`, where the adapter actually puts the tree', () => {
    // Found by tracing the live path: the adapter's generic relay unwraps the bus
    // envelope `{sid, data}` and `send_response` puts the payload in **content**;
    // the gateway then forwards the frame verbatim (`ws.send_json(message)`), and
    // `aiWebSocket` does not normalise. A handler reading only `msg.data` gets
    // undefined → zero nodes → an empty band, with nothing in any log to say why.
    // (`tests/test_adapter_mod_commands.py::test_a_slot_tree_rides_in_content_not_data`
    // pins the Python half of this contract.)
    const src = readFileSync(path.resolve(__dirname, '../hooks/useAgentWebSocket.ts'), 'utf8');
    for (const topic of ['mod_slot', 'mod_commands']) {
      const start = src.indexOf(`onWs('${topic}'`);
      expect(start, `${topic} must still be subscribed`).toBeGreaterThan(-1);
      const body = src.slice(start, src.indexOf('});', start));
      expect(body, `${topic} must read content ?? data`).toMatch(/\.content\s*\?\?/);
    }
  });
});
