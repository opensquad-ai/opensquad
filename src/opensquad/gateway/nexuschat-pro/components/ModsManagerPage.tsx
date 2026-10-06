import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronUp,
  FlaskConical,
  FolderInput,
  Menu,
  Package,
  RefreshCw,
  ToggleLeft,
  ToggleRight,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { modsAPI, ModGap, ModInfo, ModsHostStatus, ModsMatrix, ModVerdict } from '../services/api';
import {
  adminHeaderBar,
  adminHeaderCta,
  adminHeaderIcon,
  adminHeaderIconBox,
  adminHeaderNavBtn,
  adminHeaderTitle,
} from './admin/adminShellStyles';
import { OpenSquadLoader } from './OpenSquadLoader';

interface ModsManagerPageProps {
  onBack: () => void;
}

/** Verdict → badge classes. Deliberately three distinct colours plus a grey
 *  "unknown": "compatible" must never look like "cannot run". */
const VERDICT_STYLE: Record<ModVerdict, string> = {
  runnable: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  partial: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  blocked: 'bg-red-500/15 text-red-400 border-red-500/30',
  unknown: 'bg-slate-500/15 text-textMuted border-border',
};

const VERDICT_ORDER: ModVerdict[] = ['blocked', 'partial', 'unknown', 'runnable'];

const GapRow: React.FC<{ gap: ModGap }> = ({ gap }) => (
  <li className="flex items-start gap-2 text-xs">
    <span className="shrink-0 mt-0.5 px-1.5 py-0.5 rounded bg-bgLight border border-border text-[10px] uppercase tracking-wide text-textMuted">
      {gap.kind}
    </span>
    <span className="font-mono text-textMain shrink-0">{gap.name}</span>
    <span className="text-textMuted">{gap.why}</span>
  </li>
);

/** The one line that answers "so does it do anything?".
 *
 *  Loading is not effect: a mod can load and still never run (none of its events
 *  fire), or run and still be invisible (no render slot). Both look identical in
 *  a verdict, which is why this is computed server-side per mod. */
const EffectLine: React.FC<{ plan: ModInfo['plan'] }> = ({ plan }) => {
  const { t } = useTranslation();
  const effect = plan?.effect;
  if (!effect) return null;
  const served = [...effect.events, ...effect.dollar].join(' · ');

  const body =
    effect.kind === 'not_loadable' ? (
      <span className="text-textMuted">{t('mods.effectNotLoadable')}</span>
    ) : effect.kind === 'no_effect' ? (
      <span className="text-textMuted">{t('mods.effectNoEffect')}</span>
    ) : effect.kind === 'invisible' ? (
      <span className="text-amber-400">{t('mods.effectInvisible')}</span>
    ) : (
      <span className="text-emerald-400">{t('mods.effectWorks', { served })}</span>
    );

  return (
    <div className="text-xs">
      <span className="text-textMuted">{t('mods.effectLabel')}：</span>
      {body}
    </div>
  );
};

