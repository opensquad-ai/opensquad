import { describe, expect, it } from 'vitest';
import { formatRelativeAge, parseTimestampMs } from './time';

describe('parseTimestampMs', () => {
  const now = Date.parse('2026-07-11T12:00:00Z'); // 20:00 Beijing

  it('parses explicit UTC with Z', () => {
    expect(parseTimestampMs('2026-07-11T11:30:00Z', { now })).toBe(Date.parse('2026-07-11T11:30:00Z'));
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
