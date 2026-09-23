// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import {
  HtmlEmbedBlock,
  collectHtmlEmbedsFromEvents,
  collectHtmlEmbedsPrecedingMessage,
  extractHtmlEmbed,
  indexHtmlEmbedsByAssistantMessage,
  isVisualizationToolName,
} from './HtmlEmbedBlock';
import type { TimelineEntry, WorkflowEvent } from '../../utils/aiChatTimeline';

describe('isVisualizationToolName', () => {
  it('matches visualization tool names', () => {
    expect(isVisualizationToolName('visualization')).toBe(true);
    expect(isVisualizationToolName('visualization.create')).toBe(true);
    expect(isVisualizationToolName('Visualization.Create')).toBe(true);
    expect(isVisualizationToolName('web_search')).toBe(false);
  });
});

describe('extractHtmlEmbed', () => {
  it('prefers result.kind html_embed', () => {
    const embed = extractHtmlEmbed(
      'visualization.create',
      { html: '<p>args</p>', title: 'Args' },
      JSON.stringify({
        ok: true,
        kind: 'html_embed',
        html: '<p>result</p>',
        title: 'Result',
        id: 'abc',
        height: 400,
      }),
    );
    expect(embed?.html).toBe('<p>result</p>');
    expect(embed?.title).toBe('Result');
    expect(embed?.id).toBe('abc');
  });

  it('falls back to args.html for visualization tools', () => {
    const embed = extractHtmlEmbed('visualization.create', { html: '<h1>Hi</h1>', title: 'Hi' }, 'ok');
    expect(embed?.html).toBe('<h1>Hi</h1>');
    expect(embed?.title).toBe('Hi');
  });
});

