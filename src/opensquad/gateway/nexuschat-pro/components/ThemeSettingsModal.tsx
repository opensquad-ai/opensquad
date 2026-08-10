import React, { useEffect, useRef, useState } from 'react';
import { Check, Monitor, Moon, Sun } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  AppearanceMode,
  CONTRAST_MAX,
  CONTRAST_MIN,
  FONT_SIZE_MAX,
  FONT_SIZE_MIN,
  PRESET_METAS,
  PURITY_MAX,
  PURITY_MIN,
  ThemePrefs,
  ThemePresetId,
  formatContrastLabel,
  getPresetPrimary,
  hexToRgb,
  hslToHex,
  normalizeHex,
  randomPrimary,
  rgbToHsl,
} from '../utils/themeEngine';
import { loadThemePrefs, updateThemePrefs } from '../utils/themeStore';

const MODE_OPTIONS: { id: AppearanceMode; icon: React.ReactNode; labelKey: string }[] = [
  { id: 'light', icon: <Sun size={15} strokeWidth={1.75} />, labelKey: 'themeSettings.mode.light' },
  { id: 'dark', icon: <Moon size={15} strokeWidth={1.75} />, labelKey: 'themeSettings.mode.dark' },
  { id: 'system', icon: <Monitor size={15} strokeWidth={1.75} />, labelKey: 'themeSettings.mode.system' },
];

/** Compact color trigger + popover (reference: small circle swatch + hue field). */
function ColorPickerPopover({
  color,
  onChange,
  onClose,
}: {
  color: string;
  onChange: (hex: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const rgb = hexToRgb(color);
  const initial = rgbToHsl(rgb.r, rgb.g, rgb.b);
  const [hue, setHue] = useState(initial.h);
  const [sat, setSat] = useState(initial.s);
  const [lit, setLit] = useState(initial.l);
  const [hexInput, setHexInput] = useState(normalizeHex(color).toLowerCase());
  const panelRef = useRef<HTMLDivElement>(null);
  const skipEmit = useRef(false);

  useEffect(() => {
    if (skipEmit.current) {
      skipEmit.current = false;
      return;
    }
    const next = hslToHex(hue, sat, lit);
    setHexInput(next.toLowerCase());
    onChange(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hue, sat, lit]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [onClose]);

  const preview = hslToHex(hue, sat, lit);

  return (
    <div
      ref={panelRef}
      className="absolute left-0 top-[calc(100%+6px)] z-50 w-[260px] rounded-2xl border border-border bg-panel p-3 shadow-soft-lg"
    >
      <div
        className="relative mb-3 h-32 w-full cursor-crosshair overflow-hidden rounded-xl border border-border"
        style={{
          background: `
            linear-gradient(to top, #000, transparent),
            linear-gradient(to right, #fff, hsl(${hue}, 100%, 50%))
          `,
        }}
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const x = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
          const y = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height));
          setSat(x * 100);
          setLit((1 - y) * 90 + 5);
        }}
      >
        <span
          className="pointer-events-none absolute h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow-sm"
          style={{
            left: `${sat}%`,
            top: `${100 - ((lit - 5) / 90) * 100}%`,
            background: preview,
          }}
        />
      </div>

      <div className="mb-3 flex items-center gap-2">
        <span className="w-8 shrink-0 text-[11px] text-textMuted">{t('themeSettings.hue')}</span>
        <input
          type="range"
          min={0}
          max={360}
          value={hue}
          onChange={(e) => setHue(Number(e.target.value))}
          className="theme-slider min-w-0 flex-1"
          style={{
            background: 'linear-gradient(to right, #f00, #ff0, #0f0, #0ff, #00f, #f0f, #f00)',
          }}
          aria-label={t('themeSettings.hue')}
        />
        <span
          className="h-5 w-5 shrink-0 rounded-full border-2 border-white shadow-sm ring-1 ring-black/10"
          style={{ background: `hsl(${hue}, 100%, 50%)` }}
          aria-hidden
        />
      </div>

      <div className="flex items-center gap-2.5">
        <span
          className="h-8 w-8 shrink-0 rounded-full border border-black/10 shadow-sm"
          style={{ background: preview }}
          title={preview}
        />
        <input
          value={hexInput}
          onChange={(e) => {
            const v = e.target.value;
            setHexInput(v);
            if (/^#?[0-9a-fA-F]{6}$/.test(v.trim())) {
              const n = normalizeHex(v);
              const nr = hexToRgb(n);
              const hsl = rgbToHsl(nr.r, nr.g, nr.b);
              skipEmit.current = true;
              setHue(hsl.h);
              setSat(hsl.s);
              setLit(hsl.l);
              onChange(n);
            }
          }}
          className="flex-1 rounded-xl border border-border bg-bgLight px-2.5 py-1.5 font-mono text-xs text-textMain focus:outline-none focus:ring-2 focus:ring-primary/25"
        />
      </div>
    </div>
  );
}

