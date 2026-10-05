import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, Globe } from 'lucide-react';
import { setLanguage } from '../../i18n';

interface OnboardingTourProps {
  /** Skipping and finishing are the same exit: the caller learns the language to apply. */
  onFinish: (lang: 'zh' | 'en') => void;
}

/**
 * First-launch tour — the introduction a fresh install opens with.
 *
 * Content lives in the locale files (``wizard.tour*``), not in this component, so the language
 * switcher at the bottom re-renders every slide the moment it changes: no reload, no second copy of
 * the copy. The switcher is the same ``setLanguage`` the rest of the app uses, so the choice sticks
 * for the session behind it too.
 */
export const OnboardingTour: React.FC<OnboardingTourProps> = ({ onFinish }) => {
  const { t, i18n } = useTranslation();
  const [slide, setSlide] = useState(0);

  const slides = useMemo(
    () =>
      [1, 2, 3, 4].map((n) => ({
        title: t(`wizard.tour${n}Title`, { defaultValue: '' }),
        body: t(`wizard.tour${n}Body`, { defaultValue: '' }),
      })),
    [t],
  );
  const last = slides.length - 1;
  const current = Math.min(slide, last);

  const lang: 'zh' | 'en' = i18n.language?.startsWith('en') ? 'en' : 'zh';

  const go = useCallback(
    (next: number) => setSlide(Math.max(0, Math.min(last, next))),
    [last],
  );

  const finish = useCallback(() => onFinish(lang), [onFinish, lang]);

  // Left/right arrows and Escape follow the on-screen buttons, so the tour is
  // usable the way a wizard screen is expected to be.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') go(current + 1);
      else if (e.key === 'ArrowLeft') go(current - 1);
      else if (e.key === 'Escape') finish();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [current, go, finish]);

  return (
    <div
      className="h-full w-full bg-bgLight text-textMain flex flex-col overflow-hidden relative"
      data-testid="onboarding-tour"
    >
      {/* 跳过 — top right, as in every first-run tour. */}
      <div className="flex justify-end p-4">
        <button
          type="button"
          onClick={finish}
          data-testid="onboarding-skip"
          className="text-sm text-textMuted hover:text-textMain transition-colors px-3 py-1.5 rounded-lg hover:bg-primary/5"
        >
          {t('wizard.tourSkip', { defaultValue: '跳过' })}
        </button>
      </div>

      <div className="flex-1 min-h-0 flex flex-col items-center justify-center px-6 sm:px-10 text-center">
        <div className="flex items-center gap-3 mb-10">
          <img src="/logo.svg" alt="OpenSquad" className="w-11 h-11 drop-shadow-lg" />
          <span className="text-xl font-black tracking-[0.2em] uppercase text-textMain">
            OpenSquad
          </span>
        </div>

        <div className="w-full max-w-2xl min-h-[9.5rem]">
          <h1
            className="text-3xl sm:text-4xl font-black leading-snug mb-4"
            data-testid="onboarding-title"
          >
            {current === 0
              ? t('wizard.tour1Title', { defaultValue: '按你的方式，编排一支 AI 团队。' })
              : slides[current].title}
          </h1>
          <p className="text-base sm:text-lg text-textMuted leading-relaxed" data-testid="onboarding-body">
            {current === 0
              ? t('wizard.tour1Body', {
                  defaultValue: '本地优先：所有会话、文件与协作数据都留在你自己的机器上。',
                })
              : slides[current].body}
          </p>
        </div>

        <div className="flex items-center gap-2 mt-10" data-testid="onboarding-dots">
          {slides.map((_, i) => (
            <button
              key={i}
              type="button"
              aria-label={`${i + 1}`}
              onClick={() => go(i)}
              className={
                i === current
                  ? 'h-1.5 w-6 rounded-full bg-primary transition-all'
                  : 'h-1.5 w-1.5 rounded-full bg-border hover:bg-textMuted transition-all'
              }
            />
          ))}
        </div>
      </div>

      <div className="px-6 sm:px-10 pb-6 pt-2 flex items-center justify-between gap-4">
        <button
          type="button"
          onClick={() => go(current - 1)}
          disabled={current === 0}
          data-testid="onboarding-back"
          className="inline-flex items-center gap-1.5 px-4 py-2.5 rounded-full text-sm font-semibold text-textMuted hover:text-textMain hover:bg-primary/5 transition-all disabled:opacity-0"
        >
          <ChevronLeft size={16} />
          {t('wizard.tourBack', { defaultValue: '返回' })}
        </button>

        {/* The language switch sits at the bottom, exactly where the tour says it
            is: changing it re-renders the slide in place. */}
        <div className="flex items-center gap-2">
          <Globe size={14} className="text-textMuted" />
          <select
            value={lang}
            onChange={(e) => setLanguage(e.target.value as 'zh' | 'en')}
            data-testid="onboarding-language"
            aria-label={t('wizard.tourLanguage', { defaultValue: '语言' })}
            className="bg-transparent text-sm text-textMuted hover:text-textMain focus:outline-none cursor-pointer"
          >
            <option value="zh">简体中文</option>
            <option value="en">English</option>
          </select>
        </div>

        {current === last ? (
          <button
            type="button"
            onClick={finish}
            data-testid="onboarding-done"
            className="inline-flex items-center gap-1.5 px-6 py-2.5 rounded-full text-sm font-semibold bg-primary text-white shadow-lg shadow-primary/25 hover:opacity-90 transition-all"
          >
            {t('wizard.tourDone', { defaultValue: '开始使用' })}
            <ChevronRight size={16} />
          </button>
        ) : (
          <button
            type="button"
            onClick={() => go(current + 1)}
            data-testid="onboarding-next"
            className="inline-flex items-center gap-1.5 px-6 py-2.5 rounded-full text-sm font-semibold bg-primary text-white shadow-lg shadow-primary/25 hover:opacity-90 transition-all"
          >
            {t('wizard.tourNext', { defaultValue: '下一步' })}
            <ChevronRight size={16} />
          </button>
        )}
      </div>
    </div>
  );
};

export default OnboardingTour;
