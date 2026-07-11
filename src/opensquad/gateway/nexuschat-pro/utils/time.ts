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
 * Cursor-style relative age for session list badges.
 * Units: 分钟 / 小时 / 天 / 个月 (or compact en: m / h / d / mo).
 */
export function formatRelativeAge(
  input: string | number | Date | null | undefined,
  opts?: { locale?: 'zh' | 'en'; now?: number },
): string {
  if (input == null || input === '') return '';
  const ts =
    typeof input === 'number'
      ? input
      : input instanceof Date
        ? input.getTime()
        : Date.parse(String(input));
  if (!Number.isFinite(ts)) return '';

  const now = opts?.now ?? Date.now();
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
