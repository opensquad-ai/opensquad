// @vitest-environment jsdom
/**
 * The first-launch tour.
 *
 * Driven, not scanned: the tour must walk four slides, go back, jump by dot, report the language
 * once on skip or finish — and above all re-render every slide in place when the switch at the
 * bottom changes language, which is what "内容可以随时根据底部的中英语言切换" asks for. That last one
 * only works because the copy lives in the locale files, so the parity check below is part of the
 * same promise: a missing English key would simply show Chinese text.
 *
 * No @testing-library dependency and no JSX: this repo carries neither a DOM-testing library nor a
 * .tsx test include pattern, and a feature test is not a reason to change either. react 19 exports
 * `act`, jsdom is already here, and React.createElement is enough.
 */
import fs from 'fs';
import path from 'path';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

import i18n from '../../i18n';
import { OnboardingTour } from './OnboardingTour';

const ROOT = path.resolve(__dirname, '..', '..');
const zh = JSON.parse(fs.readFileSync(path.join(ROOT, 'locales/zh.json'), 'utf8'));
const en = JSON.parse(fs.readFileSync(path.join(ROOT, 'locales/en.json'), 'utf8'));

const CONTROLS = ['tourSkip', 'tourBack', 'tourNext', 'tourDone', 'tourLanguage'];
const SLIDES = [1, 2, 3, 4].flatMap((n) => [`tour${n}Title`, `tour${n}Body`]);

let container: HTMLDivElement;
let root: Root;

function mount(onFinish: (lang: 'zh' | 'en') => void): void {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(React.createElement(OnboardingTour, { onFinish }));
  });
}

function el(testid: string): HTMLElement {
  const found = container.querySelector(`[data-testid="${testid}"]`);
  if (!found) throw new Error(`no element for ${testid}`);
  return found as HTMLElement;
}

function click(testid: string): void {
  act(() => {
    el(testid).dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

/** Select needs the native setter, otherwise React's value tracker swallows the change. */
async function pickLanguage(value: 'zh' | 'en'): Promise<void> {
  const select = el('onboarding-language') as HTMLSelectElement;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
    setter?.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

const title = () => el('onboarding-title').textContent;

afterEach(async () => {
  // The parity test never mounts, so there may be no root to unmount.
  if (root) act(() => root.unmount());
  container?.remove();
  await i18n.changeLanguage('zh');
});

describe('first-launch tour', () => {
  it('has every key in both languages', () => {
    for (const key of [...CONTROLS, ...SLIDES]) {
      expect(zh.wizard[key], `zh.wizard.${key}`).toBeTruthy();
      expect(en.wizard[key], `en.wizard.${key}`).toBeTruthy();
      expect(en.wizard[key]).not.toBe(zh.wizard[key]);
    }
  });

  it('walks four slides and reports the language once on finish', () => {
    const onFinish = vi.fn();
    mount(onFinish);
    expect(title()).toBe(zh.wizard.tour1Title);

    for (let n = 2; n <= 4; n++) {
      click('onboarding-next');
      expect(title()).toBe(zh.wizard[`tour${n}Title`]);
    }

    expect(container.querySelector('[data-testid="onboarding-next"]')).toBeNull();
    click('onboarding-done');
    expect(onFinish).toHaveBeenCalledTimes(1);
    expect(onFinish).toHaveBeenCalledWith('zh');
  });

  it('goes back and jumps by dot', () => {
    mount(() => undefined);
    click('onboarding-next');
    click('onboarding-back');
    expect(title()).toBe(zh.wizard.tour1Title);

    const dots = el('onboarding-dots').querySelectorAll('button');
    expect(dots).toHaveLength(4);
    act(() => {
      (dots[2] as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(title()).toBe(zh.wizard.tour3Title);
  });

  it('skipping still reports the language', () => {
    const onFinish = vi.fn();
    mount(onFinish);
    click('onboarding-skip');
    expect(onFinish).toHaveBeenCalledWith('zh');
  });

  it('re-renders the same slide in English when the switch changes', async () => {
    mount(() => undefined);
    click('onboarding-next');
    expect(title()).toBe(zh.wizard.tour2Title);

    await pickLanguage('en');
    expect(title()).toBe(en.wizard.tour2Title);
    expect(el('onboarding-next').textContent).toContain(en.wizard.tourNext);
    expect(el('onboarding-skip').textContent).toContain(en.wizard.tourSkip);

    await pickLanguage('zh');
    expect(title()).toBe(zh.wizard.tour2Title);
  });
});
