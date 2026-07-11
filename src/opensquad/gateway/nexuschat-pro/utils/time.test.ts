import { describe, expect, it } from 'vitest';
import { formatRelativeAge } from './time';

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
});
