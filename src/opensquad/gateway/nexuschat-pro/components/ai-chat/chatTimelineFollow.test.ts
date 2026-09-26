// @vitest-environment jsdom
/**
 * Behavioural lock for the main chat scroll-follow (ChatTimeline).
 *
 * Reported symptom: during a deep-thinking turn the whole column moved in
 * stutters — "follow a bit, freeze, jump" — and only while the live thought
 * body was still growing (before its own 320px scrollbar appeared). The cause
 * was self-inflicted: `pin()` writes `el.scrollTop`, and a programmatic write
 * fires a `scroll` event on that same element. `handleScroll` treated every
 * event as a user drag (`markUserScrolling`), so each successful pin disarmed
 * the next 180ms of pins — the column lagged a chunk-frames' worth of growth,
 * then snapped. Once the thought body capped its height the column stopped
 * growing per chunk, `pin()` was no longer called, and the stutter vanished,
 * which is exactly the reported shape.
 *
 * `FollowScrollBox` already suppressed this echo (`lastSetTopRef`); the
 * timeline did not. Two transitions are driven here for real:
 *
 *   T1  the echo of our own pin does not disarm the next pin;
 *   T2  a genuine user scroll still stops the follow (the guard must not
 *       over-correct into yanking the reader back to the bottom).
 *
 * jsdom has no layout engine and no ResizeObserver, so scroll metrics are
 * modelled explicitly and the observer hands its callbacks to the test.
 *
 * Written with `React.createElement`: the vitest `include` glob is
 * `**\/*.test.ts`, so this file must not be `.tsx`.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatTimeline } from './ChatTimeline';

// The follow path bails while a fold is animating. No fold is involved here, and
// `performance.now()` right after the jsdom window boots would otherwise read as
// "a fold was toggled just now".
vi.mock('../Collapse', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../Collapse')>();
  return { ...actual, isFoldAnimating: () => false };
});

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

const VIEW_PX = 400;
const ROW_PX = 200;

let roCallbacks: Array<() => void> = [];

class FakeResizeObserver {
  private cb: () => void;

  constructor(cb: () => void) {
    this.cb = cb;
  }

  observe() {
    roCallbacks.push(this.cb);
  }

  unobserve() {}

  disconnect() {
    roCallbacks = roCallbacks.filter((c) => c !== this.cb);
  }
}

type Harness = {
  /** Content grows by `px` and the column observer reports a resize. */
  grow: (px: number) => void;
  /** The reader drags the thumb to `top` (no echo: it is not our value). */
  dragTo: (top: number) => void;
  /** The scroll event the browser fires after our own scrollTop write. */
  pinEcho: () => void;
  top: () => number;
};

let container: HTMLDivElement;
let root: Root | undefined;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  roCallbacks = [];
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  container = document.createElement('div');
  document.body.appendChild(container);
});

afterEach(() => {
  if (root) act(() => root?.unmount());
  root = undefined;
  container.remove();
  vi.unstubAllGlobals();
});

const renderTimeline = (): Harness => {
  const scrollRef = React.createRef<HTMLDivElement>();
  const unpinRef = { current: false };

  act(() => {
    root = createRoot(container);
    root.render(
      h(ChatTimeline, {
        scrollRef,
        unpinRef,
        entries: [{ _uid: 'a' }, { _uid: 'b' }],
        renderEntry: (_entry: unknown, _i: number, key: string) => h('div', { key }, 'row'),
      }),
    );
  });

  const el = scrollRef.current;
  if (!el) throw new Error('timeline scroll container was not rendered');

  // A real scroll model: scrollTop clamps to [0, scrollHeight - clientHeight],
  // so pin() sees the same "already at the bottom" state a browser would give it.
  let contentPx = VIEW_PX + ROW_PX;
  let topPx = 0;
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => contentPx });
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => VIEW_PX });
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => topPx,
    set: (v: number) => {
      topPx = Math.max(0, Math.min(v, Math.max(0, contentPx - VIEW_PX)));
    },
  });

  const fireResize = () => {
    act(() => {
      for (const cb of [...roCallbacks]) cb();
    });
  };

  return {
    grow: (px: number) => {
      contentPx += px;
      fireResize();
    },
    dragTo: (top: number) => {
      topPx = Math.max(0, Math.min(top, Math.max(0, contentPx - VIEW_PX)));
      act(() => {
        el.dispatchEvent(new Event('scroll'));
      });
    },
    pinEcho: () => {
      act(() => {
        el.dispatchEvent(new Event('scroll'));
      });
    },
    top: () => topPx,
  };
};

describe('ChatTimeline stick-to-bottom', () => {
  it('the echo of its own pin does not disarm the next pin (T1)', () => {
    const tl = renderTimeline();

    tl.grow(ROW_PX);
    const first = tl.top();
    expect(first).toBeGreaterThan(0);

    // The browser fires `scroll` for the write pin() just made. Nothing about
    // the reader changed, so the next chunk must still be followed.
    tl.pinEcho();
    tl.grow(ROW_PX);

    expect(tl.top()).toBeGreaterThan(first);
  });

  it('a genuine user scroll still stops the follow (T2)', () => {
    const tl = renderTimeline();

    tl.grow(ROW_PX);
    const pinned = tl.top();
    expect(pinned).toBeGreaterThan(0);

    // The reader drags the thumb back up: not our value, so not an echo.
    tl.dragTo(0);
    tl.grow(ROW_PX);

    expect(tl.top()).toBe(0);
  });
});
