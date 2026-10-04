// @vitest-environment jsdom
/**
 * The landing hero's greeting must actually contain text.
 *
 * Reported: creating a new session showed the tip line but not the big greeting above it. The
 * component renders its <h1> unconditionally, so an empty title is simply invisible — and the
 * title was always empty, because the greetings were looked up as one object while the reader only
 * accepted arrays, so every period fell back to an empty list. The tip stayed because it is read as
 * a plain array. These tests read the real locale files, the way the app does.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

import i18n from '../i18n';
import { getDayPeriod, pickGreeting, pickTip } from './newChatGreetings';

const PERIODS = ['morning', 'afternoon', 'evening', 'lateNight'] as const;

describe('the landing greeting', () => {
  it('has text for every period of the day, in both languages', async () => {
    for (const lang of ['zh', 'en']) {
      await i18n.changeLanguage(lang);
      for (const period of PERIODS) {
        const title = pickGreeting(period, `seed-${lang}-${period}`);
        expect(title.trim(), `${lang} / ${period}`).not.toBe('');
      }
    }
  });

  it('is stable for a session, and the period is decided by the clock', () => {
    expect(pickGreeting('lateNight', 'session-1')).toBe(pickGreeting('lateNight', 'session-1'));
    expect(getDayPeriod(new Date(2026, 9, 4, 23, 30))).toBe('lateNight');
    expect(getDayPeriod(new Date(2026, 9, 4, 9, 0))).toBe('morning');
    expect(getDayPeriod(new Date(2026, 9, 4, 13, 0))).toBe('afternoon');
    expect(getDayPeriod(new Date(2026, 9, 4, 19, 0))).toBe('evening');
  });

  it('still has a tip, which is the line that never disappeared', async () => {
    await i18n.changeLanguage('zh');

    expect(pickTip('session-1').trim()).not.toBe('');
  });

  it('asks for each period by its own key, not for the parent object', () => {
    const src = fs.readFileSync(path.resolve(__dirname, 'newChatGreetings.ts'), 'utf8');

    for (const period of PERIODS) {
      expect(src).toContain(`'newChatLanding.${period}'`);
    }
  });
});