describe('AIChatPage wiring: embeds render in both UI modes', () => {
  const PAGE = fs.readFileSync(path.join(process.cwd(), 'components/AIChatPage.tsx'), 'utf8');

  it('builds the embed index for solo mode too, at every render site', () => {
    // Solo used to skip the index (`isSolo ? null : …`) and both render sites
    // carried a `!isSolo &&` guard, so an agent-created table was invisible to
    // anyone in document-stream mode — with no console error to notice.
    expect(PAGE).not.toMatch(/isSolo\s*\?\s*null\s*:\s*indexHtmlEmbedsByAssistantMessage/);
    expect(PAGE).not.toMatch(/!isSolo\s*&&\s*entry\.data\.role\s*===\s*'assistant'/);
    expect(PAGE).not.toMatch(/!isSolo\s*&&\s*nested\.data\.role\s*===\s*'assistant'/);
    // Main timeline + task_fold inner loop.
    expect(PAGE.match(/indexHtmlEmbedsByAssistantMessage\(/g)?.length).toBe(2);
  });
});

/**
 * Form bridge contract (source-level, same technique as codeWellTheme.test.ts):
 * embedded pages must be able to hand collected form data back to the agent.
 */
describe('html embed form bridge', () => {
  const SRC = fs.readFileSync(path.join(process.cwd(), 'components/ai-chat/HtmlEmbedBlock.tsx'), 'utf8');
  const PLUGIN = fs.readFileSync(
    path.join(process.cwd(), '../../../plugins/visualization/plugin.py'),
    'utf8',
  );

  it('iframe listens for os_form_submit from its own contentWindow only', () => {
    expect(SRC).toContain("'os_form_submit'");
    expect(SRC).toMatch(/event\.source\s*!==\s*iframeRef\.current\.contentWindow/);
    expect(SRC).toMatch(/onFormSubmit\?\s*:/);
  });

  it('sandbox keeps allow-forms + allow-scripts so forms actually run', () => {
    expect(SRC).toContain('sandbox="allow-scripts allow-forms allow-modals"');
  });

  it('the form contract reaches the agent through the system prompt, not dead tool prose', () => {
    // Native FC ships only the docstring's first line (96-char budget), so the
    // tool description can hold no more than a hint. The full contract — and
    // how to recognise the returned message — lives in the prompt part.
    // test_visualization_form_protocol.py locks the delivered side.
    expect(PLUGIN).toContain('os_form_submit');
    expect(PLUGIN).toContain('window.parent.postMessage');
    const part = fs.readFileSync(
      path.join(process.cwd(), '../../../prompts/parts/common_2.26_agent_web_interactive_html.md'),
      'utf8',
    );
    expect(part).toContain("type: 'os_form_submit'");
    expect(part).toContain('[Form submission]');
    expect(part).toContain('[表单提交]');
  });
});

/**
 * Runtime lock for two gaps found after the first pass:
 *  - the ✓ only rendered in the `chrome` header, while the chat embeds are
 *    `seamless` (no header) → a submit was visually silent;
 *  - the comment promised double-submit suppression that did not exist.
 * Rendered through react-dom directly (this repo ships no testing-library).
 */
describe('html embed form bridge — runtime behaviour', () => {
  let container: HTMLDivElement | null = null;
  let root: Root | null = null;

  beforeEach(() => {
    (globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(() => {
    if (root) act(() => root!.unmount());
    root = null;
    container?.remove();
    container = null;
  });

  /**
   * jsdom leaves srcdoc iframes without a usable contentWindow, so pin a
   * sentinel `contentWindow` on the node the ref points at — that is exactly
   * the identity the bridge compares `event.source` against.
   */
  function mountEmbed(
    onFormSubmit: (payload: unknown, embedTitle: string) => void,
    variant: 'chrome' | 'seamless',
  ): Window {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root!.render(
        React.createElement(HtmlEmbedBlock, {
          payload: { kind: 'html_embed', html: '<form><button>submit</button></form>', title: 'IMAP config' },
          variant,
          onFormSubmit,
        }),
      );
    });
    const iframe = container.querySelector('iframe') as HTMLIFrameElement | null;
    expect(iframe, 'embed iframe missing').not.toBeNull();
    const ownWindow = {} as Window;
    Object.defineProperty(iframe, 'contentWindow', { value: ownWindow, configurable: true });
    return ownWindow;
  }

  function post(source: unknown, payload: unknown) {
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', { data: { type: 'os_form_submit', payload }, source: source as Window }),
      );
    });
  }

  const badge = () => container!.querySelector('[data-html-embed-submitted]');

  it('seamless — submitting shows ✓ even though this variant has no header', () => {
    const onFormSubmit = vi.fn();
    const ownWindow = mountEmbed(onFormSubmit, 'seamless');
    expect(badge()).toBeNull();

    post(ownWindow, { imap_server: 'imap.example.com', api_key: 'secret' });

    expect(onFormSubmit).toHaveBeenCalledTimes(1);
    expect(onFormSubmit).toHaveBeenCalledWith(
      { imap_server: 'imap.example.com', api_key: 'secret' },
      'IMAP config',
    );
    expect(badge()?.textContent).toBe('✓');
  });

  it('chrome — the header acknowledgement still renders (string payloads too)', () => {
    const onFormSubmit = vi.fn();
    const ownWindow = mountEmbed(onFormSubmit, 'chrome');

    post(ownWindow, 'raw text');

    expect(onFormSubmit).toHaveBeenCalledWith('raw text', 'IMAP config');
    expect(badge()?.textContent).toBe('✓');
  });

  it('ignores a submit from a foreign window (parallel cards stay isolated)', () => {
    const onFormSubmit = vi.fn();
    mountEmbed(onFormSubmit, 'seamless');

    post(window, { api_key: 'not-mine' });

    expect(onFormSubmit).not.toHaveBeenCalled();
    expect(badge()).toBeNull();
  });

  it('drops a burst repeat but still lets the user resubmit after fixing a field', () => {
    const onFormSubmit = vi.fn();
    const ownWindow = mountEmbed(onFormSubmit, 'seamless');
    const now = vi.spyOn(Date, 'now');
    try {
      now.mockReturnValue(1_000_000);
      post(ownWindow, { api_key: 'a' });
      post(ownWindow, { api_key: 'a' }); // double-click / double-fire
      expect(onFormSubmit).toHaveBeenCalledTimes(1);

      now.mockReturnValue(1_000_000 + 1_500);
      post(ownWindow, { api_key: 'b' });
      expect(onFormSubmit).toHaveBeenCalledTimes(2);
      expect(onFormSubmit).toHaveBeenLastCalledWith({ api_key: 'b' }, 'IMAP config');
    } finally {
      now.mockRestore();
    }
  });

  it('the ✓ flash auto-hides instead of overlaying the embed forever', async () => {
    vi.useFakeTimers();
    try {
      const ownWindow = mountEmbed(vi.fn(), 'seamless');
      post(ownWindow, { api_key: 'x' });
      expect(badge()).not.toBeNull();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });

      expect(badge()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('collectHtmlEmbedsFromEvents', () => {
  it('collects embeds from tool_call events with results', () => {
    const events: WorkflowEvent[] = [
      {
        type: 'tool_call',
        content: {
          id: '1',
          name: 'visualization.create',
          arguments: { html: '<p>a</p>', title: 'A' },
        },
        timestamp: 1,
        result: JSON.stringify({
          ok: true,
          kind: 'html_embed',
          html: '<p>a</p>',
          title: 'A',
          id: 'viz-a',
        }),
        resultStatus: 'success',
      },
      {
        type: 'thought',
        content: 'thinking',
        timestamp: 2,
      },
    ];
    const embeds = collectHtmlEmbedsFromEvents(events);
    expect(embeds).toHaveLength(1);
    expect(embeds[0].id).toBe('viz-a');
  });
});

describe('collectHtmlEmbedsPrecedingMessage', () => {
  it('collects embeds from workflows since the last user message', () => {
    const timeline: TimelineEntry[] = [
      { kind: 'message', data: { role: 'user', content: 'show wind' }, _uid: 'u1' },
      {
        kind: 'workflow',
        data: {
          events: [
            {
              type: 'tool_call',
              content: { name: 'visualization.create', arguments: { html: '<b>1</b>' } },
              timestamp: 1,
              result: JSON.stringify({
                ok: true,
                kind: 'html_embed',
                html: '<b>1</b>',
                title: 'Wind',
                id: 'w1',
              }),
              resultStatus: 'success',
            },
          ],
          status: null,
          completed: true,
        },
        _uid: 'wf1',
      },
      { kind: 'message', data: { role: 'assistant', content: 'done' }, _uid: 'a1' },
    ];
    const embeds = collectHtmlEmbedsPrecedingMessage(timeline, 2);
    expect(embeds).toHaveLength(1);
    expect(embeds[0].id).toBe('w1');
    expect(embeds[0].title).toBe('Wind');
  });

  it('does not leak embeds across user turns', () => {
    const timeline: TimelineEntry[] = [
      { kind: 'message', data: { role: 'user', content: 'first' }, _uid: 'u0' },
      {
        kind: 'workflow',
        data: {
          events: [
            {
              type: 'tool_call',
              content: { name: 'visualization.create', arguments: { html: '<b>old</b>' } },
              timestamp: 1,
              result: JSON.stringify({
                ok: true,
                kind: 'html_embed',
                html: '<b>old</b>',
                id: 'old',
              }),
              resultStatus: 'success',
            },
          ],
          status: null,
          completed: true,
        },
        _uid: 'wf0',
      },
      { kind: 'message', data: { role: 'assistant', content: 'old reply' }, _uid: 'a0' },
      { kind: 'message', data: { role: 'user', content: 'second' }, _uid: 'u1' },
      { kind: 'message', data: { role: 'assistant', content: 'new reply' }, _uid: 'a1' },
    ];
    expect(collectHtmlEmbedsPrecedingMessage(timeline, 4)).toEqual([]);
    expect(collectHtmlEmbedsPrecedingMessage(timeline, 2)[0]?.id).toBe('old');
  });
});

describe('indexHtmlEmbedsByAssistantMessage', () => {
  it('matches per-row collectHtmlEmbedsPrecedingMessage', () => {
    const timeline: TimelineEntry[] = [
      { kind: 'message', data: { role: 'user', content: 'first' }, _uid: 'u0' },
      {
        kind: 'workflow',
        data: {
          events: [
            {
              type: 'tool_call',
              content: { name: 'visualization.create', arguments: { html: '<b>old</b>' } },
              timestamp: 1,
              result: JSON.stringify({
                ok: true,
                kind: 'html_embed',
                html: '<b>old</b>',
                id: 'old',
              }),
              resultStatus: 'success',
            },
          ],
          status: null,
          completed: true,
        },
        _uid: 'wf0',
      },
      { kind: 'message', data: { role: 'assistant', content: 'old reply' }, _uid: 'a0' },
      { kind: 'message', data: { role: 'user', content: 'second' }, _uid: 'u1' },
      { kind: 'message', data: { role: 'assistant', content: 'new reply' }, _uid: 'a1' },
    ];
    const map = indexHtmlEmbedsByAssistantMessage(timeline);
    expect(map.get(2)?.[0]?.id).toBe('old');
    expect(map.get(4)).toBeUndefined();
    expect(map.get(2)).toEqual(collectHtmlEmbedsPrecedingMessage(timeline, 2));
    expect(map.get(4) ?? []).toEqual(collectHtmlEmbedsPrecedingMessage(timeline, 4));
  });

  // Real session 20260922_115927_auai: the agent created the fillable table,
  // then answered with a bare tool call (content null). The next turn's reply
  // said "表格已经生成好了，就在这条回复下方 👇" — and no iframe rendered,
  // because the payload was dropped at the turn boundary instead of reaching a
  // reply row. These lock the two orderings that produced it.
  const embedBlock = (id: string): TimelineEntry => ({
    kind: 'workflow',
    data: {
      events: [
        {
          type: 'tool_call',
          content: { name: 'visualization__create', arguments: { html: `<b>${id}</b>` } },
          timestamp: 1,
          result: JSON.stringify({ ok: true, kind: 'html_embed', html: `<b>${id}</b>`, id }),
          resultStatus: 'success',
        } as WorkflowEvent,
      ],
      status: null,
      completed: true,
    },
    _uid: `wf-${id}`,
  });

  it('keeps an embed whose turn produced no reply (tail)', () => {
    // Disk order: [user, assistant(announcement), workflow(embed)] — nothing
    // follows the block, so there is no reply to claim it.
    const timeline: TimelineEntry[] = [
      { kind: 'message', data: { role: 'user', content: '做个表格' }, _uid: 'u0' },
      {
        kind: 'message',
        data: { role: 'assistant', content: '可以，我直接给你做一个通用的可交互表格' },
        _uid: 'a0',
      },
      embedBlock('tbl'),
    ];
    const map = indexHtmlEmbedsByAssistantMessage(timeline);
    expect(map.get(1)?.[0]?.id).toBe('tbl');
  });

  it('keeps an embed when the user sends the next message before any reply', () => {
    // Live order: the repeat question lands between the tool work and the reply.
    const timeline: TimelineEntry[] = [
      { kind: 'message', data: { role: 'user', content: '做个表格' }, _uid: 'u0' },
      { kind: 'message', data: { role: 'assistant', content: '正在生成' }, _uid: 'a0' },
      embedBlock('tbl'),
      { kind: 'message', data: { role: 'user', content: '你会做一个可交互表格给我填信息吗' }, _uid: 'u1' },
      { kind: 'message', data: { role: 'assistant', content: '会，表格已经生成好了' }, _uid: 'a1' },
    ];
    const map = indexHtmlEmbedsByAssistantMessage(timeline);
    expect(map.get(1)?.[0]?.id).toBe('tbl');
    // Not duplicated onto the newer reply.
    expect(map.get(4)).toBeUndefined();
  });

  it('still drops nothing when the timeline has no assistant row at all', () => {
    const map = indexHtmlEmbedsByAssistantMessage([
      { kind: 'message', data: { role: 'user', content: 'hi' }, _uid: 'u0' },
      embedBlock('tbl'),
    ]);
    expect([...map.keys()]).toEqual([]);
  });
});
