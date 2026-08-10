/**
 * SoloModelPicker — two-level model selector (Provider → Model).
 * Trigger is a grey pill; open menu shows providers, with a flyout of models
 * vertically aligned to the active provider row.
 *
 * In chat the menu opens upward (composer sits at the bottom). In forms
 * (e.g. scheduled-task editor) pass placement="down" so the menu opens below
 * and is portaled to document.body — avoiding overflow:auto clipping.
 */
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown, ChevronRight, Circle, Plus, Search } from 'lucide-react';
import type { ModelCardInfo } from '../../services/api';

export type SoloModelPickerPlacement = 'up' | 'down';

interface SoloModelPickerProps {
  cards: ModelCardInfo[];
  currentCardName: string | null;
  modelName: string | null;
  fallbackLabel?: string;
  switching?: boolean;
  disabled?: boolean;
  onSelect: (cardName: string) => void;
  onAddModels: () => void;
  /** Refresh card list when the menu opens (desktop may have added cards). */
  onWillOpen?: () => void;
  /**
   * `up` — above the trigger (chat composer, default).
   * `down` — below the trigger (forms / scroll panes).
   */
  placement?: SoloModelPickerPlacement;
  /**
   * When true (default for placement="down"), render the menu via portal so
   * parent overflow:auto does not clip it.
   */
  usePortal?: boolean;
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

type MenuPos = {
  top: number;
  left: number;
  /** Provider panel opens to the right of trigger when true. */
  openRight: boolean;
  /** Flyout (models) opens to the right of the provider panel. */
  flyoutRight: boolean;
};

export const SoloModelPicker: React.FC<SoloModelPickerProps> = ({
  cards,
  currentCardName,
  modelName,
  fallbackLabel,
  switching = false,
  disabled = false,
  onSelect,
  onAddModels,
  onWillOpen,
  placement = 'up',
  usePortal,
}) => {
  const portal = usePortal ?? placement === 'down';
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeVendor, setActiveVendor] = useState<string | null>(null);
  const [flyoutTop, setFlyoutTop] = useState(0);
  const [menuPos, setMenuPos] = useState<MenuPos | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
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
    // Hide models the user disabled in the model-card manager.
    const visible = cards.filter((c) => c.enabled !== false);
    const filtered = q
      ? visible.filter((c) => {
          const hay = `${c.title || ''} ${c.name} ${c.model_name} ${c.provider || ''}`.toLowerCase();
          return hay.includes(q);
        })
      : visible;

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

  const computeMenuPos = (): MenuPos | null => {
    const trigger = triggerRef.current;
    if (!trigger) return null;
    const rect = trigger.getBoundingClientRect();
    const gap = 8;
    const panelW = 220;
    const flyoutW = 260;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // Prefer aligning panel to trigger's left in forms; to trigger's right in chat.
    let openRight = placement === 'down';
    let left = openRight ? rect.left : rect.right - panelW;
    // Keep panel on-screen.
    if (left + panelW > vw - 8) left = Math.max(8, vw - panelW - 8);
    if (left < 8) left = 8;

    // Flyout: prefer opposite side of crowded edge.
    const spaceRight = vw - (left + panelW);
    const spaceLeft = left;
    let flyoutRight = placement === 'down' ? spaceRight >= flyoutW + 8 : spaceLeft < flyoutW + 8;
    if (placement === 'up') {
      // Chat default: flyout to the left of the provider panel.
      flyoutRight = spaceLeft < flyoutW + 8 && spaceRight >= flyoutW + 8;
    }

    let top: number;
    if (placement === 'down') {
      top = rect.bottom + gap;
      // If not enough room below, flip above.
      if (top + 320 > vh - 8 && rect.top > vh - rect.bottom) {
        top = Math.max(8, rect.top - gap - 320);
      }
    } else {
      top = rect.top - gap;
      // Absolute up-anchor handled via transform when not portaled; for portal
      // we measure after mount. Use a provisional top and clamp after layout.
      top = Math.max(8, rect.top - 320 - gap);
    }

    return { top, left, openRight, flyoutRight };
  };

