/**
 * MobileComposerMenu — narrow-viewport overflow for the AgentWebComposer toolbar.
 *
 * WHY THIS EXISTS
 * ---------------
 * The composer toolbar is a single non-wrapping row
 * (`[attach|file|mode]` … `[model|effort|voice|send]`), and both ends are
 * `shrink-0` — only the empty spacer in the middle can absorb anything. So the
 * row keeps its natural width (~404px) no matter how narrow the viewport gets,
 * and is clipped by `.os-composer-input-layer` (measured 286–396px). The last
 * control in the row — Send — therefore sat *outside* the screen on every
 * mainstream phone width, and because the clip is a plain overflow clip
 * (`documentElement.scrollWidth === innerWidth`) it could not be scrolled to:
 *
 *     viewport 430 → Send right edge 421  ✅
 *     viewport 393 → Send right edge 421  ❌ 28px out (32px button, 4px visible)
 *     viewport 390 → Send right edge 421  ❌ 31px out
 *     viewport 360 → Send right edge 421  ❌ 61px out
 *     viewport 320 → Send right edge 421  ❌ 101px out
 *
 * Reported as "手机访问 opensquad 无法发送 agent 会话消息" — the user could type
 * but had nothing to press.
 *
 * THE FIX (plan A)
 * ----------------
 * Pin Attach and Send, and fold the three *settings* pickers (mode / model /
 * effort) into this menu below the `md` breakpoint. `md` is not arbitrary: it is
 * the same 768px threshold the app already uses for its compact agent-web
 * layout (`useIsCompactAgentWeb` → `max-width: 767px`), so "the toolbar
 * collapses" and "the layout goes compact" flip at the same width.
 *
 * WHY A FLAT PANEL, NOT THE THREE PICKERS
 * ---------------------------------------
 * ModePicker / SoloModelPicker / EffortPicker each own an absolutely positioned
 * popover. Nesting those inside a fourth popover produced menus stacked on menus
 * that escaped the viewport, and each picker would still have been ~176–260px
 * wide before its own fly-out. Re-rendering the same option *data* (imported
 * from those modules, never copied) as one level keeps a single positioning
 * context and one scroll container.
 *
 * Mutations verified (each one makes utils/composerMobile.scan.test.ts fail):
 *   MR1 drop `md:hidden` on the root            → R3
 *   MR2 drop POPOVER_SURFACE_CLASS on the panel → R4
 *   MR3 drop the `MoreHorizontal` trigger       → R2
 */
import React, { useEffect, useRef, useState } from 'react';
import { Check, Compass, Hammer, MoreHorizontal, Plus } from 'lucide-react';
import type { ModelCardInfo } from '../../services/api';
import { MODES, type AgentMode } from './ModePicker';
import {
  DEEPSEEK_LEVELS,
  STANDARD_LEVELS,
  normalizeDeepseekEffort,
  type ReasoningEffort,
} from './EffortPicker';
import { POPOVER_SURFACE_CLASS, usePopMenuMounted } from './popoverSurface';

export interface MobileComposerMenuProps {
  disabled?: boolean;
  mode: AgentMode;
  onModeChange: (mode: AgentMode) => void;
  modelCards: ModelCardInfo[];
  currentCardName: string | null;
  modelName: string;
  switchingModel?: boolean;
  onSelectModel: (cardName: string) => void;
  /** Fired on open — the composer re-fetches cards so the list matches desktop. */
  onWillOpen?: () => void;
  onAddModels?: () => void;
  effort: ReasoningEffort;
  onEffortChange: (effort: ReasoningEffort) => void;
  /** Only true when the active card actually exposes a reasoning knob. */
  showEffort: boolean;
  deepseekStyle?: boolean;
  /** Placement classes from the composer — carries the `md:hidden` that
   *  retires this control on desktop, where the pickers are inline again. */
  className?: string;
}