/** Grant/revoke the capabilities that leave the mod's own world. */
const PermissionsBlock: React.FC<{
  mod: ModInfo;
  capabilities: Array<{ id: string; blurb: string }>;
  onGrant: (mod: ModInfo, capability: string) => void;
  busy: boolean;
}> = ({ mod, capabilities, onGrant, busy }) => {
  const { t } = useTranslation();
  return (
    <div>
      <div className="text-[11px] font-bold uppercase tracking-wide text-textMuted mb-1">
        {t('mods.permissions')}
      </div>
      <ul className="space-y-1">
        {capabilities.map((cap) => (
          <li key={cap.id} className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              className="mt-0.5 shrink-0"
              checked={mod.permissions.granted.includes(cap.id)}
              disabled={busy}
              onChange={() => onGrant(mod, cap.id)}
              aria-label={cap.id}
            />
            <span className="font-mono text-textMain shrink-0">$.{cap.id}</span>
            <span className="text-textMuted">{cap.blurb}</span>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-[11px] text-amber-400">{t('mods.permissionsNotASandbox')}</p>
    </div>
  );
};

const ModCard: React.FC<{
  mod: ModInfo;
  capabilities: Array<{ id: string; blurb: string }>;
  onToggle: (mod: ModInfo) => void;
  onGrant: (mod: ModInfo, capability: string) => void;
  busy: boolean;
}> = ({ mod, capabilities, onToggle, onGrant, busy }) => {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(mod.verdict === 'blocked');
  const gaps = mod.blocked_by.length + mod.degraded_by.length;

  return (
    <div className={`bg-panel border rounded-xl overflow-hidden ${mod.verdict === 'blocked' ? 'border-red-500/25' : 'border-border'}`}>
      <div className="flex items-center gap-3 px-4 py-3">
        <Package size={18} className="text-primary shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-semibold text-textMain truncate">{mod.name}</span>
            {mod.version ? <span className="text-[11px] text-textMuted shrink-0">v{mod.version}</span> : null}
            {mod.has_catch ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-bgLight border border-border text-textMuted shrink-0">
                .catch
              </span>
            ) : null}
            {mod.plan && !mod.plan.loadable ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 border border-red-500/25 text-red-400 shrink-0">
                {t('mods.notLoadable')}
              </span>
            ) : null}
            {mod.plan && mod.plan.loadable && mod.plan.inert.length > 0 ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 border border-amber-500/25 text-amber-400 shrink-0">
                {t('mods.inertCount', { n: mod.plan.inert.length })}
              </span>
            ) : null}
          </div>
          {mod.description ? (
            <div className="text-xs text-textMuted truncate">{mod.description}</div>
          ) : null}
        </div>

        <span className={`shrink-0 text-[11px] px-2 py-0.5 rounded-full border ${VERDICT_STYLE[mod.verdict]}`}>
          {t(`mods.verdict.${mod.verdict}`)}
        </span>

        <button
          onClick={() => onToggle(mod)}
          disabled={busy}
          className={`shrink-0 transition-colors disabled:opacity-50 ${mod.state.enabled ? 'text-primary' : 'text-textMuted'}`}
          title={mod.state.enabled ? t('mods.disable') : t('mods.enable')}
        >
          {mod.state.enabled ? <ToggleRight size={22} /> : <ToggleLeft size={22} />}
        </button>
        <button
          onClick={() => setExpanded((p) => !p)}
          className="shrink-0 text-textMuted hover:text-textMain transition-colors"
          aria-label="details"
        >
          {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
        </button>
      </div>

      {!expanded ? (
        <div className="px-4 pb-3 space-y-1">
          <EffectLine plan={mod.plan} />
          <div className="text-xs text-textMuted">
            {t(`mods.verdictHint.${mod.verdict}`)}
            {gaps > 0 ? <span className="ml-2 text-textMain">{gaps}</span> : null}
          </div>
        </div>
      ) : (
        <div className="px-4 pb-4 pt-3 space-y-3 border-t border-border/50">
          <EffectLine plan={mod.plan} />
          <p className="text-xs text-textMuted">{t(`mods.verdictHint.${mod.verdict}`)}</p>

          {mod.blocked_by.length > 0 ? (
            <div>
              <div className="text-[11px] font-bold uppercase tracking-wide text-red-400 mb-1">
                {t('mods.gapBlocked')}
              </div>
              <ul className="space-y-1">
                {mod.blocked_by.map((g) => (
                  <GapRow key={`${g.kind}-${g.name}`} gap={g} />
                ))}
              </ul>
            </div>
          ) : null}

          {mod.degraded_by.length > 0 ? (
            <div>
              <div className="text-[11px] font-bold uppercase tracking-wide text-amber-400 mb-1">
                {t('mods.gapDegraded')}
              </div>
              <ul className="space-y-1">
                {mod.degraded_by.map((g) => (
                  <GapRow key={`${g.kind}-${g.name}`} gap={g} />
                ))}
              </ul>
            </div>
          ) : null}

          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
            {mod.used_events.length > 0 ? (
              <div>
                <span className="text-textMuted">{t('mods.usedEvents')}：</span>
                <span className="font-mono text-textMain">{mod.used_events.join(', ')}</span>
              </div>
            ) : null}
            {mod.used_dollar.length > 0 ? (
              <div>
                <span className="text-textMuted">{t('mods.usedDollar')}：</span>
                <span className="font-mono text-textMain">{mod.used_dollar.join(', ')}</span>
              </div>
            ) : null}
          </div>

          {mod.notes.length > 0 ? (
            <ul className="text-xs text-amber-400 space-y-1">
              {mod.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          ) : null}

          {capabilities.length > 0 ? (
            <PermissionsBlock mod={mod} capabilities={capabilities} onGrant={onGrant} busy={busy} />
          ) : null}

          <div className="flex flex-wrap items-center gap-2 text-[11px] text-textMuted">
            <span className="font-mono truncate">{mod.dir_name}</span>
            {mod.modules.length > 0 ? <span className="font-mono">{mod.modules.join(', ')}</span> : null}
            {mod.state.enabled ? <span className="text-primary">{t('mods.enabledIntent')}</span> : null}
          </div>
        </div>
      )}
    </div>
  );
};

const MatrixRow: React.FC<{ label: string; groups: Record<'served' | 'degraded' | 'refused', string[]> }> = ({
  label,
  groups,
}) => {
  const { t } = useTranslation();
  return (
    <div className="text-xs">
      <div className="text-textMuted mb-1">{label}</div>
      {(['served', 'degraded', 'refused'] as const).map((k) => (
        <div key={k} className="flex gap-2 mb-1">
          <span
            className={`shrink-0 w-12 text-[10px] px-1 py-0.5 text-center rounded border ${
              k === 'served'
                ? 'border-emerald-500/30 text-emerald-400'
                : k === 'degraded'
                  ? 'border-amber-500/30 text-amber-400'
                  : 'border-red-500/30 text-red-400'
            }`}
          >
            {t(`mods.${k}`)}
          </span>
          <span className="font-mono text-textMain break-all">{groups[k].join(' · ')}</span>
        </div>
      ))}
    </div>
  );
};

const MatrixPanel: React.FC<{ matrix: ModsMatrix; host: ModsHostStatus }> = ({ matrix, host }) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className="max-w-3xl mx-auto mt-6 bg-panel border border-border rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((p) => !p)}
        className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-bgLight/40 transition-colors"
      >
        <span className="text-sm font-semibold text-textMain flex-1">{t('mods.matrixTitle')}</span>
        <span className="text-[11px] text-textMuted">
          {matrix.events.served.length}+{matrix.events.degraded.length}+{matrix.events.refused.length}
        </span>
        {open ? <ChevronUp size={16} className="text-textMuted" /> : <ChevronDown size={16} className="text-textMuted" />}
      </button>
      {open ? (
        <div className="px-4 pb-4 space-y-4 border-t border-border/50 pt-3">
          <MatrixRow label={t('mods.matrixEvents')} groups={matrix.events} />
          <MatrixRow label={t('mods.matrixDollar')} groups={matrix.dollar} />
          <div className="text-xs space-y-1">
            <div>
              <span className="text-textMuted">{t('mods.matrixSlots')}：</span>
              <span className="font-mono text-textMain">{matrix.slots_open.join(' · ')}</span>
            </div>
            <div>
              <span className="text-textMuted">{t('mods.elementsOpen')}：</span>
              <span className="font-mono text-textMain">{matrix.elements_open.join(' · ')}</span>
            </div>
            <div>
              <span className="text-textMuted">{t('mods.matrixNeverOpen')}：</span>
              <span className="text-textMain">{matrix.slots_never}</span>
            </div>
          </div>
          <div className="text-[11px] text-textMuted">
            {t('mods.source', { source: matrix.source, upstream: matrix.upstream })}
          </div>
          <div className="text-[11px] text-textMuted">
            {host.node_runtime ? t('mods.nodeFound', { path: host.node_runtime }) : t('mods.nodeMissing')}
          </div>
        </div>
      ) : null}
    </div>
  );
};