  useLayoutEffect(() => {
    if (!open) {
      setMenuPos(null);
      return;
    }
    const sync = () => {
      const pos = computeMenuPos();
      if (!pos) return;
      // Refine vertical position after menu has real height (portal / up).
      const menu = menuRef.current;
      const trigger = triggerRef.current;
      if (menu && trigger) {
        const mH = menu.offsetHeight || 280;
        const rect = trigger.getBoundingClientRect();
        const gap = 8;
        const vh = window.innerHeight;
        if (placement === 'up') {
          pos.top = Math.max(8, rect.top - gap - mH);
        } else {
          let top = rect.bottom + gap;
          if (top + mH > vh - 8 && rect.top - gap - mH >= 8) {
            top = rect.top - gap - mH;
          }
          pos.top = Math.min(top, Math.max(8, vh - mH - 8));
        }
      }
      setMenuPos(pos);
    };
    sync();
    const raf = window.requestAnimationFrame(sync);
    window.addEventListener('resize', sync);
    window.addEventListener('scroll', sync, true);
    return () => {
      window.cancelAnimationFrame(raf);
      window.removeEventListener('resize', sync);
      window.removeEventListener('scroll', sync, true);
    };
  }, [open, placement, groups.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (rootRef.current?.contains(t)) return;
      if (menuRef.current?.contains(t)) return;
      setOpen(false);
      setQuery('');
      setActiveVendor(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        setQuery('');
        setActiveVendor(null);
      }
    };
    document.addEventListener('mousedown', onDoc, true);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc, true);
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
  }, [open, activeVendor, activeGroup, groups, query, menuPos]);

  const close = () => {
    setOpen(false);
    setQuery('');
    setActiveVendor(null);
  };

  const flyoutRight = menuPos?.flyoutRight ?? placement === 'down';

  const menuBody = open ? (
    <div
      ref={menuRef}
      className={
        portal
          ? 'fixed z-[200]'
          : placement === 'down'
            ? 'absolute top-[calc(100%+8px)] left-0 z-50'
            : 'absolute bottom-[calc(100%+8px)] right-0 z-50'
      }
      style={
        portal && menuPos
          ? { top: menuPos.top, left: menuPos.left }
          : portal
            ? { visibility: 'hidden' as const }
            : undefined
      }
      role="listbox"
    >
      {/* Level 2: models — anchored beside the active provider row */}
      {activeGroup && (
        <div
          ref={flyoutRef}
          className={`absolute w-[min(260px,calc(100vw-8rem))] rounded-xl border border-border bg-bgLight shadow-[0_8px_30px_rgba(0,0,0,0.12)] overflow-hidden ${
            flyoutRight ? 'left-full ml-1.5' : 'right-full mr-1.5'
          }`}
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
                        : 'bg-transparent text-textMain hover:bg-black/[0.06] dark:hover:bg-white/[0.10]'
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
      <div className="w-[min(220px,calc(100vw-3rem))] rounded-xl border border-border bg-bgLight shadow-[0_8px_30px_rgba(0,0,0,0.12)] overflow-hidden">
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
                      : 'bg-transparent text-textMain hover:bg-black/[0.06] dark:hover:bg-white/[0.10]'
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
            className="w-full flex items-center gap-2 px-3 py-2.5 text-[13px] text-textMain hover:bg-black/[0.06] dark:hover:bg-white/[0.10] transition-colors border-0 bg-transparent cursor-pointer"
          >
            <Plus size={14} className="text-textMuted" />
            <span className="font-medium">Add Models</span>
          </button>
        </div>
      </div>
    </div>
  ) : null;

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled || switching}
        onClick={() => {
          setOpen((v) => {
            const next = !v;
            if (next) onWillOpen?.();
            return next;
          });
        }}
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

      {portal && typeof document !== 'undefined'
        ? menuBody && createPortal(menuBody, document.body)
        : menuBody}
    </div>
  );
};
