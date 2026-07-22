/**
 * SoloModelPicker — two-level model selector (Provider → Model).
 * Trigger is a grey pill; open menu shows providers, with a flyout of models
 * vertically aligned to the active provider row.
 */
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Circle, Plus, Search } from 'lucide-react';
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
    if (card) return card.title || card.model_name || card.name;
  }
  return modelName || fallback || 'Select model';
}

function vendorKey(card: ModelCardInfo): string {
  return card.provider?.trim() || '';
}

function vendorLabel(key: string): string {
  return key || 'Other';
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
  const [activeVendor, setActiveVendor] = useState<string | null>(null);
  const [flyoutTop, setFlyoutTop] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const flyoutRef = useRef<HTMLDivElement>(null);
  const vendorListRef = useRef<HTMLDivElement>(null);
  const vendorBtnRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const searchRef = useRef<HTMLInputElement>(null);

  const selectedName = resolveSelectedName(cards, currentCardName, modelName);
  const label = displayLabel(cards, selectedName, modelName, fallbackLabel);
  const selectedCard = cards.find((c) => c.name === selectedName) || null;
  const selectedVendor = selectedCard ? vendorKey(selectedCard) : null;

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
      const v = vendorKey(c);
      if (v in idx) out[idx[v]].items.push(c);
      else {
        idx[v] = out.length;
        out.push({ vendor: v, items: [c] });
      }
    }
    return out;
  }, [cards, query]);

  const activeGroup = useMemo(() => {
    if (activeVendor == null) return null;
    return groups.find((g) => g.vendor === activeVendor) || null;
  }, [groups, activeVendor]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(false);
        setQuery('');
        setActiveVendor(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        setQuery('');
        setActiveVendor(null);
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
    if (!open) return;
    // Prefer current selection's vendor; else first group.
    const initial =
      selectedVendor != null && groups.some((g) => g.vendor === selectedVendor)
        ? selectedVendor
        : groups[0]?.vendor ?? null;
    setActiveVendor(initial);
    const t = window.setTimeout(() => searchRef.current?.focus(), 30);
    return () => window.clearTimeout(t);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps -- only on open

  useEffect(() => {
    if (!open) return;
    if (activeVendor != null && groups.some((g) => g.vendor === activeVendor)) return;
    setActiveVendor(groups[0]?.vendor ?? null);
  }, [open, groups, activeVendor]);

  // Keep L2 flyout vertically aligned with the active provider row.
  useLayoutEffect(() => {
    if (!open || activeVendor == null) return;

    const sync = () => {
      const menu = menuRef.current;
      const btn = vendorBtnRefs.current[activeVendor];
      if (!menu || !btn) return;

      const menuRect = menu.getBoundingClientRect();
      const btnRect = btn.getBoundingClientRect();
      let top = btnRect.top - menuRect.top;

      const flyout = flyoutRef.current;
      if (flyout) {
        const flyoutH = flyout.offsetHeight;
        const maxTop = Math.max(0, menuRect.height - flyoutH);
        top = Math.max(0, Math.min(top, maxTop));
      } else {
        top = Math.max(0, top);
      }
      setFlyoutTop(top);
    };

    sync();
    // Second pass after flyout mounts / height settles.
    const raf = window.requestAnimationFrame(sync);
    const list = vendorListRef.current;
    list?.addEventListener('scroll', sync, { passive: true });
    window.addEventListener('resize', sync);
    return () => {
      window.cancelAnimationFrame(raf);
      list?.removeEventListener('scroll', sync);
      window.removeEventListener('resize', sync);
    };
  }, [open, activeVendor, activeGroup, groups, query]);

  const close = () => {
    setOpen(false);
    setQuery('');
    setActiveVendor(null);
  };

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        disabled={disabled || switching}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 max-w-[220px] sm:max-w-[280px] rounded-full px-2.5 py-1.5 text-[12px] text-textMain bg-black/[0.05] dark:bg-white/[0.08] hover:bg-primary/15 transition-colors disabled:opacity-50 disabled:cursor-not-allowed border-0 cursor-pointer"
        title={switching ? 'Applying model…' : 'Switch model'}
      >
        <Circle size={10} className="shrink-0 text-textMuted fill-textMuted/30" strokeWidth={1.5} />
        <span className="truncate font-medium">
          {label}
        </span>
        <ChevronDown
          size={13}
          className={`shrink-0 opacity-55 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div
          ref={menuRef}
          className="absolute bottom-[calc(100%+8px)] right-0 z-50"
          role="listbox"
        >
          {/* Level 2: models — anchored beside the active provider row */}
          {activeGroup && (
            <div
              ref={flyoutRef}
              className="absolute right-full mr-1.5 w-[min(260px,calc(100vw-8rem))] rounded-xl border border-border bg-white dark:bg-[#2a2a2c] shadow-[0_8px_30px_rgba(0,0,0,0.12)] overflow-hidden"
              style={{ top: flyoutTop }}
            >
              <div className="max-h-[300px] overflow-y-auto py-1">
                {activeGroup.items.length === 0 ? (
                  <div className="px-3 py-4 text-[12px] text-textMuted text-center">No models</div>
                ) : (
                  activeGroup.items.map((card) => {
                    const selected = card.name === selectedName;
                    return (
                      <button
                        key={card.name}
                        type="button"
                        role="option"
                        aria-selected={selected}
                        onClick={() => {
                          // Always fire — re-clicking the optimistic label must
                          // re-send switch_model if the prior command was dropped.
                          onSelect(card.name);
                          close();
                        }}
                        className={`w-full flex items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors border-0 cursor-pointer ${
                          selected
                            ? 'bg-black/[0.06] dark:bg-white/[0.08] text-textMain'
                            : 'bg-transparent text-textMain hover:bg-primary/10'
                        }`}
                      >
                        <span className="w-4 shrink-0 flex items-center justify-center">
                          {selected ? <Check size={14} className="text-textMain" /> : null}
                        </span>
                        <span className="flex-1 min-w-0 truncate font-medium">
                          {card.title || card.model_name || card.name}
                        </span>
                      </button>
                    );
                  })
                )}
              </div>
            </div>
          )}

          {/* Level 1: providers */}
          <div className="w-[min(220px,calc(100vw-3rem))] rounded-xl border border-border bg-white dark:bg-[#2a2a2c] shadow-[0_8px_30px_rgba(0,0,0,0.12)] overflow-hidden">
            <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border/70">
              <Search size={14} className="text-textMuted shrink-0" />
              <input
                ref={searchRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索供应商 / 模型"
                className="flex-1 min-w-0 bg-transparent border-0 outline-none text-[13px] text-textMain placeholder:text-textMuted/60"
              />
            </div>

            <div ref={vendorListRef} className="max-h-[260px] overflow-y-auto py-1">
              {groups.length === 0 ? (
                <div className="px-3 py-4 text-[12px] text-textMuted text-center">未找到模型</div>
              ) : (
                groups.map((g) => {
                  const active = g.vendor === activeVendor;
                  const isCurrent = selectedVendor === g.vendor;
                  const refKey = g.vendor || '__other';
                  return (
                    <button
                      key={refKey}
                      type="button"
                      ref={(el) => {
                        vendorBtnRefs.current[g.vendor] = el;
                      }}
                      onMouseEnter={() => setActiveVendor(g.vendor)}
                      onFocus={() => setActiveVendor(g.vendor)}
                      onClick={() => setActiveVendor(g.vendor)}
                      className={`w-full flex items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors border-0 cursor-pointer ${
                        active
                          ? 'bg-black/[0.06] dark:bg-white/[0.08] text-textMain'
                          : 'bg-transparent text-textMain hover:bg-primary/10'
                      }`}
                    >
                      <span className="w-4 shrink-0 flex items-center justify-center">
                        {isCurrent ? <Check size={14} className="text-textMain" /> : null}
                      </span>
                      <span className="flex-1 min-w-0 truncate font-medium">
                        {vendorLabel(g.vendor)}
                      </span>
                      <ChevronRight size={14} className="shrink-0 text-textMuted/50" />
                    </button>
                  );
                })
              )}
            </div>

            <div className="border-t border-border/70">
              <button
                type="button"
                onClick={() => {
                  close();
                  onAddModels();
                }}
                className="w-full flex items-center gap-2 px-3 py-2.5 text-[13px] text-textMain hover:bg-primary/10 transition-colors border-0 bg-transparent cursor-pointer"
              >
                <Plus size={14} className="text-textMuted" />
                <span className="font-medium">Add Models</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
