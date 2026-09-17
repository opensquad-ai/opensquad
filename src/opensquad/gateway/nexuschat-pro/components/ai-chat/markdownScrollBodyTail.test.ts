// @vitest-environment jsdom
/**
 * Behavioural lock for the thought "tail" (`os-thought-tail`).
 *
 * The live deep-think body was a flat wall of `textMuted` — measured on the
 * reported screenshot, every plain-text glyph core sat at the same luminance
 * (123 on a 246 background), so the newest line the agent had just produced
 * looked exactly like the oldest one. The tail turns the scroll box into a
 * comet: newest lines are still settling, older lines are solid.
 *
 * A pure-function test cannot see this: the effect is a class the scroll
 * container carries, and its whole point is that it is *conditional*. So the
 * component is really rendered here, and the three transitions that matter are
 * driven for real:
 *
 *   T1  a live body carries the mask and the mount fade;
 *   T2  a body that is not live carries neither — a finished thought is
 *       rendered at full weight for reading;
 *   T3  the reader leaving the bottom drops the mask (reading mode), and
 *       coming back restores it;
 *   T4  the next live body starts re-armed at the bottom;
 *   T5  `FollowScrollBox` reports stick state on *edges only* — one event per
 *       crossing, not one per scroll frame (the parent `setState`s).
 *
 * Written with `React.createElement`: the vitest `include` glob is
 * `**\/*.test.ts`, so this file must not be `.tsx`.
 *
 * Mutations verified — see `C:/tmp/prov/mutate_thoughttail.py`:
 *   MA1 `tail = softEdge && stuck` → `softEdge` (mask never drops in reading
 *       mode)                                        → T3
 *   MA2 drop the `onStickChange` wiring             → T3
 *   MA3 drop the `softEdge` re-arm effect           → T4
 *   MA4 `publish` loses its edge check              → T5
 *   MA5 thought body stops passing `softEdge`       → `thoughtTail.scan.test.ts`
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { MarkdownScrollBody } from './MarkdownScrollBody';
import { FollowScrollBox } from './FollowScrollBox';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;

const h = React.createElement;

const TEXT = 'first line\n\nsecond line';

const renderBody = (props: Record<string, unknown> = {}) => {
  act(() => {
    root.render(h(MarkdownScrollBody, { text: TEXT, ...props }));
  });
  return container.firstElementChild as HTMLElement;
};

/**
 * jsdom reports every scroll metric as 0, which reads as "pinned to the
 * bottom". Overwrite them per element so a real scroll position can be
 * simulated without a layout engine.
 */
const setMetrics = (el: HTMLElement, scrollHeight: number, clientHeight: number, scrollTop: number) => {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: scrollHeight });
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: clientHeight });
  Object.defineProperty(el, 'scrollTop', { configurable: true, writable: true, value: scrollTop });
};

const scroll = (el: HTMLElement) => {
  act(() => {
    el.dispatchEvent(new Event('scroll'));
  });
};

const hasTail = (el: HTMLElement) => el.classList.contains('os-thought-tail');

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

describe('T1 — a live body carries the tail', () => {
  it('mask + mount fade are on the scroll container', () => {
    const el = renderBody({ softEdge: true, follow: true });
    expect(hasTail(el)).toBe(true);
    expect(el.classList.contains('os-thought-settle')).toBe(true);
  });

  it('the mask sits on the scroll container, not on the text node', () => {
    // A mask on the inner markdown div would fade with the content instead of
    // staying in viewport space — the gradient would then never move.
    const el = renderBody({ softEdge: true, follow: true });
    expect(el.className).toContain('overflow-y-auto');
    expect(el.firstElementChild?.className).not.toContain('os-thought-tail');
  });

  it('and the text is still rendered', () => {
    const el = renderBody({ softEdge: true, follow: true });
    expect(el.textContent).toContain('second line');
  });
});

describe('T2 — a body that is not live is left alone', () => {
  it('no mask and no mount fade without softEdge', () => {
    const el = renderBody({ follow: false });
    expect(el.className).not.toContain('os-thought-tail');
    expect(el.className).not.toContain('os-thought-settle');
  });

  it('still renders the text at full weight', () => {
    const el = renderBody({ follow: false, muted: true });
    expect(el.textContent).toContain('first line');
    expect(el.querySelector('.text-textMuted')).not.toBeNull();
  });
});

describe('T3 — reading mode drops the tail', () => {
  it('scrolling away from the bottom removes the mask', () => {
    const el = renderBody({ softEdge: true, follow: true });
    expect(hasTail(el)).toBe(true);

    setMetrics(el, 1000, 200, 0);
    scroll(el);

    expect(hasTail(el)).toBe(false);
    expect(el.textContent).toContain('second line');
  });

  it('and returning to the bottom brings it back', () => {
    const el = renderBody({ softEdge: true, follow: true });
    setMetrics(el, 1000, 200, 0);
    scroll(el);
    expect(hasTail(el)).toBe(false);

    setMetrics(el, 1000, 200, 800);
    scroll(el);
    expect(hasTail(el)).toBe(true);
  });

  it('staying near the bottom keeps it (48px tolerance)', () => {
    const el = renderBody({ softEdge: true, follow: true });
    setMetrics(el, 1000, 200, 770);
    scroll(el);
    expect(hasTail(el)).toBe(true);
  });
});

describe('T4 — the next live body is re-armed', () => {
  it('starts pinned to the bottom again', () => {
    const el = renderBody({ softEdge: true, follow: true });
    setMetrics(el, 1000, 200, 0);
    scroll(el);
    expect(hasTail(el)).toBe(false);

    // Idle, then a new streaming thought arrives.
    renderBody({ softEdge: false, follow: false });
    const el2 = renderBody({ softEdge: true, follow: true });

    expect(hasTail(el2)).toBe(true);
  });
});

describe('T5 — stick state is reported on edges only', () => {
  it('one notification per crossing', () => {
    const calls: boolean[] = [];
    let el!: HTMLElement;
    act(() => {
      root.render(
        h(FollowScrollBox, {
          contentKey: 0,
          follow: true,
          onStickChange: (s: boolean) => calls.push(s),
          children: h('span', null, 'x'),
        }),
      );
    });
    el = container.firstElementChild as HTMLElement;

    setMetrics(el, 1000, 200, 0);
    scroll(el);
    scroll(el);
    scroll(el);
    expect(calls).toEqual([false]);

    setMetrics(el, 1000, 200, 800);
    scroll(el);
    scroll(el);
    expect(calls).toEqual([false, true]);
  });
});
