// @vitest-environment jsdom
/**
 * Behavioural lock for the follow-up suggestion chips.
 *
 * The source scan (`utils/followupSuggestions.scan.test.ts`) proves the wiring
 * exists; this file proves it behaves. Three things matter and are easy to
 * regress:
 *
 *  1. a tap sends the suggestion's FULL text (not its id, not a truncated label);
 *  2. hydration is turn-aware — a `suggest_followups` event that is followed by
 *     a newer user message must NOT be restored, or a refresh resurrects stale
 *     chips that point at a conversation that already moved on;
 *  3. malformed payloads degrade to "no chips" instead of throwing inside the
 *     WS event handler (which would kill the whole info stream).
 *
 * Written with `React.createElement` on purpose: the vitest include glob only
 * matches `.test.ts` files, so this file must not be `.tsx`.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  FollowupSuggestions,
  hydrateFollowupsFromEvents,
  parseFollowupSuggestions,
} from './ai-chat/FollowupSuggestions';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;

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

const render = (node: React.ReactElement) => act(() => root.render(node));

const chips = () => Array.from(container.querySelectorAll('button'));

describe('FollowupSuggestions — rendering', () => {
  it('renders one tappable chip per suggestion, in order', () => {
    const items = [
      { id: 'fu_1', text: '把这次复盘的结论导出成 md 报告' },
      { id: 'fu_2', text: '分析今日涨停板块与十五五规划的关联' },
      { id: 'fu_3', text: '针对弱兑现组给出优化建议' },
    ];
    render(React.createElement(FollowupSuggestions, { suggestions: items, onPick: () => {} }));

    const buttons = chips();
    expect(buttons).toHaveLength(3);
    expect(buttons.map((b) => b.textContent)).toEqual(items.map((i) => i.text));
    expect(container.querySelector('[data-testid="followup-suggestions"]')).not.toBeNull();
  });

  it('renders nothing for an empty list (the composer slot collapses)', () => {
    render(React.createElement(FollowupSuggestions, { suggestions: [], onPick: () => {} }));
    expect(container.querySelector('[data-testid="followup-suggestions"]')).toBeNull();
    expect(chips()).toHaveLength(0);
  });

  it('carries the full text as title so a clamped chip is still readable', () => {
    const long = '把本次分析中提到的所有板块逐一列出，并标注每个板块的兑现率与证据数量';
    render(
      React.createElement(FollowupSuggestions, {
        suggestions: [{ id: 'fu_1', text: long }],
        onPick: () => {},
      }),
    );
    expect(chips()[0].getAttribute('title')).toBe(long);
    expect(chips()[0].textContent).toContain(long);
  });
});

describe('FollowupSuggestions — picking', () => {
  it('sends the suggestion TEXT (verbatim) exactly once', () => {
    const onPick = vi.fn();
    const items = [
      { id: 'fu_1', text: '第一条追问' },
      { id: 'fu_2', text: '第二条追问' },
    ];
    render(React.createElement(FollowupSuggestions, { suggestions: items, onPick }));

    act(() => {
      chips()[1].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick).toHaveBeenCalledWith('第二条追问');
  });
});

describe('parseFollowupSuggestions — tolerant of messy tool-call shapes', () => {
  it('accepts plain strings and assigns positional ids', () => {
    expect(parseFollowupSuggestions(['甲', '乙'])).toEqual([
      { id: 'fu_1', text: '甲' },
      { id: 'fu_2', text: '乙' },
    ]);
  });

  it('accepts {id,text} dicts and falls back to title/content/label', () => {
    expect(
      parseFollowupSuggestions([
        { id: 'a', text: '来自 text' },
        { title: '来自 title' },
        { content: '来自 content' },
      ]),
    ).toEqual([
      { id: 'a', text: '来自 text' },
      { id: 'fu_2', text: '来自 title' },
      { id: 'fu_3', text: '来自 content' },
    ]);
    expect(parseFollowupSuggestions([{ label: '来自 label' }])).toEqual([
      { id: 'fu_1', text: '来自 label' },
    ]);
  });

  it('caps at 3 even if the backend over-sends', () => {
    expect(parseFollowupSuggestions(['1', '2', '3', '4', '5'])).toHaveLength(3);
  });

  it('drops unusable entries without throwing', () => {
    expect(parseFollowupSuggestions([null, '', '  ', {}, 42])).toEqual([]);
    expect(parseFollowupSuggestions(null)).toEqual([]);
    expect(parseFollowupSuggestions('not-a-list')).toEqual([]);
  });
});

describe('hydrateFollowupsFromEvents — the last offer wins, and only if still live', () => {
  const infoEvent = (payload: Record<string, unknown>) => ({ type: 'info', content: payload });

  /**
   * The exact shape `runner` persists at the top of every user turn —
   * `add_event("info", {"text": "Workflow started", "started_ms": …})`, right
   * after `self._current_round += 1`. This, and NOT a `role: 'user'` event, is
   * how hydration learns the user has moved on: user text is only ever written
   * to `messages`, never to `events`.
   */
  const roundStart = (roundId: number) => ({
    type: 'info',
    data: { text: 'Workflow started', started_ms: 1_700_000_000_000 },
    timestamp: '2026-09-14T00:00:00Z',
    turn_id: 0,
    round_id: roundId,
  });

  const offer = (text: string) => ({
    type: 'info',
    data: {
      event: 'suggest_followups',
      id: 'fu_abc',
      suggestions: [{ id: 'fu_1', text }],
      text: `建议追问：${text}`,
    },
    timestamp: '2026-09-14T00:00:05Z',
  });

  it('restores the suggestions of the last suggest_followups event', () => {
    expect(
      hydrateFollowupsFromEvents([
        infoEvent({ event: 'other_business' }),
        infoEvent({
          event: 'suggest_followups',
          suggestions: [{ id: 'fu_1', text: '追一下' }],
        }),
      ]),
    ).toEqual([{ id: 'fu_1', text: '追一下' }]);
  });

  it('hydrates the exact persisted {type:info,data} shape the backend writes', () => {
    expect(hydrateFollowupsFromEvents([roundStart(1), offer('导出成 md 报告')])).toEqual([
      { id: 'fu_1', text: '导出成 md 报告' },
    ]);
  });

  it('keeps the offer while its own round is still the newest one', () => {
    // In-round events (token usage, tool calls) follow the offer inside the
    // same round — none of them may be mistaken for a new user turn.
    expect(
      hydrateFollowupsFromEvents([
        roundStart(2),
        offer('再跑一次全量测试'),
        { type: 'turn_usage', data: { total_tokens: 1200 } },
        { type: 'thought', data: { text: 'Agent entering wait mode' } },
      ]),
    ).toEqual([{ id: 'fu_1', text: '再跑一次全量测试' }]);
  });

  it('returns [] once a newer round start follows the offer (user ignored it)', () => {
    // The user typed their own message instead of tapping a chip: the backend
    // opened a new round, so the old offer was implicitly consumed and must not
    // come back — even when that new round produced no offer of its own.
    expect(
      hydrateFollowupsFromEvents([
        roundStart(1),
        offer('旧建议'),
        roundStart(2),
        { type: 'turn_usage', data: { total_tokens: 42 } },
      ]),
    ).toEqual([]);
  });

  it('does not treat a lookalike info event as a round start', () => {
    expect(
      hydrateFollowupsFromEvents([
        offer('还是最新的'),
        { type: 'info', data: { text: 'Agent entering wait mode - listening for events' } },
      ]),
    ).toEqual([{ id: 'fu_1', text: '还是最新的' }]);
  });

  it('requires the round marker to arrive as an `info` event', () => {
    // Same text, wrong channel — must not end the search early.
    expect(
      hydrateFollowupsFromEvents([offer('保留我'), { type: 'thought', text: 'Workflow started' }]),
    ).toEqual([{ id: 'fu_1', text: '保留我' }]);
  });

  it('still honours a persisted user-message event (forward-compat)', () => {
    // Never emitted by today's backend (see the scan guard R10) — kept so the
    // hydration stays correct the day user turns do get persisted as events.
    expect(
      hydrateFollowupsFromEvents([
        infoEvent({
          event: 'suggest_followups',
          suggestions: [{ id: 'fu_1', text: '追一下' }],
        }),
        { type: 'message', role: 'user', content: '用户已经自己提问了' },
        { type: 'message', role: 'assistant', content: '回答' },
      ]),
    ).toEqual([]);
  });

  it('keeps the newest offer when several were emitted in one session', () => {
    expect(
      hydrateFollowupsFromEvents([
        infoEvent({
          event: 'suggest_followups',
          suggestions: [{ id: 'fu_1', text: '旧建议' }],
        }),
        infoEvent({ event: 'turn_usage', total_tokens: 10 }),
        infoEvent({
          event: 'suggest_followups',
          suggestions: [{ id: 'fu_1', text: '新建议' }],
        }),
      ]),
    ).toEqual([{ id: 'fu_1', text: '新建议' }]);
  });

  it('reads the nested {data:{event}} shape too', () => {
    expect(
      hydrateFollowupsFromEvents([
        {
          type: 'info',
          data: { event: 'suggest_followups', suggestions: [{ id: 'fu_1', text: '嵌套' }] },
        },
      ]),
    ).toEqual([{ id: 'fu_1', text: '嵌套' }]);
  });

  it('degrades to [] on empty / unknown history', () => {
    expect(hydrateFollowupsFromEvents([])).toEqual([]);
    expect(hydrateFollowupsFromEvents(undefined)).toEqual([]);
    expect(
      hydrateFollowupsFromEvents([infoEvent({ event: 'suggest_followups' })]),
    ).toEqual([]);
  });
});
