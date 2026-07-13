/**
 * EffortPicker — Cursor-style Low / Medium / High reasoning depth.
 * DeepSeek models only expose high|max at the API — show two options (High / Max).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';

export type ReasoningEffort = 'low' | 'medium' | 'high';

const STANDARD_LEVELS: { id: ReasoningEffort; label: string }[] = [
  { id: 'low', label: 'Low' },
  { id: 'medium', label: 'Medium' },
  { id: 'high', label: 'High' },
];

/** DeepSeek maps UI High→API high (medium), UI Max→API max (high). */
const DEEPSEEK_LEVELS: { id: ReasoningEffort; label: string }[] = [
  { id: 'medium', label: 'High' },
  { id: 'high', label: 'Max' },
];

function normalizeDeepseekEffort(effort: ReasoningEffort): ReasoningEffort {
  return effort === 'high' ? 'high' : 'medium';
}

interface EffortPickerProps {
  effort: ReasoningEffort;
  disabled?: boolean;
  /** DeepSeek maps low/medium→high, high→max — show API-facing labels */
  deepseekStyle?: boolean;
  onSelect: (effort: ReasoningEffort) => void;
}

export const EffortPicker: React.FC<EffortPickerProps> = ({
  effort,
  disabled = false,
  deepseekStyle = false,
  onSelect,
}) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const levels = deepseekStyle ? DEEPSEEK_LEVELS : STANDARD_LEVELS;
  const activeEffort = deepseekStyle ? normalizeDeepseekEffort(effort) : effort;
  const current = useMemo(
    () => levels.find((l) => l.id === activeEffort) || levels[levels.length - 1],
    [levels, activeEffort],
  );

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded-lg px-1.5 py-1 text-[12px] text-textMuted hover:text-textMain hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors disabled:opacity-50 disabled:cursor-not-allowed border-0 bg-transparent cursor-pointer"
        title="Reasoning effort"
      >
        <span className="font-medium tabular-nums">{current.label}</span>
        <ChevronDown
          size={13}
          className={`shrink-0 opacity-60 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div
          className="absolute bottom-[calc(100%+8px)] left-0 z-50 w-[180px] rounded-xl border border-border bg-panel shadow-[0_8px_30px_rgba(0,0,0,0.12)] overflow-hidden"
          role="listbox"
        >
          <div className="px-3 pt-2.5 pb-1 text-[10px] font-medium uppercase tracking-wide text-textMuted/55">
            Effort
          </div>
          <div className="py-1">
            {levels.map((level) => {
              const selected = level.id === activeEffort;
              return (
                <button
                  key={level.id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    if (level.id !== activeEffort) onSelect(level.id);
                    setOpen(false);
                  }}
                  className={`w-full flex items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors border-0 cursor-pointer ${
                    selected
                      ? 'bg-primary/10 text-primary'
                      : 'bg-transparent text-textMain hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'
                  }`}
                >
                  <span className="flex-1 font-medium">{level.label}</span>
                  {selected ? <Check size={14} className="shrink-0 text-primary" /> : null}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
