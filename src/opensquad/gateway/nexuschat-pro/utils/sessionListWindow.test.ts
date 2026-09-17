/**
 * Sidebar session-list paging window.
 *
 * The regression under test: every silent refresh re-read page 1 and wrote it
 * over the rendered rows. After the user had scrolled a few pages in, each
 * refresh truncated the list back to one page — which invalidated the scroll
 * position, put the bottom sentinel back in view, immediately fired load-more,
 * and had the next refresh truncate it again. Observed as "it loads, then
 * refreshes, then refreshes again — looks stuck".
 */
import { describe, expect, it } from 'vitest';
import {
  advanceLoadedRows,
  appendSessionPage,
  isNearListEnd,
  mergeRefreshedPrefix,
  refreshLimit,
  SESSION_LIST_MAX_LIMIT,
  SESSION_LIST_PAGE_SIZE,
} from './sessionListWindow';

type Row = { id: string; title?: string; current?: boolean };

const row = (id: string, extra: Partial<Row> = {}): Row => ({ id, ...extra });
/** `count` rows named `s0..s{n-1}` — i.e. the newest-first slice of a bigger list. */
const rows = (count: number, from = 0): Row[] =>
  Array.from({ length: count }, (_, i) => row(`s${from + i}`));

describe('refreshLimit', () => {
  it('never asks for less than one page', () => {
    expect(refreshLimit(0)).toBe(SESSION_LIST_PAGE_SIZE);
    expect(refreshLimit(-5)).toBe(SESSION_LIST_PAGE_SIZE);
    expect(refreshLimit(Number.NaN)).toBe(SESSION_LIST_PAGE_SIZE);
  });

  it('re-reads the whole window already on screen', () => {
    expect(refreshLimit(100)).toBe(100);
    expect(refreshLimit(350)).toBe(350);
  });

  it('clamps to the server cap so a refresh stays bounded', () => {
    expect(refreshLimit(900)).toBe(SESSION_LIST_MAX_LIMIT);
    expect(refreshLimit(SESSION_LIST_MAX_LIMIT)).toBe(SESSION_LIST_MAX_LIMIT);
  });
});

describe('advanceLoadedRows', () => {
  it('counts server rows, so the offset is the running total consumed', () => {
    expect(advanceLoadedRows(0, 100)).toBe(100);
    expect(advanceLoadedRows(100, 100)).toBe(200);
    expect(advanceLoadedRows(200, 40)).toBe(240);
  });

  it('treats a short final page as progress without overrunning', () => {
    expect(advanceLoadedRows(300, 0)).toBe(300);
  });

  it('ignores non-finite input rather than poisoning the cursor', () => {
    expect(advanceLoadedRows(Number.NaN, 50)).toBe(50);
    expect(advanceLoadedRows(50, Number.NaN)).toBe(50);
  });
});

describe('mergeRefreshedPrefix', () => {
  it('does not truncate a scrolled list back to page 1', () => {
    const rendered = rows(300);
    const freshPage1 = rows(SESSION_LIST_PAGE_SIZE);
    const next = mergeRefreshedPrefix(rendered, freshPage1);
    // The bug produced 100 here, which is what re-armed the sentinel.
    expect(next).toHaveLength(300);
    expect(next.map((r) => r.id)).toEqual(rendered.map((r) => r.id));
  });

  it('lets refreshed values win for the rows the refresh covered', () => {
    const rendered = [row('s0', { title: 'old' }), row('s1'), row('s2'), row('s3')];
    const fresh = [row('s0', { title: 'new', current: true }), row('s1'), row('s2')];
    const next = mergeRefreshedPrefix(rendered, fresh);
    expect(next[0]).toMatchObject({ id: 's0', title: 'new', current: true });
    expect(next.map((r) => r.id)).toEqual(['s0', 's1', 's2', 's3']);
  });

  it('keeps the tail in its existing order', () => {
    const rendered = rows(5);
    const fresh = rows(2);
    expect(mergeRefreshedPrefix(rendered, fresh).map((r) => r.id)).toEqual([
      's0',
      's1',
      's2',
      's3',
      's4',
    ]);
  });

  it('never returns something shorter than the rendered list', () => {
    const rendered = rows(10);
    // A refresh that dropped rows: 4 of the 10 are gone and one is new.
    const fresh = [...rows(3), row('new')];
    const next = mergeRefreshedPrefix(rendered, fresh);
    expect(next.length).toBeGreaterThanOrEqual(rendered.length);
    expect(next[0].id).toBe('s0');
    expect(next.some((r) => r.id === 'new')).toBe(true);
    // The rows the refresh did cover are not duplicated back in from the tail.
    expect(next.filter((r) => r.id === 's0')).toHaveLength(1);
  });

  it('adopts a longer fresh page wholesale (first load / newer-first reorder)', () => {
    const rendered = rows(2);
    const fresh = rows(6);
    expect(mergeRefreshedPrefix(rendered, fresh)).toBe(fresh);
  });

  it('returns the fresh page when nothing is rendered yet', () => {
    const fresh = rows(3);
    expect(mergeRefreshedPrefix([], fresh)).toBe(fresh);
  });

  it('returns an empty list when the refresh is empty and nothing was rendered', () => {
    expect(mergeRefreshedPrefix([], [])).toEqual([]);
  });
});

describe('appendSessionPage', () => {
  it('appends new rows and skips ids already present', () => {
    const rendered = rows(3);
    const next = appendSessionPage(rendered, [row('s3'), row('s4')]);
    expect(next.map((r) => r.id)).toEqual(['s0', 's1', 's2', 's3', 's4']);
  });

  it('returns the same reference when a page adds nothing — no idle re-render', () => {
    const rendered = rows(3);
    expect(appendSessionPage(rendered, [row('s0'), row('s1')])).toBe(rendered);
    expect(appendSessionPage(rendered, [])).toBe(rendered);
  });
});

describe('isNearListEnd', () => {
  it('is true inside the threshold and false outside it', () => {
    const base = { scrollHeight: 1000, clientHeight: 400 };
    // 600 - 440 = 160 → at the threshold, still not "near" (strict <)
    expect(isNearListEnd({ ...base, scrollTop: 440 })).toBe(false);
    expect(isNearListEnd({ ...base, scrollTop: 450 })).toBe(true);
    expect(isNearListEnd({ ...base, scrollTop: 600 })).toBe(true);
    expect(isNearListEnd({ ...base, scrollTop: 0 })).toBe(false);
  });

  it('is true when the content does not fill the viewport (no scrollbar)', () => {
    expect(isNearListEnd({ scrollHeight: 200, clientHeight: 400, scrollTop: 0 })).toBe(true);
  });

  it('honours a custom threshold', () => {
    expect(isNearListEnd({ scrollHeight: 1000, clientHeight: 400, scrollTop: 570 }, 250)).toBe(true);
    expect(isNearListEnd({ scrollHeight: 1000, clientHeight: 400, scrollTop: 0 }, 40)).toBe(false);
  });
});