export const ModsManagerPage: React.FC<ModsManagerPageProps> = ({ onBack }) => {
  const { t } = useTranslation();
  const [mods, setMods] = useState<ModInfo[]>([]);
  const [matrix, setMatrix] = useState<ModsMatrix | null>(null);
  const [host, setHost] = useState<ModsHostStatus | null>(null);
  const [capabilities, setCapabilities] = useState<Array<{ id: string; blurb: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [importPath, setImportPath] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await modsAPI.list();
      setMods(data.mods || []);
      setMatrix(data.matrix);
      setHost(data.host);
      // Capability blurbs come from the backend so the page cannot drift from
      // what the gates actually enforce.
      try {
        const perms = await modsAPI.permissions();
        setCapabilities(perms.capabilities || []);
      } catch {
        setCapabilities([]);
      }
    } catch (e: any) {
      setError(e?.message || t('mods.loadFail'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleToggle = useCallback(
    async (mod: ModInfo) => {
      const next = !mod.state.enabled;
      setBusy(true);
      try {
        const res = await modsAPI.setEnabled(mod.dir_name, next);
        setMods((prev) => prev.map((m) => (m.dir_name === mod.dir_name ? { ...m, state: res.state } : m)));
        if (res.host) setHost(res.host);
      } catch (e: any) {
        setError(e?.message || t('mods.loadFail'));
      } finally {
        setBusy(false);
      }
    },
    [t]
  );

  const handleGrant = useCallback(
    async (mod: ModInfo, capability: string) => {
      const held = mod.permissions?.granted || [];
      const next = held.includes(capability) ? held.filter((c) => c !== capability) : [...held, capability];
      setBusy(true);
      try {
        const res = await modsAPI.setPermissions(mod.dir_name, next, mod.permissions?.domains || []);
        setMods((prev) => prev.map((m) => (m.dir_name === mod.dir_name ? { ...m, permissions: res.permissions } : m)));
      } catch (e: any) {
        setError(e?.message || t('mods.loadFail'));
      } finally {
        setBusy(false);
      }
    },
    [t]
  );

  const handleImport = useCallback(async () => {
    const path = importPath.trim();
    if (!path) return;
    // A mod is arbitrary code with full process privileges: importing it *is*
    // running it, and the compatibility verdict says nothing about safety. One
    // explicit confirmation — this is the only place the choice is actually made.
    if (!window.confirm(t('mods.importConfirm'))) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await modsAPI.importMod(path);
      setNotice(t('mods.importOk', { name: res.name }));
      setImportPath('');
      await load();
    } catch (e: any) {
      setError(e?.message || t('mods.importFail'));
    } finally {
      setBusy(false);
    }
  }, [importPath, load, t]);

  const sorted = [...mods].sort(
    (a, b) => VERDICT_ORDER.indexOf(a.verdict) - VERDICT_ORDER.indexOf(b.verdict) || a.name.localeCompare(b.name)
  );

  return (
    <div className="flex flex-col h-full w-full bg-bgLight">
      <div className={`${adminHeaderBar} justify-between`}>
        <div className="flex items-center gap-2 md:gap-2.5">
          <button
            type="button"
            onClick={onBack}
            className={adminHeaderNavBtn}
            title={t('common.back', { defaultValue: 'Back' })}
            aria-label={t('common.back', { defaultValue: 'Back' })}
          >
            <ArrowLeft size={16} />
          </button>
          <button
            onClick={() => window.dispatchEvent(new CustomEvent('openMobileNav'))}
            className={`${adminHeaderNavBtn} md:hidden`}
            aria-label="Navigation menu"
          >
            <Menu size={16} />
          </button>
          <div className={`hidden md:flex ${adminHeaderIconBox}`}>
            <Package size={14} className={adminHeaderIcon} />
          </div>
          <div className="flex items-baseline gap-1.5 shrink-0">
            <h2 className={adminHeaderTitle}>{t('mods.title')}</h2>
            <span className="hidden md:inline text-xs text-textMuted">{t('mods.subtitle')}</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <input
            value={importPath}
            onChange={(e) => setImportPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleImport();
            }}
            placeholder={t('mods.importPlaceholder')}
            className="hidden md:block w-56 px-3 py-1.5 bg-bgLight border border-border rounded-lg text-xs font-mono focus:outline-none focus:ring-1 focus:ring-primary/50"
          />
          <button onClick={handleImport} disabled={busy || !importPath.trim()} className={adminHeaderCta}>
            <FolderInput size={13} /> {t('mods.import')}
          </button>
          <button onClick={() => void load()} disabled={loading} className={adminHeaderCta}>
            {loading ? <OpenSquadLoader size={16} /> : <RefreshCw size={13} />} {t('mods.rescan')}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        {error ? (
          <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-sm mb-4 max-w-3xl mx-auto">
            <AlertCircle size={16} /> {error}
          </div>
        ) : null}
        {notice ? (
          <div className="flex items-center gap-2 p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-emerald-400 text-sm mb-4 max-w-3xl mx-auto">
            <Check size={16} /> {notice}
          </div>
        ) : null}

        {/* Always visible: the page must not read as "mods work here" just because
            a verdict says runnable. What is actually wired is spelled out. */}
        <div className="flex items-start gap-2 p-3 bg-indigo-500/10 border border-indigo-500/20 rounded-xl text-indigo-300 text-xs mb-4 max-w-3xl mx-auto">
          <FlaskConical size={16} className="shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold">{t('mods.experimentalTitle')}</div>
            <div className="text-indigo-300/80">{t('mods.experimentalBody')}</div>
            {host?.scope ? (
              <div className="mt-1 font-mono text-[11px] text-indigo-300/70">
                {t('mods.hostScope')}: {host.scope}
              </div>
            ) : null}
            {host?.observed && host.observed.degraded > 0 ? (
              <div className="mt-1 text-amber-400">
                {t('mods.hostDegraded', { n: host.observed.degraded })}
                {host.last_failure ? <span className="block font-mono text-[11px]">{host.last_failure}</span> : null}
              </div>
            ) : null}
            {host?.observed && host.observed.degraded === 0 && host.observed.running > 0 ? (
              <div className="mt-1 text-indigo-300/70">
                {t('mods.hostRunning', { n: host.observed.running })}
              </div>
            ) : null}
            {host?.observed && host.observed.stale > 0 ? (
              <div className="mt-1 text-indigo-300/50">{t('mods.hostStale', { n: host.observed.stale })}</div>
            ) : null}
          </div>
        </div>

        {host && !host.available ? (
          <div className="flex items-start gap-2 p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-amber-400 text-xs mb-4 max-w-3xl mx-auto">
            <AlertCircle size={16} className="shrink-0 mt-0.5" />
            <div>
              <div className="font-semibold">{t('mods.hostUnavailable')}</div>
              {host.reason ? <div className="text-amber-400/80">{host.reason}</div> : null}
              {!host.node_runtime ? <div className="text-amber-400/80">{t('mods.nodeMissing')}</div> : null}
            </div>
          </div>
        ) : null}

        {loading ? (
          <div className="flex items-center justify-center py-16 text-textMuted">
            <OpenSquadLoader size={32} />
          </div>
        ) : sorted.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-textMuted text-center max-w-md mx-auto">
            <Package size={40} className="mb-3 opacity-30" />
            <p className="mb-2">{t('mods.empty')}</p>
            <p className="text-xs">{t('mods.emptyHint')}</p>
          </div>
        ) : (
          <div className="space-y-3 max-w-3xl mx-auto">
            {sorted.map((m) => (
              <ModCard
                key={m.dir_name}
                mod={m}
                capabilities={capabilities}
                onToggle={handleToggle}
                onGrant={handleGrant}
                busy={busy}
              />
            ))}
          </div>
        )}

        {matrix && host ? <MatrixPanel matrix={matrix} host={host} /> : null}
      </div>
    </div>
  );
};
