/**
 * SoloModelPicker — Cursor-style model selector for Solo composer.
 * Shows current model; opens an upward popover with search + card list
 * (same source as header select) and "Add Models" → Models page.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, Plus, Search } from 'lucide-react';
import type { ModelCardInfo } from '../../services/api';

interface SoloModelPickerProps {
  cards: ModelCardInfo[];
  currentCardName: string | null;
  modelName: string | null;
  fallbackLabel?: string;
  switching?: boolean;
  disabled?: boolean;
  onSelect: (cardName: string) => void;
  onAddModels: () => void;
}

function resolveSelectedName(
  cards: ModelCardInfo[],
  currentCardName: string | null,
  modelName: string | null,
): string {
  if (currentCardName && cards.some((c) => c.name === currentCardName)) {
    return currentCardName;
  }
  if (modelName) {
    const byModel = cards.find((c) => c.model_name === modelName);
    if (byModel) return byModel.name;
  }
  return '';
}

function displayLabel(
  cards: ModelCardInfo[],
  selectedName: string,
  modelName: string | null,
  fallback?: string,
): string {
  if (selectedName) {
    const card = cards.find((c) => c.name === selectedName);
    if (card) return card.title || card.name;
  }
  return modelName || fallback || 'Select model';
}

export const SoloModelPicker: React.FC<SoloModelPickerProps> = ({
  cards,
  currentCardName,
  modelName,
  fallbackLabel,
  switching = false,
  disabled = false,
  onSelect,
  onAddModels,
}) => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const selectedName = resolveSelectedName(cards, currentCardName, modelName);
  const label = displayLabel(cards, selectedName, modelName, fallbackLabel);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? cards.filter((c) => {
          const hay = `${c.title || ''} ${c.name} ${c.model_name} ${c.provider || ''}`.toLowerCase();
          return hay.includes(q);
        })
      : cards;

    const out: { vendor: string; items: ModelCardInfo[] }[] = [];
    const idx: Record<string, number> = {};
    for (const c of filtered) {
      const v = c.provider?.trim() || '';
      if (v in idx) out[idx[v]].items.push(c);
      else {
        idx[v] = out.length;
        out.push({ vendor: v, items: [c] });
      }
    }
    return out;
  }, [cards, query]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(false);
        setQuery('');
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        setQuery('');
      }
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  useEffect(() => {
    if (open) {
      const t = window.setTimeout(() => searchRef.current?.focus(), 30);
      return () => window.clearTimeout(t);
    }
  }, [open]);

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        disabled={disabled || switching}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 max-w-[200px] sm:max-w-[240px] rounded-lg px-1.5 py-1 text-[12px] text-textMuted hover:text-textMain hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors disabled:opacity-50 disabled:cursor-not-allowed border-0 bg-transparent cursor-pointer"
        title={switching ? 'Switching model…' : 'Switch model'}
      >
        <span className="truncate font-medium">
          {switching ? 'Switching…' : label}
        </span>
        <ChevronDown
          size={13}
          className={`shrink-0 opacity-60 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div
          className="absolute bottom-[calc(100%+8px)] right-0 z-50 w-[min(300px,calc(100vw-2rem))] rounded-xl border border-border bg-white dark:bg-[#2a2a2c] shadow-[0_8px_30px_rgba(0,0,0,0.12)] overflow-hidden"
          role="listbox"
        >
          {/* Search */}
          <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border/70">
            <Search size={14} className="text-textMuted shrink-0" />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search models"
              className="flex-1 min-w-0 bg-transparent border-0 outline-none text-[13px] text-textMain placeholder:text-textMuted/60"
            />
          </div>

          {/* Model list */}
          <div className="max-h-[280px] overflow-y-auto py-1">
            {groups.length === 0 ? (
              <div className="px-3 py-4 text-[12px] text-textMuted text-center">No models found</div>
            ) : (
              groups.map((g) => (
                <div key={g.vendor || '__other'} className="py-0.5">
                  {g.vendor ? (
                    <div className="px-3 pt-1.5 pb-1 text-[10px] font-medium uppercase tracking-wide text-textMuted/55">
                      {g.vendor}
                    </div>
                  ) : null}
                  {g.items.map((card) => {
                    const selected = card.name === selectedName;
                    return (
                      <button
                        key={card.name}
                        type="button"
                        role="option"
                        aria-selected={selected}
                        onClick={() => {
                          if (card.name !== selectedName) onSelect(card.name);
                          setOpen(false);
                          setQuery('');
                        }}
                        className={`w-full flex items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors border-0 cursor-pointer ${
                          selected
                            ? 'bg-primary/10 text-primary'
                            : 'bg-transparent text-textMain hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'
                        }`}
                      >
                        <span className="flex-1 min-w-0 truncate font-medium">
                          {card.title || card.name}
                        </span>
                        {card.model_name && card.model_name !== (card.title || card.name) ? (
                          <span className="text-[11px] text-textMuted/55 truncate max-w-[40%] shrink-0">
                            {card.model_name}
                          </span>
                        ) : null}
                        {selected ? <Check size={14} className="shrink-0 text-primary" /> : null}
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>

          {/* Add Models */}
          <div className="border-t border-border/70">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setQuery('');
                onAddModels();
              }}
              className="w-full flex items-center gap-2 px-3 py-2.5 text-[13px] text-textMain hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors border-0 bg-transparent cursor-pointer"
            >
              <Plus size={14} className="text-textMuted" />
              <span className="font-medium">Add Models</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
