// @vitest-environment jsdom
/**
 * Behavioural lock for the fold primitive (the source scan in
 * `utils/foldPrimitive.scan.test.ts` can only see shapes, not behaviour).
 *
 * These are the three things that actually make the transition work, and the
 * three a refactor is most likely to break:
 *
 *  1. CLOSED body stays in the DOM (an unmounted body has no "from" box);
 *  2. the wrapper carries `is-closed` + `inert` (clipping, and out of the tab
 *     order / a11y tree);
 *  3. the FIRST expansion is armed — the body appears one frame before `open`
 *     flips, which is the only reason a lazily-mounted body can animate.
 *
 * Written with `React.createElement` on purpose: the vitest `include` glob is
 * `**\/*.test.ts`, so the file must not be `.tsx`.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Collapse, ControlledFold, FoldChevron, useFold, type FoldState } from './Collapse';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;

const h = React.createElement;

const shell = (fold: FoldState, lazy: boolean) =>
  h(
    'div',
    null,
    h('button', { id: 'toggle', onClick: () => fold.toggle() }, 'toggle'),
    h(
      Collapse,
      { open: fold.open },
      !lazy || fold.mounted ? h('span', { id: 'body' }, 'BODY') : null,
    ),
  );

const wrapper = () => container.querySelector('.os-collapse') as HTMLElement;
const body = () => container.querySelector('#body');

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

/** Wait for the arming `requestAnimationFrame` to land. */
const nextFrame = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 40));
  });
};

describe('Collapse', () => {
  it('keeps the closed body mounted, clipped and out of the tab order', () => {
    act(() => root.render(h(Collapse, { open: false }, h('span', { id: 'body' }, 'BODY'))));

    expect(body(), 'a closed body MUST stay mounted or nothing can animate').not.toBeNull();
    expect(wrapper().className).toContain('os-collapse');
    expect(wrapper().className).toContain('is-closed');
    // `0fr` clips the track, but only `inert` + aria-hidden take it out of the
    // tab order and the accessibility tree.
    expect(wrapper().hasAttribute('inert')).toBe(true);
    expect(wrapper().getAttribute('aria-hidden')).toBe('true');
    expect(wrapper().firstElementChild?.className).toContain('os-collapse-body');
  });

  it('drops the closed state when open', () => {
    act(() => root.render(h(Collapse, { open: true }, h('span', { id: 'body' }, 'BODY'))));

    expect(wrapper().className).not.toContain('is-closed');
    expect(wrapper().hasAttribute('inert')).toBe(false);
    expect(wrapper().getAttribute('aria-hidden')).toBeNull();
  });

  it('FoldChevron is one icon that rotates, never a swapped pair', () => {
    const mount = (open: boolean) =>
      act(() => root.render(h(FoldChevron as never, { open } as never)));

    mount(false);
    const closed = container.querySelector('svg') as SVGElement;
    expect(closed.getAttribute('class')).toContain('os-fold-chevron');
    expect(closed.getAttribute('class')).not.toContain('is-open');

    mount(true);
    const opened = container.querySelector('svg') as SVGElement;
    expect(opened.getAttribute('class')).toContain('os-fold-chevron');
    expect(opened.getAttribute('class')).toContain('is-open');
    // Same node type/identity — a `ChevronRight`/`ChevronDown` swap could not
    // transition because React would replace the DOM node.
    expect(opened.tagName).toBe(closed.tagName);
  });
});

describe('useFold', () => {
  it('arms the first expansion: body mounts one frame BEFORE open flips', async () => {
    let latest: FoldState | null = null;
    const Probe = () => {
      const fold = useFold(false);
      latest = fold;
      return shell(fold, true);
    };
    act(() => root.render(h(Probe)));

    expect(latest!.open).toBe(false);
    expect(latest!.mounted).toBe(false);
    expect(body()).toBeNull(); // nothing paid for while collapsed

    // First expansion — the body must appear while the wrapper is still closed.
    act(() => {
      (container.querySelector('#toggle') as HTMLButtonElement).click();
    });
    expect(latest!.mounted, 'body must be mounted in the arming commit').toBe(true);
    expect(latest!.open, 'open must lag one frame so there is a transition').toBe(false);
    expect(body()).not.toBeNull();
    expect(wrapper().className).toContain('is-closed');

    await nextFrame();
    expect(latest!.open).toBe(true);
    expect(wrapper().className).not.toContain('is-closed');
  });

  it('toggles back and forth instantly once mounted (pure CSS after the first open)', async () => {
    let latest: FoldState | null = null;
    const Probe = () => {
      const fold = useFold(false);
      latest = fold;
      return shell(fold, true);
    };
    act(() => root.render(h(Probe)));

    const click = () =>
      act(() => {
        (container.querySelector('#toggle') as HTMLButtonElement).click();
      });

    click();
    await nextFrame();
    expect(latest!.open).toBe(true);

    click(); // close — must be immediate, no arming
    expect(latest!.open).toBe(false);
    expect(latest!.mounted, 'stays mounted after the first open').toBe(true);
    expect(body(), 'closing must not unmount').not.toBeNull();

    click(); // re-open — immediate, already mounted
    expect(latest!.open).toBe(true);
  });

  it('setOpenNow restores state without animating it', () => {
    let latest: FoldState | null = null;
    const Probe = () => {
      const fold = useFold(false);
      latest = fold;
      return shell(fold, true);
    };
    act(() => root.render(h(Probe)));

    act(() => latest!.setOpenNow(true));
    // Restored-open state must be visible in the same commit — an animated
    // expand right after paint reads as a flash.
    expect(latest!.open).toBe(true);
    expect(latest!.mounted).toBe(true);
    expect(wrapper().className).not.toContain('is-closed');
  });
});

describe('ControlledFold', () => {
  /** Caller owns the boolean — the shape used by the Set-driven accordions. */
  const mount = (open: boolean) => {
    let setter: (v: boolean) => void = () => {};
    const Probe = () => {
      const [v, set] = React.useState(open);
      setter = set;
      return h(
        'div',
        null,
        h('button', { id: 'toggle', onClick: () => set((p) => !p) }, 'toggle'),
        h(ControlledFold, { open: v }, h('span', { id: 'body' }, 'BODY')),
      );
    };
    act(() => root.render(h(Probe)));
    return () => setter;
  };

  it('does not pay for the body while collapsed, yet animates the first open', async () => {
    const get = mount(false);

    expect(body(), 'lazy by default').toBeNull();

    act(() => {
      (container.querySelector('#toggle') as HTMLButtonElement).click();
    });
    // Render-phase latch: the body exists, the wrapper is still closed.
    expect(body()).not.toBeNull();
    expect(wrapper().className).toContain('is-closed');

    await nextFrame();
    expect(wrapper().className).not.toContain('is-closed');

    // Closing keeps it mounted, so the reverse direction animates too.
    act(() => {
      (container.querySelector('#toggle') as HTMLButtonElement).click();
    });
    expect(wrapper().className).toContain('is-closed');
    expect(body(), 'closing must not unmount').not.toBeNull();
    void get;
  });
});
