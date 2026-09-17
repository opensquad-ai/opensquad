// @vitest-environment jsdom
/**
 * `openSessionTab` — the producer half of the "open a session tab" contract.
 *
 * The whole feature depends on a window `CustomEvent`, which fails *silently*:
 * before this helper existed, `AIChatPage` listened for
 * `opensquad-open-session-tab` and nothing in the codebase ever dispatched it,
 * so "click the finished parallel task to read its tool flow" did nothing at
 * all — no error, no warning, no tab. A unit test on a dispatch is the only way
 * to lock the shape of something the type system cannot see.
 *
 * Pinned here:
 *   B1 the event is dispatched on `window` with `detail.sessionId`;
 *   B2 a blank/absent id dispatches nothing and reports `false`, so callers can
 *      keep the affordance disabled instead of opening an empty tab;
 *   B3 the id is trimmed — the session store keys on the exact string;
 *   B4 the id is passed through verbatim otherwise (no accidental casing or
 *      prefix rewriting: `parseContentTabKey` gets it as-is).
 */
import { describe, expect, it } from 'vitest';

import { OPEN_SESSION_TAB_EVENT, openSessionTab } from './uiEvents';

interface Captured {
  sessionId: unknown;
  eventName: string;
}

/** Collect every dispatch of the event while `fn` runs. */
const capture = (fn: () => void): Captured[] => {
  const got: Captured[] = [];
  const handler = (e: Event) => {
    got.push({ sessionId: (e as CustomEvent).detail?.sessionId, eventName: e.type });
  };
  window.addEventListener(OPEN_SESSION_TAB_EVENT, handler);
  try {
    fn();
  } finally {
    window.removeEventListener(OPEN_SESSION_TAB_EVENT, handler);
  }
  return got;
};

describe('B1 — the event carries the session id', () => {
  it('dispatches one event named OPEN_SESSION_TAB_EVENT', () => {
    const got = capture(() => openSessionTab('20260916_abc'));
    expect(got).toHaveLength(1);
    expect(got[0].eventName).toBe(OPEN_SESSION_TAB_EVENT);
    expect(got[0].sessionId).toBe('20260916_abc');
  });

  it('reports that it dispatched', () => {
    expect(capture(() => expect(openSessionTab('s1')).toBe(true))).toHaveLength(1);
  });
});

describe('B2 — a missing id is not a request', () => {
  it('dispatches nothing for an absent or blank id', () => {
    for (const raw of ['', '   ', '\t\n', null, undefined]) {
      const got = capture(() => {
        expect(openSessionTab(raw)).toBe(false);
      });
      expect(got, `raw=${JSON.stringify(raw)}`).toEqual([]);
    }
  });
});

describe('B3/B4 — the id is trimmed, otherwise passed through verbatim', () => {
  it('trims surrounding whitespace', () => {
    const got = capture(() => openSessionTab('  s1  '));
    expect(got[0].sessionId).toBe('s1');
  });

  it('does not otherwise rewrite the id', () => {
    // The session store keys on the exact string, so a "helpful" lowercase or
    // prefix would open nothing.
    for (const id of ['20260916_061130_XANN', 'a.b-c_d', '0']) {
      const got = capture(() => openSessionTab(id));
      expect(got[0].sessionId).toBe(id);
    }
  });
});
