/**
 * Time-of-day greetings & tips for the new-session landing.
 *
 * Source-of-truth text lives in the i18n locale files (zh.json / en.json) under
 * `newChatLanding`. This module picks a stable item per (period, seedKey) so
 * the same sessionId does not flicker between renders.
 */

import i18n from '../i18n';

export type DayPeriod = 'morning' | 'afternoon' | 'evening' | 'lateNight';

export function getDayPeriod(date = new Date()): DayPeriod {
  const h = date.getHours();
  if (h >= 5 && h < 11) return 'morning';
  if (h >= 11 && h < 17) return 'afternoon';
  if (h >= 17 && h < 22) return 'evening';
  return 'lateNight';
}

type GreetingSets = Record<DayPeriod, string[]>;

function readLocalized(lang: string): { greetings: GreetingSets; tips: string[] } {
  // Always re-read from i18n so live language switches pick up new strings.
  // `returnObjects: true` returns arrays for pluralized keys.
  const t = i18n.getFixedT(lang);
  const read = <T,>(key: string, fallback: T): T => {
    const v = t(key, { returnObjects: true }) as unknown;
    if (Array.isArray(v) && (v as unknown[]).length) return v as T;
    return fallback;
  };
  const greetings = read<GreetingSets>('newChatLanding', {
    morning: [],
    afternoon: [],
    evening: [],
    lateNight: [],
  });
  const tips = read<string[]>('newChatLanding.tips', []);
  return { greetings, tips };
}

function pickRandom<T>(items: T[], seed?: number): T {
  if (!items || items.length === 0) {
    // Last-resort fallback so the landing never crashes on missing locale data.
    return '' as unknown as T;
  }
  if (seed == null) return items[Math.floor(Math.random() * items.length)]!;
  const i = Math.abs(seed) % items.length;
  return items[i]!;
}

/** Stable-ish pick for a session: same sessionId → same greeting until period changes. */
export function pickGreeting(period: DayPeriod, seedKey?: string): string {
  const lang = i18n.language || 'zh';
  const { greetings } = readLocalized(lang);
  const list = greetings?.[period] || [];
  if (!seedKey) return pickRandom(list);
  let h = 0;
  for (let i = 0; i < seedKey.length; i++) h = (h * 31 + seedKey.charCodeAt(i)) | 0;
  return pickRandom(list, h + period.length * 17);
}

export function pickTip(seedKey?: string): string {
  const lang = i18n.language || 'zh';
  const { tips } = readLocalized(lang);
  if (!seedKey) return pickRandom(tips);
  let h = 0;
  for (let i = 0; i < seedKey.length; i++) h = (h * 17 + seedKey.charCodeAt(i)) | 0;
  return pickRandom(tips, h);
}
