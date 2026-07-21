import React, { useState, useEffect, useCallback, useRef, forwardRef, useImperativeHandle } from 'react';
import {
  X, Save, RefreshCw, ToggleLeft, ToggleRight, Plus, FolderOpen, ExternalLink,
  CheckCircle, AlertCircle, Palette, Info, SlidersHorizontal, Cable, Settings2,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { systemConfigAPI, versionAPI } from '../services/api';
import { getSystemConfigCached, peekSystemConfig, setSystemConfigCache } from '../services/configCache';
import { APP_VERSION } from '../utils/appVersion';
import {
  beginDesktopUpdate,
  failDesktopUpdate,
} from '../services/desktopUpdateOverlay';
import WorkspaceManager from './WorkspaceManager';
import { ThemeSettingsPanel } from './ThemeSettingsModal';
import { SoftOverlay } from './SoftOverlay';
import {
  type WorkflowExpandLevel,
  useWorkflowExpandLevel,
} from '../utils/workflowExpandPref';

interface SystemConfigPageProps {
  isOpen: boolean;
  onClose: () => void;
  /** Optional tab to open when modal becomes visible */
  initialTab?: TabKey;
}

type TabKey = 'general' | 'theme' | 'workspace' | 'ports' | 'advanced' | 'about';

const NAV_ITEMS: { key: TabKey; i18nKey: string; icon: React.ReactNode }[] = [
  { key: 'general', i18nKey: 'systemConfig.tabs.general', icon: <Settings2 size={16} strokeWidth={1.75} /> },
  { key: 'theme', i18nKey: 'systemConfig.tabs.theme', icon: <Palette size={16} strokeWidth={1.75} /> },
  { key: 'workspace', i18nKey: 'systemConfig.tabs.workspace', icon: <FolderOpen size={16} strokeWidth={1.75} /> },
  { key: 'ports', i18nKey: 'systemConfig.tabs.ports', icon: <Cable size={16} strokeWidth={1.75} /> },
  { key: 'advanced', i18nKey: 'systemConfig.tabs.advanced', icon: <SlidersHorizontal size={16} strokeWidth={1.75} /> },
  { key: 'about', i18nKey: 'systemConfig.tabs.about', icon: <Info size={16} strokeWidth={1.75} /> },
];

const SETTINGS_TAB_KEY = 'opensquad_settings_tab';
const VALID_TABS = new Set<TabKey>(['general', 'theme', 'workspace', 'ports', 'advanced', 'about']);

function readSavedTab(): TabKey {
  try {
    const saved = localStorage.getItem(SETTINGS_TAB_KEY);
    if (saved && VALID_TABS.has(saved as TabKey)) return saved as TabKey;
  } catch {}
  return 'general';
}

// ---------- small reusable bits ----------

const Toggle: React.FC<{ value: boolean; onChange: (v: boolean) => void }> = ({ value, onChange }) => {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={() => onChange(!value)}
      className={`transition-colors ${value ? 'text-primary' : 'text-textMuted'}`}
      title={value ? t('common.enabled') : t('common.disabled')}
    >
      {value ? <ToggleRight size={28} /> : <ToggleLeft size={28} />}
    </button>
  );
};

const FieldRow: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="flex items-center gap-3 py-2 border-b border-border last:border-0">
    <span className="text-xs text-textMuted w-40 shrink-0">{label}</span>
    <div className="flex-1">{children}</div>
  </div>
);

const TextInput: React.FC<{
  value: string | number;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
}> = ({ value, onChange, type = 'text', placeholder }) => (
  <input
    type={type}
    value={value}
    onChange={e => onChange(e.target.value)}
    placeholder={placeholder}
    className="w-full px-3 py-1.5 bg-bgLight border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
  />
);

// ---------- Tab: General ----------

