import { describe, expect, it } from 'vitest';
import {
  formatDayLabel,
  formatRelativeAge,
  groupByLocalDay,
  parseTimestampMs,
} from './time';

describe('parseTimestampMs', () => {
  const now = Date.parse('2026-07-11T12:00:00Z'); // 20:00 Beijing

  it('parses explicit UTC with Z', () => {
    expect(parseTimestampMs('2026-07-11T11:30:00Z', { now })).toBe(Date.parse('2026-07-11T11:30:00Z'));
  });

  it('afternoon China time stored as naive UTC is not shown as morning', () => {
    // 13:03 CST = 05:03 UTC. Naive ISO must parse as UTC, not as local 05:03.
    expect(parseTimestampMs('2026-09-12T05:03:00', { now: Date.parse('2026-09-12T08:00:00Z') })).toBe(
      Date.parse('2026-09-12T05:03:00Z'),
    );
  });

  it('treats naive ISO as UTC when that is not in the future', () => {
    // utcnow()-style naive: 11:30 UTC → 30m ago
    expect(parseTimestampMs('2026-07-11T11:30:00.123456', { now })).toBe(
      Date.parse('2026-07-11T11:30:00.123456Z'),
    );
  });

  it('falls back to local parse when UTC reading is in the future (legacy local wall clock)', () => {
    // Beijing local 19:30 stored without offset while now is 12:00 UTC (20:00 Beijing).
    // Forcing +Z would put it 7.5h in the future — must use local instead.
    const localWall = '2026-07-11T19:30:00.000';
    const asLocal = Date.parse(localWall);
    expect(parseTimestampMs(localWall, { now })).toBe(asLocal);
  });
});

describe('formatRelativeAge', () => {
  const now = Date.parse('2026-07-11T16:00:00Z');

  it('formats chinese units', () => {
    expect(formatRelativeAge(now - 30_000, { locale: 'zh', now })).toBe('刚刚');
    expect(formatRelativeAge(now - 5 * 60_000, { locale: 'zh', now })).toBe('5分钟');
    expect(formatRelativeAge(now - 3 * 3600_000, { locale: 'zh', now })).toBe('3小时');
    expect(formatRelativeAge(now - 10 * 86400_000, { locale: 'zh', now })).toBe('10天');
    expect(formatRelativeAge(now - 60 * 86400_000, { locale: 'zh', now })).toBe('2个月');
  });

  it('formats compact english units', () => {
    expect(formatRelativeAge(now - 5 * 60_000, { locale: 'en', now })).toBe('5m');
    expect(formatRelativeAge(now - 3 * 3600_000, { locale: 'en', now })).toBe('3h');
    expect(formatRelativeAge(now - 10 * 86400_000, { locale: 'en', now })).toBe('10d');
    expect(formatRelativeAge(now - 60 * 86400_000, { locale: 'en', now })).toBe('2mo');
  });

  it('does not inflate age for naive UTC timestamps in China-like local TZ', () => {
    // Regression: Date.parse(naive) as local made UTC wall clocks look ~8h older.
    const ts = '2026-07-11T15:30:00.000000'; // meant to be UTC (30m before `now`)
    expect(formatRelativeAge(ts, { locale: 'zh', now })).toBe('30分钟');
  });
});

describe('formatDayLabel', () => {
  // Local wall clock — the day buckets are local by definition.
  const now = new Date(2026, 8, 23, 12, 0, 0).getTime(); // 2026-09-23 12:00
  const at = (y: number, m: number, d: number, hh = 9) =>
    new Date(y, m - 1, d, hh, 0, 0).getTime();

  it('names today and yesterday, then the date', () => {
    expect(formatDayLabel(at(2026, 9, 23), { now })).toBe('今天');
    expect(formatDayLabel(at(2026, 9, 22), { now })).toBe('昨天');
    expect(formatDayLabel(at(2026, 9, 21), { now })).toBe('9/21');
    // Across a year boundary the year has to be spelled out, or 12/31 sorts
    // next to 01/01 as if it were the same day.
    expect(formatDayLabel(at(2025, 12, 31), { now })).toBe('2025/12/31');
    expect(formatDayLabel(null, { now })).toBe('');
  });

  it('formats english labels', () => {
    expect(formatDayLabel(at(2026, 9, 23), { locale: 'en', now })).toBe('Today');
    expect(formatDayLabel(at(2026, 9, 22), { locale: 'en', now })).toBe('Yesterday');
    expect(formatDayLabel(at(2026, 9, 21), { locale: 'en', now })).toBe('9/21');
  });

  it('accepts the epoch-seconds shape scheduled executions carry', () => {
    // ScheduledExecution.started_at is seconds, unlike session JSON (ms).
    expect(formatDayLabel(Math.floor(at(2026, 9, 23) / 1000), { now })).toBe('今天');
  });
});

describe('groupByLocalDay', () => {
  const now = new Date(2026, 8, 23, 12, 0, 0).getTime();
  const at = (y: number, m: number, d: number, hh = 9) =>
    new Date(y, m - 1, d, hh, 0, 0).getTime();

  it('groups newest-first and merges a day whose items are not contiguous', () => {
    const items = [
      { id: 'a', started_at: at(2026, 9, 23, 9) },
      { id: 'b', started_at: at(2026, 9, 23, 8) },
      { id: 'c', started_at: at(2026, 9, 22, 9) },
      { id: 'd', started_at: at(2026, 9, 23, 7) },
    ];
    const groups = groupByLocalDay(items, (i) => i.started_at, { now });
    expect(groups.map((g) => g.key)).toEqual(['2026-09-23', '2026-09-22']);
    expect(groups[0].items.map((i) => i.id)).toEqual(['a', 'b', 'd']);
    expect(groups[1].items.map((i) => i.id)).toEqual(['c']);
  });

  it('keeps items with an unusable timestamp instead of dropping them', () => {
    const groups = groupByLocalDay(
      [{ id: 'x', started_at: null }, { id: 'y', started_at: at(2026, 9, 22) }],
      (i) => i.started_at,
      { now },
    );
    expect(groups.map((g) => g.key)).toEqual(['', '2026-09-22']);
    expect(groups.flatMap((g) => g.items).map((i) => i.id)).toEqual(['x', 'y']);
  });

  it('returns nothing for an empty list', () => {
    expect(groupByLocalDay([], (i: { started_at: number }) => i.started_at)).toEqual([]);
  });
});
