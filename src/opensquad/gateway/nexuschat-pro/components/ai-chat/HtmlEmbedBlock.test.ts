import { describe, expect, it } from 'vitest';
import {
  collectHtmlEmbedsFromEvents,
  collectHtmlEmbedsPrecedingMessage,
  extractHtmlEmbed,
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
