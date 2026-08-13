import { type RefObject, useEffect, useState } from 'react';

export const DEFAULT_ESTIMATE = 96;
export const DEFAULT_OVERSCAN = 14;
export const ALWAYS_RENDER_TAIL = 4;
export const WINDOW_AFTER = 48;

export type VirtualRange = { start: number; end: number };

export type TimelineWindowLayout = {
  padTopPx: number;
  midStart: number;
  midEnd: number;
  padMidPx: number;
  tailStart: number;
};

/**
 * Render only timeline rows near the scrollport. Off-screen rows become
 * fixed-height spacers so long sessions do not mount thousands of bubbles.
 */
export function useTimelineVirtualRange(
  scrollRef: RefObject<HTMLElement | null>,
  count: number,
  estimatePx = DEFAULT_ESTIMATE,
  overscan = DEFAULT_OVERSCAN,
): VirtualRange {
  const [range, setRange] = useState<VirtualRange>(() => ({
    start: 0,
    end: Math.max(0, count - 1),
  }));

  useEffect(() => {
    if (count <= WINDOW_AFTER) {
      setRange({ start: 0, end: Math.max(0, count - 1) });
      return;
    }
    const el = scrollRef.current;
    if (!el) {
      setRange({ start: Math.max(0, count - 40), end: count - 1 });
      return;
    }

    const update = () => {
      const top = el.scrollTop;
      const h = el.clientHeight || 600;
      const start = Math.max(0, Math.floor(top / estimatePx) - overscan);
      const end = Math.min(count - 1, Math.ceil((top + h) / estimatePx) + overscan);
      setRange((prev) => (prev.start === start && prev.end === end ? prev : { start, end }));
    };

    update();
    el.addEventListener('scroll', update, { passive: true });
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null;
    ro?.observe(el);
    return () => {
      el.removeEventListener('scroll', update);
      ro?.disconnect();
    };
  }, [scrollRef, count, estimatePx, overscan]);

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
