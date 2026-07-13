/**
 * Format elapsed milliseconds as compact human units: s / m / h / d.
 * Examples: 5s · 3m 18s · 1h 2m · 2d 3h 5m
 */
export function formatElapsed(ms: number): string {
  const totalSec = Math.max(0, Math.round(ms / 1000));
  if (totalSec < 1) return '0s';

  const d = Math.floor(totalSec / 86400);
  const h = Math.floor((totalSec % 86400) / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;

  const parts: string[] = [];
  if (d > 0) parts.push(`${d}d`);
  if (h > 0) parts.push(`${h}h`);
  if (m > 0) parts.push(`${m}m`);
  // Omit trailing 0s when a larger unit is already present.
  if (s > 0 || parts.length === 0) parts.push(`${s}s`);
  return parts.join(' ');
}

/** Like formatElapsed but never returns empty; floors to at least 1s when ms > 0. */
export function formatElapsedAtLeastOneSecond(ms: number): string {
  if (ms > 0 && ms < 1000) return '1s';
  return formatElapsed(ms);
}
