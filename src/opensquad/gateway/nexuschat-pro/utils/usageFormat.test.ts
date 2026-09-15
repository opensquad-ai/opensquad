// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { formatDuration, formatFullTimestamp, formatTokenCount, formatTokenExact } from './usageFormat';

describe('formatTokenCount', () => {
  it('keeps small counts as plain integers', () => {
    expect(formatTokenCount(0)).toBe('0');
    expect(formatTokenCount(812)).toBe('812');
  });

  it('compacts thousands with one decimal and strips trailing .0', () => {
    expect(formatTokenCount(1_000)).toBe('1K');
    expect(formatTokenCount(12_340)).toBe('12.3K');
    expect(formatTokenCount(999_999)).toBe('1000K');
  });

  it('compacts millions and billions', () => {
    expect(formatTokenCount(3_000_000)).toBe('3M');
    expect(formatTokenCount(3_140_000)).toBe('3.1M');
    expect(formatTokenCount(1_000_000)).toBe('1M');
    expect(formatTokenCount(2_400_000_000)).toBe('2.4B');
  });

  it('is defensive against junk input', () => {
    expect(formatTokenCount(-5)).toBe('0');
    expect(formatTokenCount(NaN)).toBe('0');
    expect(formatTokenCount(Infinity)).toBe('0');
  });
});

describe('formatTokenExact', () => {
  it('adds thousands separators for the popover detail', () => {
    expect(formatTokenExact(8_192)).toBe('8,192');
    expect(formatTokenExact(1_234_567)).toBe('1,234,567');
  });
});

describe('formatDuration', () => {
  it('renders sub-minute as seconds', () => {
    expect(formatDuration(45_000)).toBe('45s');
    expect(formatDuration(0)).toBe('0s');
  });

  it('renders minutes + seconds like the reference UI', () => {
    expect(formatDuration(574_000)).toBe('9m 34s');
  });

  it('renders hours for long turns', () => {
    expect(formatDuration(3_723_000)).toBe('1h 2m 3s');
  });
});

describe('formatFullTimestamp', () => {
  const ts = new Date(2026, 7, 9, 22, 48, 13).getTime(); // 2026-08-09 22:48:13 local (Sunday)

  it('renders the zh CN full date shown in the reference popover', () => {
    const out = formatFullTimestamp(ts, 'zh');
    expect(out).toBe('2026年8月9日周日 22:48:13');
  });

  it('renders an english variant', () => {
    const out = formatFullTimestamp(ts, 'en');
    expect(out).toContain('2026');
    expect(out).toContain('22:48:13');
  });

  it('returns empty for unparseable input', () => {
    expect(formatFullTimestamp('not a date', 'zh')).toBe('');
  });
});
