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
