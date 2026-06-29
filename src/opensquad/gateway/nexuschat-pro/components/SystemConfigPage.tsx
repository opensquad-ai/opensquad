import React, { useState, useEffect, useCallback, useRef, forwardRef, useImperativeHandle } from 'react';
import { X, Save, RefreshCw, ToggleLeft, ToggleRight, Plus, FolderOpen, ExternalLink, CheckCircle, AlertCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { systemConfigAPI, versionAPI } from '../services/api';
import { getSystemConfigCached, peekSystemConfig, setSystemConfigCache } from '../services/configCache';
import { APP_VERSION } from '../utils/appVersion';
import WorkspaceManager from './WorkspaceManager';

interface SystemConfigPageProps {
  isOpen: boolean;
  onClose: () => void;
}

type TabKey = 'workspace' | 'ports' | 'advanced' | 'about';

const TAB_LABELS: { key: TabKey; i18nKey: string }[] = [
  { key: 'about', i18nKey: 'systemConfig.tabs.about' },
  { key: 'workspace', i18nKey: 'systemConfig.tabs.workspace' },
  { key: 'ports', i18nKey: 'systemConfig.tabs.ports' },
  { key: 'advanced', i18nKey: 'systemConfig.tabs.advanced' },
];

const SETTINGS_TAB_KEY = 'opensquad_settings_tab';

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
}

const AboutTab: React.FC = () => {
  const { t } = useTranslation();
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

  const handleCheck = async () => {
    if (versionInfo.check_skipped) {
      // Server already told us the channel disables checks; don't even try.
      return;
    }
    setChecking(true);
    setError(null);
    try {
      const data = await versionAPI.check();
      const next: VersionInfoState = {
        current: data.current || APP_VERSION,
        channel: data.channel,
        latest: data.latest,
        url: data.url,
        update_available: data.update_available,
        check_skipped: data.check_skipped,
        skip_reason: data.skip_reason,
      };
      setVersionInfo(next);
      setUpdateChecked(true);
      sessionStorage.setItem(VERSION_CHECK_CACHE_KEY, JSON.stringify(next));
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
          disabled={checking || versionInfo.check_skipped}
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
              <div className="px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg">
                <div className="flex items-center gap-2 text-amber-700 font-medium text-sm mb-1">
                  <AlertCircle size={16} />
                  {t('systemConfig.about.newVersion', { version: versionInfo.latest })}
                </div>
                <p className="text-xs text-amber-600">{t('systemConfig.about.updateHint')}</p>
                {versionInfo.url && (
                  <a
                    href={versionInfo.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 inline-flex items-center gap-1 text-xs text-primary hover:underline"
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

export const SystemConfigPage: React.FC<SystemConfigPageProps> = ({ isOpen, onClose }) => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabKey>(() => {
    try {
      const saved = localStorage.getItem(SETTINGS_TAB_KEY);
      if (saved && ['about', 'workspace', 'ports', 'advanced'].includes(saved)) {
        return saved as TabKey;
      }
    } catch {}
    return 'about';
  });
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
    const cached = peekSystemConfig();
    if (cached) {
      setConfig(cached);
      void load({ silent: true, force: true });
      return;
    }
    void load();
  }, [isOpen, load]);

  // Persist active tab to localStorage
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

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 z-[100] flex items-center justify-center backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-panel rounded-2xl shadow-2xl w-full max-w-2xl mx-4 border border-border flex flex-col" style={{ height: '80vh', minHeight: '500px' }}>
        {/* Header */}
        <div className="bg-primary px-6 py-4 flex justify-between items-center text-white rounded-t-2xl shrink-0">
          <h3 className="font-semibold text-lg">{t('systemConfig.title')}</h3>
          <div className="flex items-center gap-2">
            <button onClick={() => void load({ force: true })} disabled={loading} className="p-1.5 hover:text-white/80 transition-colors" title={t('systemConfig.reload')}>
              <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
            </button>
            <button onClick={onClose} className="hover:text-white/80 transition-colors">
              <X size={20} />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-border bg-bgLight shrink-0">
          {TAB_LABELS.map(tab => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`px-5 py-3 text-sm font-medium transition-colors border-b-2 ${
                activeTab === tab.key
                  ? 'border-primary text-primary'
                  : 'border-transparent text-textMuted hover:text-textMain'
              }`}
            >
              {t(tab.i18nKey)}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:bg-border [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-track]:bg-transparent">
          {error && (
            <div className="mb-3 px-4 py-2 bg-red-50 border border-red-200 rounded-lg text-sm text-red-600">{error}</div>
          )}
          <div style={{ display: activeTab === 'about' ? 'block' : 'none' }}>
            <AboutTab />
          </div>
          {loading && activeTab !== 'about' && !config && (
            <div className="flex items-center justify-center h-40">
              <RefreshCw size={24} className="animate-spin text-primary" />
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
        <div className="px-5 py-4 border-t border-border flex justify-between items-center shrink-0 bg-bgLight rounded-b-2xl">
          {saved ? (
            <span className="text-sm text-green-600 font-medium">{t('systemConfig.saved')}</span>
          ) : (
            <span className="text-xs text-textMuted">{t('systemConfig.restartHint')}</span>
          )}
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-textMuted border border-border rounded-lg hover:bg-bgLight hover:text-textMain transition-colors"
            >
              {t('common.cancel')}
            </button>
            <button
              onClick={handleSave}
              disabled={saving || !config}
              className="px-5 py-2 bg-primary text-white text-sm rounded-lg font-semibold hover:opacity-90 transition-colors flex items-center gap-2 disabled:opacity-60"
            >
              {saving ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
              {t('common.save')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
