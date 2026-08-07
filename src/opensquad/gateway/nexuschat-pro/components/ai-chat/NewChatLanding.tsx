/**
 * New-session hero: soft tip + time-of-day greeting above the centered composer.
 */
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb } from 'lucide-react';
import { getDayPeriod, pickGreeting, pickTip } from '../../utils/newChatGreetings';

export interface NewChatLandingProps {
  /** Stable seed so title/tip don't flicker while landing is open */
  seedKey?: string;
  className?: string;
}

export const NewChatLanding: React.FC<NewChatLandingProps> = ({ seedKey, className = '' }) => {
  const { i18n } = useTranslation();
  // Re-pick greeting/tip when language switches so Chinese ↔ English swap immediately.
  const langKey = i18n.language || 'zh';
  const period = useMemo(() => getDayPeriod(), [seedKey, langKey]);
  const tip = useMemo(() => pickTip(seedKey), [seedKey, langKey]);
  const title = useMemo(() => pickGreeting(period, seedKey), [period, seedKey, langKey]);

  return (
    <div
      className={`os-new-chat-landing flex flex-col items-center text-center px-4 select-none ${className}`}
      aria-hidden={false}
    >
      <p className="flex items-center justify-center gap-1.5 max-w-xl text-[12px] leading-relaxed text-textMuted/80 mb-4 sm:mb-5">
        <Lightbulb size={13} className="shrink-0 opacity-70" strokeWidth={1.75} />
        <span className="min-w-0">{tip}</span>
      </p>
      <h1 className="max-w-2xl text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight text-textMain leading-snug">
        {title}
      </h1>
    </div>
  );
};
