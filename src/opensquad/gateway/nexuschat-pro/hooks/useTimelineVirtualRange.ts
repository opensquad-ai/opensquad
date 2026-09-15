import { type RefObject, useEffect, useState } from 'react';

export const DEFAULT_ESTIMATE = 96;
export const DEFAULT_OVERSCAN = 14;
export const ALWAYS_RENDER_TAIL = 8;
/** Native layout is smoother than estimated spacers; only window huge sessions. */
export const WINDOW_AFTER = 80;
/** While the user is dragging the main thumb, mount everything up to this
 *  count so estimated 96px spacers cannot fight native scrollbar geometry. */
export const FULL_MOUNT_WHILE_SCROLLING = 240;
/** Rows kept mounted while stick-to-bottom follow is active. */
export const FOLLOW_TAIL = 48;

export type VirtualRange = { start: number; end: number };

export type TimelineWindowLayout = {
  padTopPx: number;
  midStart: number;
  midEnd: number;
  padMidPx: number;
  tailStart: number;
};

export function fullTimelineRange(count: number): VirtualRange {
  return { start: 0, end: Math.max(0, count - 1) };
}

export function tailTimelineRange(count: number, visible = FOLLOW_TAIL): VirtualRange {
  if (count <= 0) return { start: 0, end: 0 };
  return { start: Math.max(0, count - visible), end: count - 1 };
}

/** Grow the window to cover the viewport. Never shrink — spacer height
 *  changes against a 96px estimate are what make the main thumb jitter. */
export function expandTimelineRange(
  prev: VirtualRange,
  visStart: number,
  visEnd: number,
  count: number,
): VirtualRange {
  const start = Math.max(0, Math.min(prev.start, visStart));
  const end = Math.min(Math.max(0, count - 1), Math.max(prev.end, visEnd));
  return prev.start === start && prev.end === end ? prev : { start, end };
}

export function nextTimelineRange(opts: {
  count: number;
  prev: VirtualRange;
  scrolling: boolean;
  unpin: boolean;
  visStart: number;
  visEnd: number;
  windowAfter?: number;
  fullMountLimit?: number;
}): VirtualRange {
  const windowAfter = opts.windowAfter ?? WINDOW_AFTER;
  const fullMountLimit = opts.fullMountLimit ?? FULL_MOUNT_WHILE_SCROLLING;
  if (opts.count <= windowAfter) {
    const next = fullTimelineRange(opts.count);
    return opts.prev.start === next.start && opts.prev.end === next.end ? opts.prev : next;
  }
  if (opts.scrolling) {
    if (opts.count <= fullMountLimit) {
      const next = fullTimelineRange(opts.count);
      return opts.prev.start === next.start && opts.prev.end === next.end ? opts.prev : next;
    }
    return opts.prev;
  }
  if (!opts.unpin) {
    const next = { start: opts.prev.start, end: opts.count - 1 };
    return opts.prev.start === next.start && opts.prev.end === next.end ? opts.prev : next;
  }
  return expandTimelineRange(opts.prev, opts.visStart, opts.visEnd, opts.count);
}

/**
 * Render only timeline rows near the scrollport. Off-screen rows become
 * fixed-height spacers so long sessions do not mount thousands of bubbles.
 */
export function useTimelineVirtualRange(
  scrollRef: RefObject<HTMLElement | null>,
  count: number,
  opts?: {
    estimatePx?: number;
    overscan?: number;
    unpinRef?: RefObject<boolean | null>;
    scrollingRef?: RefObject<boolean>;
  },
): VirtualRange {
  const estimatePx = opts?.estimatePx ?? DEFAULT_ESTIMATE;
  const overscan = opts?.overscan ?? DEFAULT_OVERSCAN;
  const unpinRef = opts?.unpinRef;
  const scrollingRef = opts?.scrollingRef;
  const [range, setRange] = useState<VirtualRange>(() =>
    count <= WINDOW_AFTER ? fullTimelineRange(count) : tailTimelineRange(count),
  );

  useEffect(() => {
    if (count <= WINDOW_AFTER) {
      setRange(fullTimelineRange(count));
      return;
    }
    const el = scrollRef.current;
    if (!el) {
      setRange(tailTimelineRange(count));
      return;
    }

    let raf = 0;
    const update = () => {
      const top = el.scrollTop;
      const h = el.clientHeight || 600;
      const visStart = Math.max(0, Math.floor(top / estimatePx) - overscan);
      const visEnd = Math.min(count - 1, Math.ceil((top + h) / estimatePx) + overscan);
      setRange((prev) =>
        nextTimelineRange({
          count,
          prev,
          scrolling: !!scrollingRef?.current,
          unpin: !!unpinRef?.current,
          visStart,
          visEnd,
        }),
      );
    };

    const onScroll = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        update();
      });
    };

    const onPointerDown = () => {
      if (scrollingRef) scrollingRef.current = true;
      update();
    };

    update();
    el.addEventListener('scroll', onScroll, { passive: true });
    el.addEventListener('pointerdown', onPointerDown);
    return () => {
      el.removeEventListener('scroll', onScroll);
      el.removeEventListener('pointerdown', onPointerDown);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [scrollRef, count, estimatePx, overscan, unpinRef, scrollingRef]);

  return range;
}

/**
 * Collapse a virtual range into O(window) mounted rows: pad + mid + pad + tail.
 * `midEnd < midStart` means the mid slice is empty (viewport is in the tail).
 */
export function layoutTimelineWindow(
  count: number,
  range: VirtualRange,
  estimatePx = DEFAULT_ESTIMATE,
  alwaysTail = ALWAYS_RENDER_TAIL,
  windowAfter = WINDOW_AFTER,
): TimelineWindowLayout {
  if (count <= 0) {
    return { padTopPx: 0, midStart: 0, midEnd: -1, padMidPx: 0, tailStart: 0 };
  }
  if (count <= windowAfter) {
    return { padTopPx: 0, midStart: 0, midEnd: count - 1, padMidPx: 0, tailStart: count };
  }
  const tailStart = Math.max(0, count - alwaysTail);
  let midStart = Math.max(0, Math.min(range.start, count - 1));
  let midEnd = Math.min(range.end, count - 1);
  if (midEnd >= tailStart) {
    midEnd = tailStart - 1;
  }
  if (midStart >= tailStart || midEnd < midStart) {
    return {
      padTopPx: tailStart * estimatePx,
      midStart: 0,
      midEnd: -1,
      padMidPx: 0,
      tailStart,
    };
  }
  return {
    padTopPx: midStart * estimatePx,
    midStart,
    midEnd,
    padMidPx: Math.max(0, tailStart - (midEnd + 1)) * estimatePx,
    tailStart,
  };
}

export function isTimelineIndexVirtualizedAway(
  index: number,
  count: number,
  range: VirtualRange,
): boolean {
  if (count <= WINDOW_AFTER) return false;
  if (index >= count - ALWAYS_RENDER_TAIL) return false;
  return index < range.start || index > range.end;
}
