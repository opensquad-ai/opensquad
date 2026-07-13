import { describe, expect, it } from 'vitest';
import { formatElapsed, formatElapsedAtLeastOneSecond } from './formatElapsed';

describe('formatElapsed', () => {
  it('formats seconds only under a minute', () => {
    expect(formatElapsed(5_000)).toBe('5s');
    expect(formatElapsed(59_000)).toBe('59s');
  });

  it('formats minutes and seconds', () => {
    expect(formatElapsed(198_000)).toBe('3m 18s');
    expect(formatElapsed(60_000)).toBe('1m');
  });

  it('formats hours and days', () => {
    expect(formatElapsed(3_661_000)).toBe('1h 1m 1s');
    expect(formatElapsed(90_000_000)).toBe('1d 1h');
  });

  it('floors sub-second positive durations to 1s when requested', () => {
    expect(formatElapsedAtLeastOneSecond(200)).toBe('1s');
    expect(formatElapsedAtLeastOneSecond(0)).toBe('0s');
  });
});
