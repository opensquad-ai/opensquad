/**
 * ModePicker — Cursor-style Plan / Build selector for the composer toolbar.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';

export type AgentMode = 'plan' | 'build';

const MODES: { id: AgentMode; label: string; hint: string }[] = [
  { id: 'build', label: 'Build', hint: 'Edit files & run shell' },
  { id: 'plan', label: 'Plan', hint: 'Read-only explore & plan' },
];

interface ModePickerProps {
  mode: AgentMode;
  disabled?: boolean;
  onSelect: (mode: AgentMode) => void;
}

export const ModePicker: React.FC<ModePickerProps> = ({
  mode,
  disabled = false,
  onSelect,
}) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const current = MODES.find((m) => m.id === mode) || MODES[0];

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
        className="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[12px] font-medium text-textMain bg-black/[0.05] dark:bg-white/[0.08] hover:bg-black/[0.08] dark:hover:bg-white/[0.12] transition-colors disabled:opacity-50 disabled:cursor-not-allowed border-0 cursor-pointer"
        title={current.hint}
      >
        <span>{current.label}</span>
        <ChevronDown
          size={13}
          className={`shrink-0 opacity-60 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div
          className="absolute bottom-[calc(100%+8px)] left-0 z-50 w-[200px] rounded-xl border border-border bg-panel shadow-[0_8px_30px_rgba(0,0,0,0.12)] overflow-hidden"
          role="listbox"
        >
          <div className="py-1">
            {MODES.map((m) => {
              const selected = m.id === mode;
              return (
                <button
                  key={m.id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    if (m.id !== mode) onSelect(m.id);
                    setOpen(false);
                  }}
                  className={`w-full flex items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors border-0 cursor-pointer ${
                    selected
                      ? 'bg-primary/10 text-primary'
                      : 'bg-transparent text-textMain hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="font-medium">{m.label}</div>
                    <div className="text-[10px] text-textMuted truncate">{m.hint}</div>
                  </div>
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
