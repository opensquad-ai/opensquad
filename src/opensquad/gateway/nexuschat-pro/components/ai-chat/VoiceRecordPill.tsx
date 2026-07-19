/**
 * VoiceRecordPill — active recording / dictating indicator for the composer.
 * Red pill + animated waveform bars + M:SS timer (matches reference voice UI).
 */
import React, { useEffect, useState } from 'react';

export interface VoiceRecordPillProps {
  /** Elapsed recording seconds */
  durationSec: number;
  /** Mic level 0–1 for live bar heights; falls back to idle pulse when omitted */
  level?: number;
  /** ASR / upload in progress after stop */
  dictating?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  title?: string;
  className?: string;
}

function formatMmSs(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, '0')}`;
}

const BAR_COUNT = 3;

export const VoiceRecordPill: React.FC<VoiceRecordPillProps> = ({
  durationSec,
  level = 0,
  dictating = false,
  disabled = false,
  onClick,
  title,
  className = '',
}) => {
  // Soft idle animation when level is flat (permissions / silence)
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (dictating) return;
    const id = window.setInterval(() => setTick((t) => t + 1), 120);
    return () => window.clearInterval(id);
  }, [dictating]);

  const clamped = Math.max(0, Math.min(1, level));
  const bars = Array.from({ length: BAR_COUNT }, (_, i) => {
    if (dictating) return 0.35 + (i === 1 ? 0.25 : 0);
    const phase = (tick + i * 2) % 8;
    const pulse = 0.35 + 0.55 * (0.5 + 0.5 * Math.sin((phase / 8) * Math.PI * 2));
    const live = 0.22 + clamped * (0.55 + (i === 1 ? 0.23 : 0.08));
    // Blend analyser level with a light pulse so bars always feel alive
    return Math.min(1, live * 0.75 + pulse * 0.35 * (0.4 + clamped * 0.6));
  });

  if (dictating) {
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={onClick}
        title={title || '正在转写…'}
        className={`inline-flex items-center gap-2 h-8 px-3 rounded-full bg-red-500/90 text-white text-[12px] font-medium border-0 cursor-default disabled:opacity-60 ${className}`}
      >
        <span className="w-3.5 h-3.5 border-2 border-white/80 border-t-transparent rounded-full animate-spin shrink-0" />
        <span className="tabular-nums tracking-wide">转写中</span>
      </button>
    );
  }

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={title || '点击停止并转写'}
      className={`inline-flex items-center gap-2 h-8 pl-2.5 pr-3 rounded-full bg-red-500 hover:bg-red-600 text-white shadow-[0_2px_8px_rgba(239,68,68,0.35)] border-0 cursor-pointer transition-colors disabled:opacity-60 ${className}`}
    >
      <span className="flex items-end gap-[2px] h-3.5 w-[14px] shrink-0" aria-hidden>
        {bars.map((h, i) => (
          <span
            key={i}
            className="w-[3px] rounded-full bg-white origin-bottom transition-[height] duration-100 ease-out"
            style={{ height: `${Math.round(h * 100)}%`, minHeight: 3 }}
          />
        ))}
      </span>
      <span className="tabular-nums text-[13px] font-semibold tracking-wide leading-none">
        {formatMmSs(durationSec)}
      </span>
    </button>
  );
};