/** Theme content for settings right pane (no outer modal chrome). */
export const ThemeSettingsPanel: React.FC = () => {
  const { t } = useTranslation();
  const [prefs, setPrefs] = useState<ThemePrefs>(() => loadThemePrefs());
  const [pickerOpen, setPickerOpen] = useState(false);

  useEffect(() => {
    setPrefs(loadThemePrefs());
  }, []);

  const patch = (partial: Partial<ThemePrefs>) => {
    setPrefs(updateThemePrefs(partial));
  };

  const selectPreset = (id: ThemePresetId) => {
    if (id === 'random') {
      patch({ preset: 'random', primary: randomPrimary() });
      return;
    }
    patch({ preset: id, primary: getPresetPrimary(id) });
  };

  const purityPct = ((prefs.purity - PURITY_MIN) / (PURITY_MAX - PURITY_MIN)) * 100;
  const contrastPct = ((prefs.contrast - CONTRAST_MIN) / (CONTRAST_MAX - CONTRAST_MIN)) * 100;
  const fontPct = ((prefs.fontSize - FONT_SIZE_MIN) / (FONT_SIZE_MAX - FONT_SIZE_MIN)) * 100;

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-2.5 text-xs font-medium text-textMuted">{t('themeSettings.appearanceMode')}</h3>
        <div className="flex gap-1 rounded-xl bg-bgLight p-1">
          {MODE_OPTIONS.map((opt) => {
            const active = prefs.mode === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => patch({ mode: opt.id })}
                className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-2.5 text-xs font-medium transition-all duration-soft ease-soft ${
                  active
                    ? 'bg-panel text-textMain shadow-soft'
                    : 'text-textMuted hover:text-textMain'
                }`}
              >
                {opt.icon}
                <span>{t(opt.labelKey)}</span>
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <h3 className="mb-2.5 text-xs font-medium text-textMuted">{t('themeSettings.colorThemes')}</h3>
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          {PRESET_METAS.map((preset) => {
            const active = prefs.preset === preset.id;
            const swatch =
              preset.id === 'random' && prefs.preset === 'random' ? prefs.primary : preset.primary;
            return (
              <button
                key={preset.id}
                type="button"
                onClick={() => selectPreset(preset.id)}
                className={`flex items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition-all duration-soft ease-soft ${
                  active
                    ? 'border-primary/35 bg-primary/[0.06]'
                    : 'border-transparent hover:bg-bgLight'
                }`}
              >
                <span className="relative mt-0.5 h-5 w-5 shrink-0">
                  {preset.id === 'random' ? (
                    <span
                      className="block h-5 w-5 rounded-full"
                      style={{
                        background: 'conic-gradient(#2D4739, #3D6B8A, #5C4A6E, #6B5A3E, #2D4739)',
                      }}
                    />
                  ) : (
                    <span
                      className="block h-5 w-5 rounded-full border border-black/10"
                      style={{ background: swatch }}
                    />
                  )}
                  {active && (
                    <span className="absolute inset-0 flex items-center justify-center rounded-full bg-black/25 text-white">
                      <Check size={11} strokeWidth={3} />
                    </span>
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-textMain">{t(preset.i18nNameKey)}</span>
                  <span className="mt-0.5 block text-[11px] leading-snug text-textMuted">
                    {t(preset.i18nDescKey)}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <h3 className="mb-2.5 text-xs font-medium text-textMuted">{t('themeSettings.customization')}</h3>
        <div className="space-y-3 rounded-2xl border border-border bg-bgLight/50 p-3.5">
          {/* Primary — pill with small circle swatch */}
          <div className="relative flex items-center gap-3">
            <span className="w-14 shrink-0 text-xs text-textMuted">{t('themeSettings.primary')}</span>
            <button
              type="button"
              onClick={() => setPickerOpen((v) => !v)}
              className="flex min-w-0 flex-1 items-center gap-2.5 rounded-full border border-border bg-panel px-2.5 py-1.5 text-left transition-colors duration-soft hover:border-primary/35"
            >
              <span
                className="h-5 w-5 shrink-0 rounded-full border border-black/10 shadow-sm"
                style={{ background: prefs.primary }}
                aria-hidden
              />
              <span className="truncate font-mono text-xs text-textMain">{prefs.primary}</span>
            </button>
            {pickerOpen && (
              <ColorPickerPopover
                color={prefs.primary}
                onChange={(hex) => patch({ primary: hex, preset: 'custom' })}
                onClose={() => setPickerOpen(false)}
              />
            )}
          </div>

          <div className="flex items-center gap-3">
            <span className="w-14 shrink-0 text-xs text-textMuted">{t('themeSettings.purity')}</span>
            <input
              type="range"
              min={PURITY_MIN}
              max={PURITY_MAX}
              value={prefs.purity}
              onChange={(e) =>
                patch({
                  purity: Number(e.target.value),
                  preset: prefs.preset === 'random' ? 'random' : 'custom',
                })
              }
              className="theme-slider flex-1"
              style={
                {
                  '--slider-pct': `${purityPct}%`,
                  '--slider-fill': 'rgb(var(--color-primary))',
                } as React.CSSProperties
              }
            />
            <span className="w-8 text-right font-mono text-xs text-textMuted">
              {Math.round(prefs.purity)}
            </span>
          </div>

          <div className="flex items-center gap-3">
            <span className="w-14 shrink-0 text-xs text-textMuted">{t('themeSettings.contrast')}</span>
            <input
              type="range"
              min={CONTRAST_MIN}
              max={CONTRAST_MAX}
              step={0.1}
              value={prefs.contrast}
              onChange={(e) =>
                patch({
                  contrast: Number(e.target.value),
                  preset: prefs.preset === 'random' ? 'random' : 'custom',
                })
              }
              className="theme-slider flex-1"
              style={
                {
                  '--slider-pct': `${contrastPct}%`,
                  '--slider-fill': 'rgb(var(--color-primary))',
                } as React.CSSProperties
              }
            />
            <span className="w-12 text-right font-mono text-xs text-textMuted">
              {formatContrastLabel(prefs.contrast)}
            </span>
          </div>
        </div>
      </section>

      <section>
        <div className="mb-2.5 flex items-center justify-between">
          <h3 className="text-xs font-medium text-textMuted">{t('themeSettings.chatFont')}</h3>
          <label className="flex cursor-pointer items-center gap-2 text-xs text-textMuted">
            <span>{t('themeSettings.serif')}</span>
            <button
              type="button"
              role="switch"
              aria-checked={prefs.serif}
              onClick={() => patch({ serif: !prefs.serif })}
              className={`relative h-5 w-9 rounded-full transition-colors duration-soft ${
                prefs.serif ? 'bg-primary' : 'bg-border'
              }`}
            >
              <span
                className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform duration-soft ${
                  prefs.serif ? 'translate-x-4' : 'translate-x-0'
                }`}
              />
            </button>
          </label>
        </div>
        <div className="mb-3 flex items-center gap-3">
          <span className="text-[11px] text-textMuted">{t('themeSettings.fontSmall')}</span>
          <input
            type="range"
            min={FONT_SIZE_MIN}
            max={FONT_SIZE_MAX}
            step={0.01}
            value={prefs.fontSize}
            onChange={(e) => patch({ fontSize: Number(e.target.value) })}
            className="theme-slider flex-1"
            style={
              {
                '--slider-pct': `${fontPct}%`,
                '--slider-fill': 'rgb(var(--color-primary))',
              } as React.CSSProperties
            }
          />
          <span className="text-[11px] text-textMuted">{t('themeSettings.fontLarge')}</span>
        </div>
        <div
          className="chat-font-surface rounded-xl border border-border bg-bgLight px-3.5 py-3 text-textMain"
          style={{
            fontSize: `calc(0.875rem * ${prefs.fontSize})`,
            fontFamily: prefs.serif
              ? '"Source Serif 4", "Noto Serif SC", Georgia, serif'
              : 'inherit',
            lineHeight: 1.55,
          }}
        >
          {t('themeSettings.fontPreview')}
        </div>
      </section>
    </div>
  );
};

/** Standalone modal kept for backwards-compat; prefer settings nav. */
export const ThemeSettingsModal: React.FC<{ isOpen: boolean; onClose: () => void }> = ({
  isOpen,
  onClose,
}) => {
  const { t } = useTranslation();
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 backdrop-blur-[2px] animate-in fade-in duration-200">
      <div className="os-modal-shell mx-4 flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden">
        <div className="os-modal-header shrink-0 rounded-t-[1rem]">
          <h2 className="text-base font-semibold text-textMain">{t('themeSettings.title')}</h2>
          <button type="button" onClick={onClose} className="os-icon-btn" aria-label={t('common.close')}>
            <span className="text-sm">×</span>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-5">
          <ThemeSettingsPanel />
        </div>
        <div className="flex shrink-0 justify-end border-t border-border px-5 py-3.5">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full bg-primary px-5 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
          >
            {t('themeSettings.done')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default ThemeSettingsModal;
