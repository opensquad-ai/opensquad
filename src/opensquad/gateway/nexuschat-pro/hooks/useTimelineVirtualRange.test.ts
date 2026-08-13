import { describe, expect, it } from 'vitest';
import {
  ALWAYS_RENDER_TAIL,
  DEFAULT_ESTIMATE,
  WINDOW_AFTER,
  isTimelineIndexVirtualizedAway,
  layoutTimelineWindow,
} from './useTimelineVirtualRange';

describe('layoutTimelineWindow', () => {
  it('mounts every row when count is within the window threshold', () => {
    const layout = layoutTimelineWindow(WINDOW_AFTER, { start: 0, end: WINDOW_AFTER - 1 });
    expect(layout.padTopPx).toBe(0);
    expect(layout.padMidPx).toBe(0);
    expect(layout.midStart).toBe(0);
    expect(layout.midEnd).toBe(WINDOW_AFTER - 1);
    expect(layout.tailStart).toBe(WINDOW_AFTER);
  });

  it('uses two spacers plus a short tail instead of O(n) placeholders', () => {
    const count = 400;
    const range = { start: 80, end: 120 };
    const layout = layoutTimelineWindow(count, range);
    const midCount = layout.midEnd - layout.midStart + 1;
    const tailCount = count - layout.tailStart;
    expect(layout.padTopPx).toBe(80 * DEFAULT_ESTIMATE);
    expect(layout.midStart).toBe(80);
    expect(layout.midEnd).toBe(120);
    expect(layout.padMidPx).toBe((layout.tailStart - 121) * DEFAULT_ESTIMATE);
    expect(layout.tailStart).toBe(count - ALWAYS_RENDER_TAIL);
    expect(midCount + tailCount).toBeLessThan(50);
    expect(midCount + tailCount).toBe(120 - 80 + 1 + ALWAYS_RENDER_TAIL);
  });

  it('skips the mid slice when the viewport is entirely in the tail', () => {
    const count = 200;
    const layout = layoutTimelineWindow(count, { start: 196, end: 199 });
    expect(layout.midEnd).toBeLessThan(layout.midStart);
    expect(layout.padTopPx).toBe((count - ALWAYS_RENDER_TAIL) * DEFAULT_ESTIMATE);
    expect(layout.padMidPx).toBe(0);
    expect(layout.tailStart).toBe(count - ALWAYS_RENDER_TAIL);
  });
});

describe('isTimelineIndexVirtualizedAway', () => {
  it('keeps the last tail rows even when they are outside the range', () => {
    const count = 100;
    const range = { start: 10, end: 20 };
    expect(isTimelineIndexVirtualizedAway(5, count, range)).toBe(true);
    expect(isTimelineIndexVirtualizedAway(15, count, range)).toBe(false);
    expect(isTimelineIndexVirtualizedAway(99, count, range)).toBe(false);
  });
});