export const MobileComposerMenu: React.FC<MobileComposerMenuProps> = ({
  disabled = false,
  mode,
  onModeChange,
  modelCards,
  currentCardName,
  modelName,
  switchingModel = false,
  onSelectModel,
  onWillOpen,
  onAddModels,
  effort,
  onEffortChange,
  showEffort,
  deepseekStyle = false,
  className,
}) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const mounted = usePopMenuMounted(open);

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

  // Same mapping the desktop EffortPicker uses: DeepSeek exposes only
  // high|max at the API, so the UI shows High / Max and normalises back.
  const levels = deepseekStyle ? DEEPSEEK_LEVELS : STANDARD_LEVELS;
  const activeEffort = deepseekStyle ? normalizeDeepseekEffort(effort) : effort;

  const rowCls = (selected: boolean) =>
    `w-full flex items-center gap-2 px-3 py-2 text-left text-[12px] transition-colors border-0 cursor-pointer ${
      selected
        ? 'bg-black/[0.06] dark:bg-white/[0.08] text-blue-600 dark:text-blue-400'
        : 'bg-transparent text-textMain hover:bg-black/[0.06] dark:hover:bg-white/[0.10]'
    }`;
  const sectionCls =
    'border-t border-border/70 px-3 pt-2.5 pb-1 text-[10px] font-medium uppercase tracking-wide text-textMuted/55';

  return (
    // Deliberately NOT `relative`: the panel must anchor to the composer card
    // (`.os-composer-input-layer`, the next positioned ancestor) instead of to
    // this 32px trigger. Anchored to the trigger, `right-0` put the panel's left
    // edge at -23px on a 320px screen — off the screen, since the trigger sits
    // ~46px in from the card's right edge (mic + gap) and the panel is 280px.
    <div ref={rootRef} className={`shrink-0 ${className ?? ''}`}>
      <button
        type="button"
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="更多设置"
        title="模式 / 模型 / 思考强度"
        onClick={() =>
          setOpen((v) => {
            const next = !v;
            if (next) onWillOpen?.();
            return next;
          })
        }
        className={`w-8 h-8 rounded-full flex items-center justify-center border-0 cursor-pointer transition-colors disabled:opacity-50 ${
          open
            ? 'bg-primary/20 text-primary'
            : 'text-textMuted hover:text-textMain hover:bg-primary/10 bg-transparent'
        }`}
      >
        <MoreHorizontal size={18} strokeWidth={1.75} />
      </button>

      {mounted ? (
        <div
          role="menu"
          aria-label="输入框设置"
          className={`absolute bottom-[calc(100%+8px)] right-2 z-[60] w-[min(280px,calc(100vw-3rem))] rounded-xl border border-border ${POPOVER_SURFACE_CLASS} overflow-hidden ${
            open ? 'os-pop-menu' : 'os-pop-menu-out'
          }`}
        >
          {/* Mode — same MODES the desktop ModePicker renders */}
          <div className="px-3 pt-2.5 pb-1 text-[10px] font-medium uppercase tracking-wide text-textMuted/55">
            模式
          </div>
          <div className="pb-1">
            {MODES.map((m) => {
              const selected = m.id === mode;
              const Icon = m.id === 'plan' ? Compass : Hammer;
              return (
                <button
                  key={m.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={selected}
                  title={m.hint}
                  onClick={() => {
                    if (!selected) onModeChange(m.id);
                  }}
                  className={rowCls(selected)}
                >
                  <span className="w-4 shrink-0 flex items-center justify-center">
                    {selected ? <Check size={13} /> : <Icon size={13} className="text-textMuted" />}
                  </span>
                  <span className="flex-1 min-w-0 truncate font-medium">{m.label}</span>
                  <span className="shrink-0 max-w-[112px] truncate text-[10px] text-textMuted/70">
                    {m.hint}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Model */}
          <div className={sectionCls}>模型</div>
          <div className="max-h-[216px] overflow-y-auto pb-1">
            {modelCards.length === 0 ? (
              <div className="px-3 py-3 text-[12px] text-textMuted">No models</div>
            ) : (
              modelCards.map((card) => {
                const selected =
                  (!!currentCardName && card.name === currentCardName) ||
                  (!currentCardName && !!modelName && card.model_name === modelName);
                return (
                  <button
                    key={card.name}
                    type="button"
                    role="menuitemradio"
                    aria-checked={selected}
                    title={card.title || card.name}
                    onClick={() => {
                      if (!selected) onSelectModel(card.name);
                      setOpen(false);
                    }}
                    className={rowCls(selected)}
                  >
                    <span className="w-4 shrink-0 flex items-center justify-center">
                      {selected ? <Check size={13} /> : null}
                    </span>
                    <span className="flex-1 min-w-0 truncate font-medium">
                      {card.title || card.name}
                    </span>
                    {card.provider ? (
                      <span className="shrink-0 max-w-[76px] truncate text-[10px] text-textMuted/70">
                        {card.provider}
                      </span>
                    ) : null}
                  </button>
                );
              })
            )}
          </div>
          <div className="border-t border-border/70">
            <button
              type="button"
              disabled={switchingModel}
              onClick={() => {
                setOpen(false);
                onAddModels?.();
              }}
              className={rowCls(false)}
            >
              <span className="w-4 shrink-0 flex items-center justify-center">
                <Plus size={13} className="text-textMuted" />
              </span>
              <span className="flex-1 min-w-0 truncate font-medium">Add Models</span>
            </button>
          </div>

          {/* Reasoning effort — chips, so all levels stay one tap away */}
          {showEffort ? (
            <>
              <div className={sectionCls}>思考强度</div>
              <div className="flex gap-1.5 px-3 pb-2.5">
                {levels.map((level) => {
                  const selected = level.id === activeEffort;
                  return (
                    <button
                      key={level.id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={selected}
                      onClick={() => {
                        if (!selected) onEffortChange(level.id);
                      }}
                      className={`flex-1 rounded-lg px-2 py-1.5 text-[12px] font-medium transition-colors border-0 cursor-pointer ${
                        selected
                          ? 'bg-primary/15 text-primary'
                          : 'bg-black/[0.05] dark:bg-white/[0.08] text-textMain hover:bg-primary/10'
                      }`}
                    >
                      {level.label}
                    </button>
                  );
                })}
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};