const WORKFLOW_EXPAND_OPTIONS: {
  id: WorkflowExpandLevel;
  titleKey: string;
  descKey: string;
}[] = [
  {
    id: 'collapsed',
    titleKey: 'systemConfig.general.workflowExpand.collapsed',
    descKey: 'systemConfig.general.workflowExpand.collapsedDesc',
  },
  {
    id: 'thoughts',
    titleKey: 'systemConfig.general.workflowExpand.thoughts',
    descKey: 'systemConfig.general.workflowExpand.thoughtsDesc',
  },
  {
    id: 'full',
    titleKey: 'systemConfig.general.workflowExpand.full',
    descKey: 'systemConfig.general.workflowExpand.fullDesc',
  },
];

const GeneralTab: React.FC = () => {
  const { t } = useTranslation();
  const [level, setLevel] = useWorkflowExpandLevel();

  return (
    <div className="space-y-6">
      <section>
        <h4 className="text-sm font-semibold text-textMain">
          {t('systemConfig.general.workflowExpand.title')}
        </h4>
        <p className="mt-1 text-xs leading-relaxed text-textMuted">
          {t('systemConfig.general.workflowExpand.hint')}
        </p>
        <div className="mt-3 space-y-2">
          {WORKFLOW_EXPAND_OPTIONS.map((opt) => {
            const active = level === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => setLevel(opt.id)}
                className={`flex w-full items-start gap-3 rounded-xl border px-3.5 py-3 text-left transition-all duration-soft ease-soft ${
                  active
                    ? 'border-primary/45 bg-primary/8 shadow-soft'
                    : 'border-border bg-bgLight/60 hover:border-border hover:bg-panel/70'
                }`}
              >
                <span
                  className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${
                    active ? 'border-primary' : 'border-border'
                  }`}
                  aria-hidden
                >
                  {active ? <span className="h-2 w-2 rounded-full bg-primary" /> : null}
                </span>
                <span className="min-w-0 flex-1">
                  <span className={`block text-sm font-medium ${active ? 'text-textMain' : 'text-textMain/90'}`}>
                    {t(opt.titleKey)}
                  </span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-textMuted">
                    {t(opt.descKey)}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
};

// ---------- Tab: Ports ----------

// Plugin-specific ports moved to plugin manager panel
const PLUGIN_PORT_KEYS = new Set(['whisper', 'websearch']);

const PortsTab: React.FC<{
  ports: Record<string, number>;
  hosts: Record<string, string>;
  onChange: (ports: Record<string, number>, hosts: Record<string, string>) => void;
}> = ({ ports, hosts, onChange }) => {
  const { t } = useTranslation();
  const allKeys = Array.from(new Set([...Object.keys(ports), ...Object.keys(hosts)]))
    .filter(k => !PLUGIN_PORT_KEYS.has(k));
  return (
    <div className="space-y-1">
      {allKeys.map(key => (
        <div key={key} className="px-4 py-3 rounded-xl bg-bgLight border border-border">
          <p className="text-sm font-semibold text-textMain mb-2 capitalize">{key}</p>
          <div className="grid grid-cols-2 gap-3">
            {ports[key] !== undefined && (
              <div>
                <label className="text-xs text-textMuted mb-1 block">{t('systemConfig.port')}</label>
                <TextInput
                  type="number"
                  value={ports[key]}
                  onChange={v => onChange({ ...ports, [key]: Number(v) }, hosts)}
                />
              </div>
            )}
            {hosts[key] !== undefined && (
              <div>
                <label className="text-xs text-textMuted mb-1 block">{t('systemConfig.bindAddress')}</label>
                <TextInput
                  value={hosts[key]}
                  onChange={v => onChange(ports, { ...hosts, [key]: v })}
                  placeholder="0.0.0.0"
                />
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};

// ---------- Tab: Advanced ----------

interface AdvancedTabHandle {
  flush: () => void;
}

const AdvancedTab = forwardRef<AdvancedTabHandle, {
  config: Record<string, any>;
  onChange: (patch: Record<string, any>) => void;
}>(({ config, onChange }, ref) => {
  const { t } = useTranslation();
  const logging = config.logging || {};
  const filesystem = config.filesystem || {};
  const workspaceDirs: string[] = filesystem.workspace_dirs || [];
  const [newDir, setNewDir] = useState('');

  const addDir = () => {
    const trimmed = newDir.trim();
    if (!trimmed || workspaceDirs.includes(trimmed)) return;
    onChange({ filesystem: { ...filesystem, workspace_dirs: [...workspaceDirs, trimmed] } });
    setNewDir('');
  };

  useImperativeHandle(ref, () => ({
    flush: () => {
      if (newDir.trim()) addDir();
    },
  }), [newDir, addDir]);

  const removeDir = (idx: number) => {
    onChange({ filesystem: { ...filesystem, workspace_dirs: workspaceDirs.filter((_, i) => i !== idx) } });
  };

  return (
    <div className="space-y-6">
      {/* Logging */}
      <div className="rounded-xl border border-border bg-bgLight px-4 pt-3 pb-1">
        <p className="text-xs font-bold text-textMuted uppercase mb-2">{t('systemConfig.advanced.logging')}</p>
        <FieldRow label={t('systemConfig.advanced.logDir')}>
          <TextInput value={logging.log_dir ?? ''} placeholder={t('systemConfig.advanced.logDirPlaceholder')} onChange={v => onChange({ logging: { ...logging, log_dir: v } })} />
        </FieldRow>
        <FieldRow label={t('systemConfig.advanced.maxFileSize')}>
          <TextInput type="number" value={logging.max_size_mb ?? 3} onChange={v => onChange({ logging: { ...logging, max_size_mb: Number(v) } })} />
        </FieldRow>
        <FieldRow label={t('systemConfig.advanced.backupCount')}>
          <TextInput type="number" value={logging.backup_count ?? 5} onChange={v => onChange({ logging: { ...logging, backup_count: Number(v) } })} />
        </FieldRow>
        <FieldRow label={t('systemConfig.advanced.logLevel')}>
          <div className="flex items-center gap-2">
            <select
              value={logging.log_level ?? 'INFO'}
              onChange={e => {
                const newLevel = e.target.value;
                onChange({ logging: { ...logging, log_level: newLevel } });
                systemConfigAPI.setLogLevel(newLevel).catch(() => {});
              }}
              className="flex-1 px-3 py-1.5 bg-bgLight border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
            >
              {['DEBUG', 'INFO', 'WARNING', 'ERROR'].map(l => <option key={l} value={l}>{l}</option>)}
            </select>
            <span className="text-xs text-green-600 whitespace-nowrap">{t('systemConfig.advanced.instantEffect')}</span>
          </div>
        </FieldRow>
        <FieldRow label="Tool Call Debug">
          <Toggle value={!!logging.tool_call_debug} onChange={v => onChange({ logging: { ...logging, tool_call_debug: v } })} />
        </FieldRow>
      </div>

      {/* Filesystem global workspace whitelist */}
      <div className="rounded-xl border border-border bg-bgLight px-4 pt-3 pb-4">
        <p className="text-xs font-bold text-textMuted uppercase mb-1">{t('systemConfig.advanced.fsWhitelist')}</p>
        <p className="text-xs text-textMuted mb-3 leading-relaxed">
          {t('systemConfig.advanced.fsWhitelistDesc')}
        </p>
        <div className="flex flex-col gap-1.5 mb-3">
          {workspaceDirs.length === 0 && (
            <p className="text-xs text-textMuted/60 italic py-1">{t('systemConfig.advanced.noExtraDirs')}</p>
          )}
          {workspaceDirs.map((dir, idx) => (
            <div key={idx} className="flex items-center gap-2 bg-panel border border-border rounded-lg px-3 py-2 group">
              <FolderOpen size={13} className="text-primary shrink-0" />
              <span className="flex-1 text-sm font-mono text-textMain truncate" title={dir}>{dir}</span>
              <button
                type="button"
                onClick={() => removeDir(idx)}
                className="opacity-0 group-hover:opacity-100 text-textMuted hover:text-red-500 transition-all shrink-0"
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            value={newDir}
            onChange={e => setNewDir(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addDir(); }}
            placeholder={t('systemConfig.advanced.addDirPlaceholder')}
            className="flex-1 px-3 py-1.5 bg-bgLight border border-border rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
          <button
            type="button"
            onClick={addDir}
            disabled={!newDir.trim()}
            className="px-3 py-1.5 bg-primary/10 text-primary rounded-lg hover:bg-primary/20 transition-colors disabled:opacity-40 flex items-center gap-1"
          >
            <Plus size={14} />
          </button>
        </div>
      </div>
    </div>
  );
});

// ---------- Tab: About ----------

const VERSION_CHECK_CACHE_KEY = 'opensquad_version_check_v1';

interface VersionInfoState {
  current: string;
  channel: 'stable' | 'dev' | 'pre-release' | 'local' | 'unknown';
  latest: string | null;
  url: string | null;
  update_available: boolean;
  check_skipped: boolean;
  skip_reason: string | null;
  download_url: string | null;
  download_name: string | null;
  download_size: number | null;
}

const AboutTab: React.FC = () => {
  const { t } = useTranslation();
  const isDesktopApp = Boolean(window.electronEnv?.isElectron) && !import.meta.env.DEV;
  const [updateBusy, setUpdateBusy] = useState(false);
  const [versionInfo, setVersionInfo] = useState<VersionInfoState>(() => {
    try {
      const cached = sessionStorage.getItem(VERSION_CHECK_CACHE_KEY);
      if (cached) {
        const parsed = JSON.parse(cached);
        return {
          current: APP_VERSION,
          channel: parsed.channel ?? 'unknown',
          latest: parsed.latest ?? null,
          url: parsed.url ?? null,
          update_available: Boolean(parsed.update_available),
          check_skipped: Boolean(parsed.check_skipped),
          skip_reason: parsed.skip_reason ?? null,
          download_url: parsed.download_url ?? null,
          download_name: parsed.download_name ?? null,
          download_size: parsed.download_size ?? null,
        };
      }
    } catch {}
    return {
      current: APP_VERSION,
      channel: 'unknown',
      latest: null,
      url: null,
      update_available: false,
      check_skipped: false,
      skip_reason: null,
      download_url: null,
      download_name: null,
      download_size: null,
    };
  });
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updateChecked, setUpdateChecked] = useState(() => {
    try {
      return Boolean(sessionStorage.getItem(VERSION_CHECK_CACHE_KEY));
    } catch {
      return false;
    }
  });

  const runDesktopUpdate = useCallback(async (downloadUrl: string, downloadName: string, version: string | null) => {
    const updater = window.electronEnv?.downloadAndInstallUpdate;
    if (!updater) {
      setError(t('systemConfig.about.desktopUpdateUnavailable'));
      return;
    }

    setError(null);
    setUpdateBusy(true);
    beginDesktopUpdate(version);

    try {
      const result = await updater({ url: downloadUrl, fileName: downloadName });
      if (!result.ok) {
        setError(result.error || t('systemConfig.about.desktopUpdateFailed'));
        failDesktopUpdate(result.error || t('systemConfig.about.desktopUpdateFailed'));
        setUpdateBusy(false);
      }
    } catch (e: any) {
      const message = e?.message || t('systemConfig.about.desktopUpdateFailed');
      setError(message);
      failDesktopUpdate(message);
      setUpdateBusy(false);
    }
  }, [t]);

  const promptAndInstallUpdate = useCallback(async (info: VersionInfoState) => {
    if (!info.download_url || !info.download_name) {
      setError(t('systemConfig.about.desktopUpdateAssetMissing'));
      return;
    }
    const confirmed = window.confirm(
      t('systemConfig.about.desktopUpdateConfirm', { version: info.latest ?? '' }),
    );
    if (!confirmed) return;
    await runDesktopUpdate(info.download_url, info.download_name, info.latest);
  }, [runDesktopUpdate, t]);

  const handleCheck = async () => {
    if (versionInfo.check_skipped) {
      return;
    }
    setChecking(true);
    setError(null);
    try {
      const platform = window.electronEnv?.platform;
      const arch = window.electronEnv?.arch;
      const data = await versionAPI.check(isDesktopApp ? platform : undefined, isDesktopApp ? arch : undefined);
      const next: VersionInfoState = {
        current: data.current || APP_VERSION,
        channel: data.channel,
        latest: data.latest,
        url: data.url,
        update_available: data.update_available,
        check_skipped: data.check_skipped,
        skip_reason: data.skip_reason,
        download_url: data.download_url ?? null,
        download_name: data.download_name ?? null,
        download_size: data.download_size ?? null,
      };
      setVersionInfo(next);
      setUpdateChecked(true);
      sessionStorage.setItem(VERSION_CHECK_CACHE_KEY, JSON.stringify(next));

      if (
        isDesktopApp &&
        next.update_available &&
        next.download_url &&
        next.download_name
      ) {
        await promptAndInstallUpdate(next);
      }
    } catch (e: any) {
      setError(e?.message || t('systemConfig.about.checkUpdateFailed'));
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Current Version */}
      <div className="rounded-xl border border-border bg-bgLight px-6 py-5">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center">
            <span className="text-2xl font-bold text-primary">OS</span>
          </div>
          <div>
            <h3 className="text-lg font-bold text-textMain">OpenSquad</h3>
            <p className="text-sm text-textMuted">Local-first Multi-Agent Collaboration Framework</p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-sm flex-wrap">
          <span className="text-textMuted">{t('systemConfig.about.currentVersion')}</span>
          <span className="font-mono font-semibold text-textMain px-2 py-0.5 bg-primary/10 rounded">
            v{APP_VERSION}
          </span>
          {versionInfo.channel && versionInfo.channel !== 'unknown' && (
            <span
              className={`text-xs font-medium px-2 py-0.5 rounded ${
                versionInfo.channel === 'stable'
                  ? 'bg-green-100 text-green-700'
                  : 'bg-blue-100 text-blue-700'
              }`}
              title={`Release channel: ${versionInfo.channel}`}
            >
              {t(`systemConfig.about.channel.${versionInfo.channel}`)}
            </span>
          )}
        </div>
      </div>

      {/* Update Check */}
      <div className="rounded-xl border border-border bg-bgLight px-6 py-5">
        <p className="text-xs font-bold text-textMuted uppercase mb-4">{t('systemConfig.about.versionCheck')}</p>
        <button
          onClick={handleCheck}
          disabled={checking || versionInfo.check_skipped || updateBusy}
          className="w-full py-2.5 bg-primary/10 text-primary rounded-lg font-medium hover:bg-primary/20 transition-colors flex items-center justify-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
        >
          <RefreshCw size={16} className={checking ? 'animate-spin' : ''} />
          {checking ? t('systemConfig.about.checking') : t('systemConfig.about.checkUpdate')}
        </button>

        {error && (
          <div className="mt-3 px-4 py-2 bg-red-50 border border-red-200 rounded-lg text-sm text-red-600 flex items-center gap-2">
            <AlertCircle size={16} />
            {error}
          </div>
        )}

        {versionInfo.check_skipped && (
          <div className="mt-3 px-4 py-3 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-800">
            <div className="font-medium mb-1">{t('systemConfig.about.checkSkippedTitle')}</div>
            <p className="text-xs text-blue-700">
              {versionInfo.skip_reason || t('systemConfig.about.checkSkippedGeneric')}
            </p>
          </div>
        )}

        {updateChecked && !checking && !error && !versionInfo.check_skipped && (
          <div className="mt-4 space-y-3">
            {versionInfo.update_available ? (
              <div className="px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg space-y-3">
                <div className="flex items-center gap-2 text-amber-700 font-medium text-sm">
                  <AlertCircle size={16} />
                  {t('systemConfig.about.newVersion', { version: versionInfo.latest })}
                </div>
                <p className="text-xs text-amber-600">{t('systemConfig.about.updateHint')}</p>
                {isDesktopApp && versionInfo.download_url && versionInfo.download_name && !updateBusy && (
                  <button
                    type="button"
                    onClick={() => promptAndInstallUpdate(versionInfo)}
                    className="w-full py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors"
                  >
                    {t('systemConfig.about.desktopUpdateNow')}
                  </button>
                )}
                {isDesktopApp && !versionInfo.download_url && (
                  <p className="text-xs text-amber-600">{t('systemConfig.about.desktopUpdateAssetMissing')}</p>
                )}
                {versionInfo.url && (
                  <a
                    href={versionInfo.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                  >
                    {t('systemConfig.about.viewRelease')} <ExternalLink size={12} />
                  </a>
                )}
              </div>
            ) : (
              <div className="px-4 py-3 bg-green-50 border border-green-200 rounded-lg flex items-center gap-2 text-green-700 text-sm">
                <CheckCircle size={16} />
                {t('systemConfig.about.alreadyLatest')}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Links */}
      <div className="rounded-xl border border-border bg-bgLight px-6 py-5">
        <p className="text-xs font-bold text-textMuted uppercase mb-3">{t('systemConfig.about.links')}</p>
        <div className="space-y-2">
          <a
            href="https://github.com/opensquad-ai/opensquad"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 text-sm text-textMuted hover:text-primary transition-colors"
          >
            <ExternalLink size={14} /> {t('systemConfig.about.github')}
          </a>
          <a
            href="https://github.com/opensquad-ai/opensquad/issues"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 text-sm text-textMuted hover:text-primary transition-colors"
          >
            <ExternalLink size={14} /> {t('systemConfig.about.issues')}
          </a>
          <a
            href="https://github.com/opensquad-ai/opensquad-plugins"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 text-sm text-textMuted hover:text-primary transition-colors"
          >
            <ExternalLink size={14} /> {t('systemConfig.about.pluginStore')}
          </a>
        </div>
      </div>
    </div>
  );
};

// ---------- Main Modal ----------

export const SystemConfigPage: React.FC<SystemConfigPageProps> = ({ isOpen, onClose, initialTab }) => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabKey>(() => initialTab && VALID_TABS.has(initialTab) ? initialTab : readSavedTab());
  const [config, setConfig] = useState<Record<string, any> | null>(() => peekSystemConfig());

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const advancedTabRef = useRef<AdvancedTabHandle>(null);

  const load = useCallback(async (opts?: { silent?: boolean; force?: boolean }) => {
    if (!opts?.silent) setLoading(true);
    setError(null);
    try {
      const sysConfig = await getSystemConfigCached(opts?.force);
      setConfig(sysConfig);
    } catch (e: any) {
      setError(e?.message || t('systemConfig.loadFailed'));
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (!isOpen) return;
    if (initialTab && VALID_TABS.has(initialTab)) {
      setActiveTab(initialTab);
    }
    const cached = peekSystemConfig();
    if (cached) {
      setConfig(cached);
      void load({ silent: true, force: true });
      return;
    }
    void load();
  }, [isOpen, initialTab, load]);

  useEffect(() => {
    try {
      localStorage.setItem(SETTINGS_TAB_KEY, activeTab);
    } catch {}
  }, [activeTab]);

  const patch = (updates: Record<string, any>) => {
    setConfig(prev => prev ? { ...prev, ...updates } : prev);
  };

  const handleSave = async () => {
    advancedTabRef.current?.flush();
    setSaving(true);
    setError(null);
    try {
      if (config) {
        await systemConfigAPI.update(config);
        setSystemConfigCache(config);
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      setError(e?.message || t('systemConfig.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const needsServerConfig = activeTab === 'workspace' || activeTab === 'ports' || activeTab === 'advanced';
  const showSave = needsServerConfig;

  return (
    <SoftOverlay
      open={isOpen}
      onBackdrop={onClose}
      zClass="z-[100]"
      className="backdrop-blur-[2px]"
    >
      <div
        className="os-modal-shell mx-4 flex w-full max-w-3xl flex-col overflow-hidden"
        style={{ height: 'min(82vh, 720px)', minHeight: '480px' }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
      >
        {/* Title row */}
        <div className="os-modal-header shrink-0 rounded-t-[1rem]">
          <h3 id="settings-title" className="text-base font-semibold tracking-tight text-textMain">
            {t('systemConfig.title')}
          </h3>
          <button type="button" onClick={onClose} className="os-icon-btn" aria-label={t('common.close')}>
            <X size={18} />
          </button>
        </div>

        {/* Body: left nav + right content */}
        <div className="flex min-h-0 flex-1">
          <nav className="flex w-[148px] shrink-0 flex-col gap-0.5 border-r border-border bg-bgLight/70 p-2 sm:w-[168px]">
            {NAV_ITEMS.map((item) => {
              const active = activeTab === item.key;
              return (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setActiveTab(item.key)}
                  className={`flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm transition-all duration-soft ease-soft ${
                    active
                      ? 'bg-panel text-textMain shadow-soft'
                      : 'text-textMuted hover:bg-panel/70 hover:text-textMain'
                  }`}
                >
                  <span className={active ? 'text-primary' : ''}>{item.icon}</span>
                  <span className="truncate font-medium">{t(item.i18nKey)}</span>
                </button>
              );
            })}
          </nav>

          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex-1 overflow-y-auto p-5 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-border">
              {error && (
                <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-600">
                  {error}
                </div>
              )}

              {activeTab === 'general' && <GeneralTab />}

              {activeTab === 'theme' && <ThemeSettingsPanel />}

              {activeTab === 'about' && <AboutTab />}

              {loading && needsServerConfig && !config && (
                <div className="flex h-40 items-center justify-center">
                  <RefreshCw size={22} className="animate-spin text-primary" />
                </div>
              )}

              {config && (
                <>
                  <div style={{ display: activeTab === 'workspace' ? 'block' : 'none' }}>
                    <WorkspaceManager />
                  </div>
                  <div style={{ display: activeTab === 'ports' ? 'block' : 'none' }}>
                    <PortsTab
                      ports={config.ports || {}}
                      hosts={config.hosts || {}}
                      onChange={(ports, hosts) => patch({ ports, hosts })}
                    />
                  </div>
                  <div style={{ display: activeTab === 'advanced' ? 'block' : 'none' }}>
                    <AdvancedTab ref={advancedTabRef} config={config} onChange={patch} />
                  </div>
                </>
              )}
            </div>

            {/* Footer */}
            <div className="flex shrink-0 items-center justify-between gap-3 border-t border-border bg-bgLight/50 px-5 py-3.5">
              <div className="min-w-0">
                {showSave ? (
                  saved ? (
                    <span className="text-sm font-medium text-green-600">{t('systemConfig.saved')}</span>
                  ) : (
                    <span className="text-xs text-textMuted">{t('systemConfig.restartHint')}</span>
                  )
                ) : (
                  <span className="text-xs text-textMuted">{t('systemConfig.liveHint')}</span>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {showSave && (
                  <button
                    type="button"
                    onClick={() => void load({ force: true })}
                    disabled={loading}
                    className="os-icon-btn"
                    title={t('systemConfig.reload')}
                  >
                    <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
                  </button>
                )}
                {showSave ? (
                  <>
                    <button
                      type="button"
                      onClick={onClose}
                      className="rounded-full border border-border px-4 py-2 text-sm text-textMuted transition-colors hover:bg-panel hover:text-textMain"
                    >
                      {t('common.cancel')}
                    </button>
                    <button
                      type="button"
                      onClick={handleSave}
                      disabled={saving || !config}
                      className="flex items-center gap-2 rounded-full bg-primary px-5 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-60"
                    >
                      {saving ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
                      {t('common.save')}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={onClose}
                    className="rounded-full bg-primary px-5 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
                  >
                    {t('themeSettings.done')}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </SoftOverlay>
  );
};
