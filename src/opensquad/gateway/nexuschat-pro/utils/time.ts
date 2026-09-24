export const formatTime = (timestamp: number, t: any): string => {
  const date = new Date(timestamp);
  const now = new Date();

  // Today: HH:mm
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  }

  // Yesterday: 昨天
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) {
    return t('common.yesterday');
  }

  // Within this year: MM/DD
  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}/${date.getDate()}`;
  }

  // Older: YYYY/MM/DD
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
};

/** Local wall-clock HH:mm for chat bubbles (never UTC). */
export function formatLocalClock(
  input: string | number | Date | null | undefined,
  opts?: { locale?: string },
): string {
  const ts = parseTimestampMs(input);
  if (!Number.isFinite(ts)) return '';
  return new Date(ts).toLocaleTimeString(opts?.locale || undefined, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

/**
 * Parse session/API timestamps to epoch ms.
 *
 * Storage convention is UTC. Naive ISO strings (no `Z` / offset) are ambiguous:
 * - new code / utcnow(): wall clock is UTC
 * - legacy datetime.now(): wall clock is local
 * Prefer UTC, but if that lands in the future, fall back to local parse.
 */
export function parseTimestampMs(
  input: string | number | Date | null | undefined,
  opts?: { now?: number },
): number {
  if (input == null || input === '') return NaN;
  if (typeof input === 'number') {
    // Heuristic: values below 1e12 are seconds.
    return input > 0 && input < 1e12 ? input * 1000 : input;
  }
  if (input instanceof Date) return input.getTime();

  const raw = String(input).trim();
  if (!raw) return NaN;

  // Already has timezone → trust Date.parse
  if (/([zZ]|[+-]\d{2}:?\d{2})$/.test(raw)) {
    return Date.parse(raw);
  }

  // Normalize "YYYY-MM-DD HH:mm:ss" → ISO-ish
  const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
  const asUtc = Date.parse(/[zZ]$/.test(normalized) ? normalized : `${normalized}Z`);
  const asLocal = Date.parse(normalized);
  const now = opts?.now ?? Date.now();

  if (!Number.isFinite(asUtc) && !Number.isFinite(asLocal)) return NaN;
  if (!Number.isFinite(asUtc)) return asLocal;
  if (!Number.isFinite(asLocal)) return asUtc;

  // UTC reading in the future ⇒ legacy local wall-clock without offset
  if (asUtc > now + 60_000) return asLocal;
  return asUtc;
}

/**
 * Cursor-style relative age for session list badges.
 * Units: 分钟 / 小时 / 天 / 个月 (or compact en: m / h / d / mo).
 */
export function formatRelativeAge(
  input: string | number | Date | null | undefined,
  opts?: { locale?: 'zh' | 'en'; now?: number },
): string {
  if (input == null || input === '') return '';
  const now = opts?.now ?? Date.now();
  const ts = parseTimestampMs(input, { now });
  if (!Number.isFinite(ts)) return '';

  const locale = opts?.locale ?? 'zh';
  const diffMs = Math.max(0, now - ts);
  const mins = Math.floor(diffMs / 60000);
  const hours = Math.floor(diffMs / 3600000);
  const days = Math.floor(diffMs / 86400000);
  const months = Math.floor(days / 30);

  if (locale === 'en') {
    if (mins < 1) return 'now';
    if (mins < 60) return `${Math.max(1, mins)}m`;
    if (hours < 24) return `${hours}h`;
    if (days < 30) return `${days}d`;
    return `${Math.max(1, months)}mo`;
  }

  if (mins < 1) return '刚刚';
  if (mins < 60) return `${Math.max(1, mins)}分钟`;
  if (hours < 24) return `${hours}小时`;
  if (days < 30) return `${days}天`;
  return `${Math.max(1, months)}个月`;
}

/** Local wall-clock day key `YYYY-MM-DD` ('' when the timestamp is unparseable). */
export function localDayKey(
  input: string | number | Date | null | undefined,
  opts?: { now?: number },
): string {
  const ts = parseTimestampMs(input, opts);
  if (!Number.isFinite(ts)) return '';
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/**
 * Header label for one calendar day: 今天 / 昨天 / M/D, and YYYY/M/D once the
 * year differs. Distinct from `formatTime`, which names the *moment* for
 * today's rows — a group header has to name the day.
 */
export function formatDayLabel(
  input: string | number | Date | null | undefined,
  opts?: { locale?: 'zh' | 'en'; now?: number },
): string {
  const ts = parseTimestampMs(input, opts);
  if (!Number.isFinite(ts)) return '';
  const locale = opts?.locale ?? 'zh';
  const now = opts?.now ?? Date.now();
  const key = localDayKey(ts);
  if (key === localDayKey(now)) return locale === 'en' ? 'Today' : '今天';
  if (key === localDayKey(now - 86400000)) return locale === 'en' ? 'Yesterday' : '昨天';
  const d = new Date(ts);
  const month = d.getMonth() + 1;
  const day = d.getDate();
  if (d.getFullYear() === new Date(now).getFullYear()) return `${month}/${day}`;
  return `${d.getFullYear()}/${month}/${day}`;
}

/**
 * Group a list into local-calendar-day buckets, in the order the days first
 * appear in `items` (callers pass newest-first, so today lands on top).
 *
 * The day key doubles as group identity, so a day whose items are not
 * contiguous still forms one group. Items with an unparseable timestamp share
 * a trailing '' group — they must never vanish from the list.
 */
export function groupByLocalDay<T>(
  items: readonly T[],
  getTimestamp: (item: T) => string | number | Date | null | undefined,
  opts?: { now?: number },
): Array<{ key: string; items: T[] }> {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = localDayKey(getTimestamp(item), opts);
    const bucket = groups.get(key);
    if (bucket) bucket.push(item);
    else groups.set(key, [item]);
  }
  return [...groups.entries()].map(([key, grouped]) => ({ key, items: grouped }));
}
