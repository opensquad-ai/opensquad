/**
 * Formatting helpers for the per-round token usage badge on assistant
 * messages (消耗 badge: "2m 5s · 12.3K", popover: input/output split)
 * and the full-timestamp tooltip on the message time.
 *
 * Pure functions — no i18n runtime dependency; the timestamp formatter
 * takes a locale so callers pass the active UI language.
 */

/** "8,192" — thousands separators, for the popover detail lines. */
export function formatTokenExact(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '0';
  return Math.round(n).toLocaleString('en-US');
}

/** "812" / "12.3K" / "3.05M" / "1.2B" — compact badge form. */
export function formatTokenCount(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0';
  const compact = (value: number, suffix: string): string => {
    // One decimal max; strip a trailing ".0" (12.0K → 12K).
    const rounded = value >= 100 ? Math.round(value) : Math.round(value * 10) / 10;
    const text = rounded % 1 === 0 ? String(rounded) : rounded.toFixed(1);
    return `${text}${suffix}`;
  };
  if (n < 1000) return String(Math.round(n));
  if (n < 1_000_000) return compact(n / 1000, 'K');
  if (n < 1_000_000_000) return compact(n / 1_000_000, 'M');
  return compact(n / 1_000_000_000, 'B');
}

/** "45s" / "9m 34s" / "1h 2m 3s" — badge duration form. */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0s';
  const total = Math.round(ms / 1000);
  if (total < 60) return `${total}s`;
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  if (mins < 60) return `${mins}m ${secs}s`;
  const hours = Math.floor(mins / 60);
  return `${hours}h ${mins % 60}m ${secs}s`;
}

/**
 * "2026年9月13日周日 22:48:13" (zh) / "Sun, Sep 13, 2026 22:48:13" (en).
 * Built from formatToParts so the shape is identical across ICU builds.
 */
export function formatFullTimestamp(input: string | number | Date, locale: string = 'zh'): string {
  const d = input instanceof Date ? input : new Date(input);
  if (Number.isNaN(d.getTime())) return '';
  const fmt = new Intl.DateTimeFormat(locale.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US', {
    year: 'numeric',
    month: locale.toLowerCase().startsWith('zh') ? 'numeric' : 'short',
    day: 'numeric',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  });
  const parts = fmt.formatToParts(d);
  const get = (type: string): string => parts.find((p) => p.type === type)?.value ?? '';
  const time = `${get('hour')}:${get('minute')}:${get('second')}`;
  if (locale.toLowerCase().startsWith('zh')) {
    return `${get('year')}年${get('month')}月${get('day')}日${get('weekday')} ${time}`;
  }
  return `${get('weekday')}, ${get('month')} ${get('day')}, ${get('year')} ${time}`;
}
